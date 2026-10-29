"""Rehearse/apply the additive SQL-assistant upgrade on local Docker Desktop.

An application rollback keeps the compatible additive schema. Full-dump recovery
is exercised on an isolated PostgreSQL; it never discards new live application data.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import yaml

from deployment.blue_green import BlueGreen, deployment_lock, write_json
from tools.quality.artifact_lifecycle import Run, docker_args

RECIPE = "sql-assistant-base-v1"
TABLES = {"sql_assistant_session", "sql_assistant_command", "sql_assistant_definition", "sql_assistant_definition_version"}
CHECKS = {"restore", "atomic_failure", "malformed_schema", "repeat", "existing_data", "previous_binary", "new_orm"}
PROBE = Path(__file__).with_name("sql_assistant_probe.py")


def checksum(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def call(*args: str, data: bytes | None = None, timeout=180) -> str:
    result = subprocess.run(docker_args(args), input=data, capture_output=True, timeout=timeout)
    if result.returncode:
        # Docker/Compose/configuration output may contain credentials.
        error = result.stderr.decode(errors="replace")
        error = re.sub(r"(?i)(password|passwd|token|secret|api_key)([\"\s:=]+)[^,\s\"]+", r"\1\2<redacted>", error)
        error = re.sub(r"://[^/@\s]+:[^/@\s]+@", "://<redacted>@", error)
        raise RuntimeError(f"{args[0]} {args[1]} failed (exit {result.returncode}): {error[-1200:]}")
    return result.stdout.decode().strip()


def inspect_container(name: str) -> dict:
    return json.loads(call("docker", "inspect", name))[0]


def inspect_image(image: str) -> dict:
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", image):
        raise ValueError("Maintenance requires an immutable local image ID")
    return json.loads(call("docker", "image", "inspect", image))[0]


def validate_contracts(old: dict, new: dict) -> None:
    changed = {path for path in old["files"].keys() | new["files"].keys() if old["files"].get(path) != new["files"].get(path)}
    unrelated = changed - {"api/db/db_models.py", "api/db/sql_assistant_schema.py"}
    if any(not old.get("canonical_ast", {}).get(path) or old["canonical_ast"][path] != new.get("canonical_ast", {}).get(path) for path in unrelated):
        raise ValueError("Unrelated schema/initializer source changes are outside this maintenance recipe")
    if set(new["ddl"]) != set(old["ddl"]) | TABLES:
        raise ValueError("Unexpected model additions/removals")
    if any(new["ddl"][table] != ddl for table, ddl in old["ddl"].items()):
        raise ValueError("Existing model DDL changed beyond execution metadata")


def metadata_network(active: dict, postgres: dict) -> str:
    shared = set(active["NetworkSettings"]["Networks"]) & set(postgres["NetworkSettings"]["Networks"])
    if len(shared) != 1:
        raise ValueError("Maintenance requires one unambiguous shared PostgreSQL network")
    return shared.pop()


def validate_receipt(receipt: dict, previous: dict, image: str, schema: str) -> None:
    if receipt.get("schema") != 1 or receipt.get("recipe") != RECIPE or receipt.get("status") != "PASS":
        raise ValueError("A completed SQL-assistant maintenance receipt is required")
    if receipt["old"]["image_id"] != previous["web_image_id"] or receipt["old"]["schema_sha256"] != previous["schema_sha256"]:
        raise ValueError("Maintenance receipt belongs to another installed source")
    if receipt["new"]["image_id"] != image or receipt["new"]["schema_sha256"] != schema:
        raise ValueError("Maintenance receipt belongs to another candidate")
    if set(receipt.get("checks", {})) != CHECKS or any(value != "PASS" for value in receipt["checks"].values()):
        raise ValueError("Maintenance rehearsal is incomplete")
    if receipt.get("probe_sha256") != checksum(PROBE):
        raise ValueError("Maintenance verification program changed")
    backup = Path(receipt["backup"]["path"])
    if not backup.is_file() or checksum(backup) != receipt["backup"]["sha256"]:
        raise ValueError("Verified maintenance backup is missing or changed")
    rehearsal = Path(receipt["rehearsal"]["path"])
    if not rehearsal.is_file() or checksum(rehearsal) != receipt["rehearsal"]["sha256"]:
        raise ValueError("Maintenance rehearsal evidence is missing or changed")


class Maintenance:
    def __init__(self, project: str, runtime: Path, image: str, evidence: Path):
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,45}", project):
            raise ValueError("Invalid Compose project")
        self.project, self.runtime, self.image = project, runtime.absolute(), image
        self.evidence = evidence.absolute()
        self.private = self.runtime / "sql-assistant-maintenance"
        self.pending = self.private / "pending.json"
        self.config = self.private / "service_conf.yaml"
        self.qa_config = self.private / "qa-service-conf.yaml"
        self.environment = self.private / "application.env"
        self.deployer = BlueGreen(project, self.runtime)

    def load(self):
        context = json.loads(call("docker", "context", "inspect"))[0]
        endpoint = context["Endpoints"]["docker"]["Host"]
        if not endpoint.startswith(("npipe://", "unix://")):
            raise ValueError("This maintenance recipe is local-only")
        if self.deployer.journal.exists():
            raise ValueError("Recover the interrupted application deployment first")
        self.previous = json.loads(self.deployer.state_path.read_bytes())
        self.active = inspect_container(f"{self.project}-{self.previous['slot']}")
        if self.active["Config"]["Labels"].get("org.ragflow.blue-green") != self.project or self.active["Image"] != self.previous["web_image_id"]:
            raise ValueError("Installed container identity differs from saved deployment")
        self.candidate = inspect_image(self.image)
        self.source_id = (self.candidate["Config"].get("Labels") or {}).get("org.ragflow.source-id", "")
        if not re.fullmatch(r"[a-f0-9]{64}", self.source_id) or (self.candidate["Os"], self.candidate["Architecture"], self.candidate["Config"]["Labels"].get("org.ragflow.validation")) != (
            "linux",
            "amd64",
            "feature-build-only",
        ):
            raise ValueError("Candidate has no exact Feature source identity")
        self.postgres = inspect_container(f"{self.project}-postgres-1")
        labels = self.postgres["Config"]["Labels"]
        if labels.get("com.docker.compose.project") != self.project or labels.get("com.docker.compose.service") != "postgres":
            raise ValueError("Refusing a foreign PostgreSQL container")
        self.network = metadata_network(self.active, self.postgres)

    def configure(self, owner):
        self.private.mkdir(parents=True, exist_ok=True)
        self.evidence.mkdir(parents=True, exist_ok=True)
        call("docker", "cp", f"{self.project}-{self.previous['slot']}:/ragflow/conf/service_conf.yaml", str(self.config))
        config = yaml.safe_load(self.config.read_text(encoding="utf-8"))
        config["postgres"] = {"host": "127.0.0.1", "port": 5432, "name": "qa", "user": "postgres", "password": "qa-only"}
        self.qa_config.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
        environment = dict(item.split("=", 1) for item in self.active["Config"]["Env"])
        environment.update(DB_TYPE="postgres", RAGFLOW_SKIP_DATABASE_INIT="true", RAGFLOW_BACKGROUND_ENABLED="false")
        if any("\n" in value or "\r" in value for value in environment.values()):
            raise ValueError("Multiline environment requires a separately reviewed configuration adapter")
        self.environment.write_text("\n".join(f"{key}={value}" for key, value in sorted(environment.items())) + "\n", encoding="utf-8")
        for path in (self.config, self.qa_config, self.environment):
            path.chmod(0o600)
            owner.path(path, category="config")

    def job(self, image: str, mode: str, *, clone=None) -> dict:
        with Run("sql-assistant-maintenance-probe", category="disposable", days=2) as run:
            run.use_image(image)
            try:
                text = call(
                    "docker",
                    "run",
                    "--rm",
                    "-i",
                    "--network",
                    "container:" + clone if clone else self.network,
                    "--env-file",
                    str(self.environment),
                    "--mount",
                    f"type=bind,src={self.qa_config if clone else self.config},dst=/ragflow/conf/service_conf.yaml,readonly",
                    *(["--env", "SQL_ASSISTANT_REHEARSAL=1"] if clone else []),
                    "--entrypoint",
                    "/ragflow/.venv/bin/python",
                    image,
                    "-B",
                    "-",
                    mode,
                    data=PROBE.read_bytes(),
                    timeout=600,
                )
                result = [line.removeprefix("SQL_ASSISTANT_MAINTENANCE_RESULT=") for line in text.splitlines() if line.startswith("SQL_ASSISTANT_MAINTENANCE_RESULT=")]
                if len(result) != 1:
                    raise ValueError("Missing or ambiguous maintenance probe output")
                return json.loads(result[0])
            finally:
                run.retire_owned()

    def contracts(self):
        old = self.job(self.previous["web_image_id"], "contract")
        new = self.job(self.image, "contract")
        from tools.quality.artifact_lifecycle import current

        for label, value in (("old", old), ("new", new)):
            path = self.evidence / (label + "-contract.json")
            write_json(path, value)
            current().path(path)
        validate_contracts(old, new)
        if old["schema_sha256"] != self.previous["schema_sha256"]:
            raise ValueError("Installed schema source differs from saved state")
        self.database_identity = self.job(self.previous["web_image_id"], "database")
        control = call(
            "docker",
            "exec",
            self.postgres["Id"],
            "sh",
            "-lc",
            'psql -X -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At -c "SELECT current_database() || \'|\' || (pg_control_system()).system_identifier::text"',
        )
        if control != self.database_identity["name"] + "|" + self.database_identity["system_identifier"]:
            raise ValueError("Application and backup address different PostgreSQL databases")
        self.old = {"image_id": self.previous["web_image_id"], "schema_sha256": old["schema_sha256"]}
        self.new = {"image_id": self.image, "schema_sha256": new["schema_sha256"], "source_id": self.source_id}

    def backup(self, owner, directory: Path) -> dict:
        directory.mkdir(parents=True, exist_ok=True)
        name = "sql-assistant-" + uuid4().hex + ".dump"
        destination = directory / name
        temporary = "/tmp/" + name
        container = self.postgres["Id"]
        copied = False
        try:
            call("docker", "exec", container, "sh", "-lc", f'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc -f {temporary} && pg_restore --list {temporary} >/dev/null', timeout=600)
            call("docker", "cp", f"{container}:{temporary}", str(destination))
            copied = True
        finally:
            if destination.exists():
                destination.chmod(0o600)
                owner.path(destination, category="backup")
                for entry in owner.data["resources"]:
                    if entry.get("path") == str(destination.absolute()):
                        entry.update(data_scope=self.project + "/PostgreSQL metadata", validation="format-checked" if copied else "incomplete")
                if not copied:
                    owner.failed = True
                owner.persist()
            call("docker", "exec", container, "rm", "-f", temporary)
        return {"path": str(destination), "sha256": checksum(destination), "database_scope": self.project + "/PostgreSQL metadata"}

    @contextmanager
    def restored(self, backup: dict):
        name = "qa-sql-migration-" + uuid4().hex[:16]
        with Run("sql-assistant-backup-restore", category="disposable", days=2) as run:
            run.use_image(self.postgres["Image"])
            try:
                call(
                    "docker",
                    "run",
                    "--rm",
                    "-d",
                    "--name",
                    name,
                    "--network",
                    "none",
                    "--tmpfs",
                    "/var/lib/postgresql/data:rw,size=2g",
                    "-e",
                    "POSTGRES_HOST_AUTH_METHOD=trust",
                    "-e",
                    "POSTGRES_DB=qa",
                    self.postgres["Image"],
                )
                deadline = time.monotonic() + 90
                while True:
                    try:
                        call("docker", "exec", name, "pg_isready", "-h", "127.0.0.1", "-U", "postgres", "-d", "qa")
                        break
                    except RuntimeError:
                        if time.monotonic() >= deadline:
                            raise
                        time.sleep(1)
                info = inspect_container(name)
                from tools.quality.artifact_docker import RUN

                if info["Config"]["Labels"].get(RUN) != run.data["run_id"]:
                    raise ValueError("Restore target is not this rehearsal's disposable container")
                call("docker", "cp", backup["path"], name + ":/tmp/input.dump")
                self.restore(name)
                yield name
            finally:
                run.retire_owned()

    @staticmethod
    def restore(name):
        call("docker", "exec", name, "dropdb", "--force", "-U", "postgres", "qa")
        call("docker", "exec", name, "createdb", "-U", "postgres", "qa")
        call("docker", "exec", name, "pg_restore", "--exit-on-error", "--no-owner", "--no-acl", "-U", "postgres", "-d", "qa", "/tmp/input.dump", timeout=600)

    def rehearse(self, owner, backups: Path) -> dict:
        self.load()
        if self.pending.exists():
            raise ValueError("Recover the interrupted maintenance first")
        self.configure(owner)
        self.contracts()
        print("Maintenance source contracts verified; creating rehearsal backup", flush=True)
        backup = self.backup(owner, backups)
        with self.restored(backup) as clone:
            baseline = self.job(self.previous["web_image_id"], "signature", clone=clone)
            result = self.job(self.image, "exercise", clone=clone)
            if result.pop("baseline") != baseline:
                raise ValueError("Rehearsal baseline differs between binaries")
            compatibility = self.job(self.previous["web_image_id"], "compatibility", clone=clone)
            self.restore(clone)
            if self.job(self.previous["web_image_id"], "signature", clone=clone) != baseline:
                raise ValueError("Full PostgreSQL recovery changed original row multisets or sequences")
        receipt = {
            "schema": 1,
            "recipe": RECIPE,
            "status": "PASS",
            "old": self.old,
            "new": self.new,
            "database_identity": self.database_identity,
            "probe_sha256": checksum(PROBE),
            "backup": backup,
            "objects_sha256": result["objects_sha256"],
            "checks": {key: "PASS" for key in CHECKS},
            "details": {
                "exercise": result,
                "previous_binary": compatibility,
                "restored_tables": len(baseline["tables"]),
                "baseline_sha256": hashlib.sha256(json.dumps(baseline, sort_keys=True).encode()).hexdigest(),
            },
        }
        path = self.evidence / "rehearsal.json"
        write_json(path, receipt)
        owner.path(path)
        print("Migration, atomic failure, repeat, previous binary and full-dump recovery: PASS", flush=True)
        return receipt

    def resume_previous(self, pending):
        for role in pending["running"]:
            info = self.deployer.inspect(role)
            if info["Image"] != pending["images"][role]:
                raise ValueError("Refusing to start a replaced maintenance container")
        call("docker", "start", *(f"{self.project}-{role}" for role in pending["running"]))
        self.deployer.wait_ready(self.previous["slot"])
        self.deployer.route(self.previous["slot"], self.previous["token"])
        self.deployer.wait_route(self.previous["token"])
        for role in pending["running"]:
            if role != self.previous["slot"]:
                self.deployer.wait_ready(role)

    def apply(self, owner, rehearsal_path: Path, backups: Path):
        self.load()
        if self.pending.exists():
            raise ValueError("Recover the interrupted maintenance first")
        self.configure(owner)
        self.contracts()
        rehearsal = json.loads(rehearsal_path.read_bytes())
        if (
            rehearsal.get("status") != "PASS"
            or rehearsal.get("old") != self.old
            or rehearsal.get("new") != self.new
            or rehearsal.get("probe_sha256") != checksum(PROBE)
            or rehearsal.get("checks") != {key: "PASS" for key in CHECKS}
        ):
            raise ValueError("Rehearsal does not qualify these exact source images")
        if checksum(Path(rehearsal["backup"]["path"])) != rehearsal["backup"]["sha256"]:
            raise ValueError("Rehearsal backup changed")
        if rehearsal.get("database_identity") != self.database_identity:
            raise ValueError("Rehearsal belongs to another PostgreSQL database")
        running, images = [], {}
        for role in ("web-blue", "web-green", "worker"):
            info = self.deployer.inspect(role)
            images[role] = info["Image"]
            if info["State"]["Status"] == "running":
                running.append(role)
        pending = {"previous": self.previous, "running": running, "images": images, "stage": "quiescing"}
        write_json(self.pending, pending)
        owner.path(self.pending, category="config")
        try:
            print("Entering local SQL-assistant maintenance window", flush=True)
            self.deployer.compose("stop", "--timeout", "30", "worker", "web-blue", "web-green")
            baseline = self.job(self.previous["web_image_id"], "signature")
            backup = self.backup(owner, backups)
            pending.update(stage="backup-created", backup=backup)
            write_json(self.pending, pending)
            with self.restored(backup) as clone:
                if self.job(self.previous["web_image_id"], "signature", clone=clone) != baseline:
                    raise ValueError("Fresh quiesced backup failed full data restore comparison")
            pending["stage"] = "applying"
            write_json(self.pending, pending)
            result = self.job(self.image, "apply")
            if result["objects_sha256"] != rehearsal["objects_sha256"] or result["database_identity"] != self.database_identity or self.job(self.previous["web_image_id"], "signature") != baseline:
                raise ValueError("Installed schema or original data differs from the qualified migration")
            self.job(self.previous["web_image_id"], "compatibility")
            pending["stage"] = "migrated"
            write_json(self.pending, pending)
            self.resume_previous(pending)
            receipt = {
                **rehearsal,
                "backup": backup,
                "rehearsal": {"path": str(rehearsal_path.absolute()), "sha256": checksum(rehearsal_path)},
                "project": self.project,
                "runtime": str(self.runtime),
                "objects_sha256": result["objects_sha256"],
            }
            path = self.evidence / "maintenance-receipt.json"
            write_json(path, receipt)
            owner.path(path)
            self.pending.unlink()
            print("Additive migration applied; previous image health recovered; maintenance receipt written", flush=True)
            return receipt
        except BaseException:
            # PostgreSQL rolls back failed DDL. Committed additions remain compatible;
            # restoring an older dump here would discard legitimate newer writes.
            self.resume_previous(pending)
            self.pending.unlink()
            raise

    def recover(self):
        self.load()
        pending = json.loads(self.pending.read_bytes())
        if pending["previous"] != self.previous:
            raise ValueError("Maintenance journal belongs to a different active deployment")
        self.resume_previous(pending)
        self.pending.unlink()


def admit_receipt(path: Path, previous: dict, config: dict, schema: str, project: str, runtime: Path):
    image = config["services"]["web-blue"]["image"]
    receipt = json.loads(path.read_bytes())
    validate_receipt(receipt, previous, image, schema)
    if receipt.get("project") != project or Path(receipt.get("runtime", "")).absolute() != runtime.absolute():
        raise ValueError("Maintenance receipt belongs to another target")
    maintenance = Maintenance(project, runtime, image, path.parent)
    maintenance.load()
    if maintenance.pending.exists():
        raise ValueError("Maintenance recovery is incomplete")
    if maintenance.source_id != receipt["new"]["source_id"]:
        raise ValueError("Maintenance Feature source identity differs")
    from tools.quality.artifact_lifecycle import current

    owner = current()
    if owner is None:
        with Run("sql-assistant-maintenance-admission", category="evidence", days=30) as run:
            maintenance.configure(run)
            actual = maintenance.job(image, "verify")
    else:
        maintenance.configure(owner)
        actual = maintenance.job(image, "verify")
    if actual["objects_sha256"] != receipt["objects_sha256"]:
        raise ValueError("Database schema no longer matches the maintenance receipt")
    if actual["database_identity"] != receipt.get("database_identity"):
        raise ValueError("Maintenance receipt belongs to another PostgreSQL database")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default="ragflow-local")
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--backups", type=Path, required=True)
    parser.add_argument("--rehearsal", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--recover", action="store_true")
    args = parser.parse_args()
    if args.apply and not args.rehearsal:
        parser.error("--apply requires --rehearsal")
    maintenance = Maintenance(args.project, args.runtime, args.image, args.evidence)
    with Run("sql-assistant-schema-maintenance", category="runtime" if args.apply or args.recover else "evidence", days=30) as owner, deployment_lock(args.runtime):
        owner.data.update(runtime_project=args.project, purpose="Verified additive SQL-assistant foundation migration")
        owner.persist()
        if args.recover:
            maintenance.recover()
        elif args.apply:
            maintenance.apply(owner, args.rehearsal, args.backups)
        else:
            maintenance.rehearse(owner, args.backups)


if __name__ == "__main__":
    main()
