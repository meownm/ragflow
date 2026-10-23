"""Qualify PostgreSQL upgrade and backup restoration using two actual images.

The probe is instrumentation; Python, dependencies, configuration and models come
from each image. No runtime source or dependency directory is mounted into them.
Historical diagnostic results remain incomplete. An explicitly approved initial
baseline may pass its limited migration/restore scope through bootstrap policy.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import signal
import subprocess
import sys
import tempfile
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.quality.release_evidence import DIGEST, SHA, validate_publication
from tools.quality.release_origin import verify_release_download
from tools.quality.redact_ci_log import redact

PROBE = ROOT / "test/integration/fixtures/upgrade_data_probe.py"
BOOTSTRAP_POLICY = ROOT / "tools/quality/release-bootstrap-baseline.json"
OWNER_LABEL = "ragflow.qualified-upgrade"


def command(arguments, *, data=None, timeout=240):
    result = subprocess.run(arguments, input=data, capture_output=True, timeout=timeout, check=False)
    if result.returncode:
        raise RuntimeError(f"{arguments[0]} operation failed: {redact(result.stderr.decode(errors='replace'))[-2000:]}")
    return result.stdout


def trusted_previous(directory: Path) -> dict:
    receipt_path = directory / "candidate-receipt.json"
    receipt = json.loads(receipt_path.read_text())
    proof = json.loads((directory / "publication-receipt.json").read_text())
    verify_release_download(directory, proof)
    validate_publication(receipt, proof, hashlib.sha256(receipt_path.read_bytes()).hexdigest())
    return receipt


def historical_baseline(image: str, revision: str, current_sha: str, repository: str) -> dict:
    """Verify a diagnostic ancestor; GitHub origin does not qualify its image."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]*@sha256:[0-9a-f]{64}", image) or not SHA.fullmatch(revision) or not SHA.fullmatch(current_sha):
        raise ValueError("Historical diagnostic baseline requires immutable image and full source SHA")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("Invalid historical baseline GitHub repository")
    commit = json.loads(command(["gh", "api", f"repos/{repository}/commits/{revision}"]))
    if not isinstance(commit, dict) or commit.get("sha") != revision:
        raise ValueError("Historical baseline commit does not match GitHub repository")
    comparison = json.loads(command(["gh", "api", f"repos/{repository}/compare/{revision}...{current_sha}"]))
    merge_base = comparison.get("merge_base_commit") if isinstance(comparison, dict) else None
    if not isinstance(comparison, dict) or comparison.get("status") not in {"ahead", "identical"} or not isinstance(merge_base, dict) or merge_base.get("sha") != revision:
        raise ValueError("Historical baseline is not a verified ancestor of candidate")
    return dict(repository=repository, source_sha=revision, image_digest=image, github_commit_verified=True, candidate_ancestor_verified=True, qualified=False)


def approved_bootstrap_baseline(image: str, revision: str, repository: str) -> str:
    """Bind the approved initial baseline to the exact checked-in policy bytes."""
    content = BOOTSTRAP_POLICY.read_bytes()
    expected = dict(schema=1, purpose="approved-initial-runtime-baseline", repository=repository, image_digest=image, source_sha=revision, qualification="historical-unqualified")
    policy = json.loads(content)
    if not isinstance(policy, dict) or type(policy.get("schema")) is not int or policy != expected:
        raise ValueError("Bootstrap baseline does not match approved initial runtime policy")
    return hashlib.sha256(content).hexdigest()


def assert_preserved(previous: dict, current: dict) -> None:
    def order(row):
        return json.dumps(row, sort_keys=True, default=str)

    for table, rows in previous.items():
        if table not in current:
            raise ValueError(f"Previous qualified table disappeared: {table}")
        columns = set().union(*(row.keys() for row in rows))
        if any(not columns <= row.keys() for row in current[table]):
            raise ValueError(f"Previous qualified columns disappeared: {table}")
        projected = [{column: row[column] for column in columns} for row in current[table]]
        if sorted(rows, key=order) != sorted(projected, key=order):
            raise ValueError(f"Previous qualified rows changed: {table}")


class DisposableUpgrade:
    def __init__(self, output: Path):
        self.output = output
        self.owner = "/".join((os.getenv("GITHUB_REPOSITORY", "local"), os.getenv("GITHUB_RUN_ID", "local"), os.getenv("GITHUB_RUN_ATTEMPT", "1"), "qualified-upgrade", uuid4().hex))
        self.name = "rf-upgrade-" + uuid4().hex
        self.resources: list[tuple[str, str]] = []
        self.password = secrets.token_hex(24)
        self.database = "t1_upgrade_" + uuid4().hex
        self.pg = ""
        self.record_resources()

    def record_resources(self) -> None:
        (self.output / "qualified-upgrade-resources.json").write_text(json.dumps({"owner": self.owner, "resources": self.resources}, indent=2) + "\n")

    def create(self, kind: str, arguments: list[str]) -> str:
        identifier = command(["docker", kind, "create", "--label", f"{OWNER_LABEL}={self.owner}", *arguments]).decode().strip()
        if not re.fullmatch(r"[a-f0-9]{64}|rf-upgrade-[a-f0-9]{32}", identifier):
            raise ValueError("Unexpected owned Docker resource identity")
        self.resources.append((kind, identifier))
        self.record_resources()
        return identifier

    def container(self, image: str, arguments: list[str]) -> str:
        identifier = command(["docker", "create", "--label", f"{OWNER_LABEL}={self.owner}", *arguments, image]).decode().strip()
        if not re.fullmatch(r"[a-f0-9]{64}", identifier):
            raise ValueError("Unexpected owned container identity")
        self.resources.append(("container", identifier))
        self.record_resources()
        return identifier

    def cleanup(self) -> None:
        errors = []
        for kind, identifier in reversed(tuple(self.resources)):
            try:
                label_path = ".Config.Labels" if kind == "container" else ".Labels"
                labels = json.loads(command(["docker", kind, "inspect", identifier, "--format", "{{json " + label_path + "}}"]))
                if not isinstance(labels, dict) or labels.get(OWNER_LABEL) != self.owner:
                    raise ValueError("Refusing to remove foreign Docker resource")
                args = ["docker", kind, "rm", identifier]
                if kind == "container":
                    args.insert(3, "--force")
                command(args)
                remaining = subprocess.run(["docker", kind, "inspect", identifier], capture_output=True, timeout=30)
                if remaining.returncode == 0:
                    raise RuntimeError("Owned Docker resource survived cleanup")
                if b"No such" not in remaining.stderr and b"not found" not in remaining.stderr:
                    raise RuntimeError("Cannot verify owned resource removal")
                self.resources.remove((kind, identifier))
                self.record_resources()
            except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as error:
                errors.append(f"{kind}: {type(error).__name__}")
        if errors:
            raise RuntimeError("Owned resource cleanup incomplete: " + ", ".join(errors))
        self.record_resources()

    def start_postgres(self, image: str):
        network = self.create("network", ["--internal", self.name])
        volume = self.create("volume", [self.name])
        self.pg = self.container(
            image,
            [
                "--network",
                network,
                "--network-alias",
                "upgrade-postgres",
                "--mount",
                f"type=volume,src={volume},dst=/var/lib/postgresql/data",
                "--env",
                "POSTGRES_USER=postgres",
                "--env",
                f"POSTGRES_PASSWORD={self.password}",
            ],
        )
        command(["docker", "start", self.pg])
        for _ in range(60):
            result = subprocess.run(["docker", "exec", self.pg, "pg_isready", "-U", "postgres"], capture_output=True, timeout=10)
            if result.returncode == 0:
                break
            time.sleep(1)
        else:
            raise RuntimeError("Disposable PostgreSQL readiness failed")
        for suffix in ("main", "current_restore", "rollback"):
            command(["docker", "exec", self.pg, "createdb", "-U", "postgres", f"{self.database}_{suffix}"])
        return network

    def run_probe(self, image: str, revision: str, network: str, mode: str, database: str, label: str) -> dict:
        config = dict(name=database, host="upgrade-postgres", port=5432, user="postgres", password=self.password, options="-c statement_timeout=30000 -c lock_timeout=10000")
        arguments = [
            "--network",
            network,
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--env",
            "DB_TYPE=postgres",
            "--env",
            "PYTHONDONTWRITEBYTECODE=1",
            "--env",
            "LITELLM_LOCAL_MODEL_COST_MAP=True",
            "--env",
            "HF_HUB_OFFLINE=1",
            "--env",
            "TRANSFORMERS_OFFLINE=1",
            "--env",
            "RAGFLOW_CREDENTIALS_KEY=t1-synthetic-credential-key",
            "--env",
            "RAGFLOW_UPGRADE_PROBE_DB=" + json.dumps(config),
            "--mount",
            f"type=bind,src={PROBE},dst=/qualification/upgrade_data_probe.py,readonly",
            "--mount",
            f"type=bind,src={self.output},dst=/proof",
            "--entrypoint",
            "/bin/bash",
        ]
        # Docker places command arguments after the image. Keep image code and
        # dependencies intact while injecting only the model probe and DB config.
        identifier = (
            command(
                [
                    "docker",
                    "create",
                    "--label",
                    f"{OWNER_LABEL}={self.owner}",
                    *arguments,
                    image,
                    "-c",
                    'test "$(cat /ragflow/SOURCE_REVISION)" = "$1" && exec /ragflow/.venv/bin/python /qualification/upgrade_data_probe.py /ragflow "$2" "$3"',
                    "probe",
                    revision,
                    mode,
                    f"/proof/{label}.json",
                ]
            )
            .decode()
            .strip()
        )
        if not re.fullmatch(r"[a-f0-9]{64}", identifier):
            raise ValueError("Unexpected owned probe container identity")
        self.resources.append(("container", identifier))
        self.record_resources()
        logs = command(["docker", "start", "--attach", identifier], timeout=600)
        (self.output / f"{label}.log").write_text(redact(logs.decode(errors="replace")))
        if command(["docker", "inspect", identifier, "--format", "{{.State.ExitCode}}"]).strip() != b"0":
            raise RuntimeError(f"Actual image model probe failed: {label}")
        actual = command(["docker", "inspect", identifier, "--format", "{{.Image}}"]).strip()
        expected = command(["docker", "image", "inspect", image, "--format", "{{.Id}}"]).strip()
        if actual != expected:
            raise ValueError("Probe executed a different image")
        return json.loads((self.output / f"{label}.json").read_text())

    def postgres_tool(self, tool: str, database: str, data=None) -> bytes:
        arguments = ["docker", "exec", "-i", self.pg, tool, "-U", "postgres", "-d", database]
        arguments.extend(["-Fc", "--no-owner", "--no-acl"] if tool == "pg_dump" else ["--exit-on-error", "--no-owner", "--no-acl"])
        return command(arguments, data=data)


def qualify(
    previous_directory: Path | None,
    current_image: str,
    current_sha: str,
    output: Path,
    postgres_image: str,
    *,
    historical_image: str | None = None,
    historical_sha: str | None = None,
    repository: str = "meownm/ragflow",
    bootstrap: bool = False,
) -> dict:
    historical = historical_image is not None or historical_sha is not None
    diagnostic = historical and not bootstrap
    report = dict(
        schema=1,
        purpose="initial-runtime-baseline-upgrade" if bootstrap else "historical-image-upgrade-diagnostic" if diagnostic else "previous-qualified-image-upgrade",
        status="INCOMPLETE",
        source_sha=current_sha,
        image_digest=current_image,
        scope="Actual image Python/model initializer, synthetic PostgreSQL rows and backup restoration; no object store or runtime API",
    )
    if os.getenv("RAGFLOW_TEST_DISPOSABLE") != "1":
        raise ValueError("Explicit RAGFLOW_TEST_DISPOSABLE=1 is required")
    if not SHA.fullmatch(current_sha) or not DIGEST.fullmatch(current_image):
        raise ValueError("Candidate requires full source SHA and immutable image digest")
    if bootstrap and not historical:
        raise ValueError("Bootstrap requires historical image and full source SHA")
    if historical:
        if previous_directory is not None or not historical_image or not historical_sha:
            raise ValueError("Select a complete historical diagnostic baseline or previous qualified release")
        if bootstrap:
            report["bootstrap_baseline_sha256"] = approved_bootstrap_baseline(historical_image, historical_sha, repository)
        baseline = historical_baseline(historical_image, historical_sha, current_sha, repository)
        old_image, old_sha = historical_image, historical_sha
        report["historical_baseline"] = baseline
        if diagnostic:
            report.update(diagnostic_status="INCOMPLETE", reason="Historical image has no trusted qualification receipt; diagnostic results cannot authorize publication")
        else:
            report["scope"] = (
                "Approved initial historical baseline: actual image Python/model initializer, synthetic PostgreSQL rows and backup restoration; no prior full qualification, object store or runtime API"
            )
    elif previous_directory is None:
        report["reason"] = "Previous trusted qualified release unavailable; initial rollout requires an approved baseline"
        return report
    else:
        previous = trusted_previous(previous_directory)
        old_image, old_sha = previous["image"]["digest"], previous["source_revision"]
        report["previous_receipt_sha256"] = hashlib.sha256((previous_directory / "candidate-receipt.json").read_bytes()).hexdigest()
    report.update(previous_image_digest=old_image, previous_source_sha=old_sha)
    if not DIGEST.fullmatch(postgres_image):
        raise ValueError("Qualification PostgreSQL image must be pinned by digest")
    for image in {old_image, current_image, postgres_image}:
        command(["docker", "pull", image], timeout=600)
    state = DisposableUpgrade(output)
    report["resource_owner"] = state.owner
    (output / "qualified-upgrade.json").write_text(json.dumps(report, indent=2) + "\n")
    try:
        network = state.start_postgres(postgres_image)
        main, restore, rollback = (f"{state.database}_{suffix}" for suffix in ("main", "current_restore", "rollback"))
        old = state.run_probe(old_image, old_sha, network, "seed", main, "previous-seeded")
        for table in ("business_document_revision", "business_document_evidence_snapshot", "system_audit_event"):
            if not old.get(table):
                raise ValueError(f"Previous image did not seed mandatory data: {table}")
        old_dump = state.postgres_tool("pg_dump", main)
        (output / "previous.dump").write_bytes(old_dump)
        upgraded = state.run_probe(current_image, current_sha, network, "upgrade", main, "candidate-upgraded")
        assert_preserved(old, upgraded)
        repeated = state.run_probe(current_image, current_sha, network, "upgrade", main, "candidate-repeat")
        if repeated != upgraded:
            raise ValueError("Repeated actual image initializer changed rows")
        compatible = state.run_probe(old_image, old_sha, network, "read", main, "previous-reads-upgraded")
        assert_preserved(old, compatible)
        current_dump = state.postgres_tool("pg_dump", main)
        (output / "candidate.dump").write_bytes(current_dump)
        state.postgres_tool("pg_restore", restore, current_dump)
        if state.run_probe(current_image, current_sha, network, "read", restore, "candidate-restored") != upgraded:
            raise ValueError("Candidate image cannot read restored candidate backup")
        state.postgres_tool("pg_restore", rollback, old_dump)
        if state.run_probe(old_image, old_sha, network, "read", rollback, "previous-restored") != old:
            raise ValueError("Previous actual image cannot read restored previous backup")
        report.update(
            status="INCOMPLETE" if diagnostic else "PASS",
            repeated_initializer=True,
            candidate_restore=True,
            previous_restore=True,
            previous_reads_upgraded=True,
            postgres_image_digest=postgres_image,
            previous_tables_checked=len(old),
            probe_sha256=hashlib.sha256(PROBE.read_bytes()).hexdigest(),
            previous_backup_sha256=hashlib.sha256(old_dump).hexdigest(),
            candidate_backup_sha256=hashlib.sha256(current_dump).hexdigest(),
            restore_data_boundary="Previous backup snapshot; writes after this snapshot are not preserved by rollback",
        )
        if diagnostic:
            report["diagnostic_status"] = "PASS"
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        if diagnostic:
            report.update(diagnostic_status="FAIL", diagnostic_reason=redact(str(error)))
        else:
            report.update(status="FAIL", reason=redact(str(error)))
    finally:
        try:
            state.cleanup()
            report["cleanup"] = "PASS"
        except (RuntimeError, ValueError, subprocess.TimeoutExpired) as error:
            report.update(status="INCOMPLETE" if diagnostic else "FAIL", cleanup="FAIL", cleanup_reason=redact(str(error)))
            if diagnostic:
                report["diagnostic_status"] = "FAIL"
    return report


def main() -> int:
    def cancelled(signum, frame):
        raise KeyboardInterrupt("Qualification cancelled")

    signal.signal(signal.SIGTERM, cancelled)
    parser = argparse.ArgumentParser(description=__doc__)
    baseline = parser.add_mutually_exclusive_group()
    baseline.add_argument("--previous-release-directory", type=Path)
    baseline.add_argument("--previous-tag")
    baseline.add_argument("--historical-image", help="Immutable historical image without previous release qualification proof")
    parser.add_argument("--historical-sha", help="Full historical source SHA, verified as candidate ancestor through GitHub")
    parser.add_argument("--bootstrap", action="store_true", help="One-time initial baseline migration/restore under checked-in approval policy")
    parser.add_argument("--baseline-repository", default=os.getenv("RAGFLOW_RELEASE_REPOSITORY", "meownm/ragflow"))
    parser.add_argument("--image", default=os.getenv("RAGFLOW_IMAGE"), required=not os.getenv("RAGFLOW_IMAGE"))
    parser.add_argument("--sha", default=os.getenv("SOURCE_SHA"), required=not os.getenv("SOURCE_SHA"))
    parser.add_argument("--postgres-image", default=os.getenv("RAGFLOW_QUALIFICATION_POSTGRES_IMAGE", ""))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    diagnostic = (args.historical_image is not None or args.historical_sha is not None) and not args.bootstrap
    purpose = "initial-runtime-baseline-upgrade" if args.bootstrap else "historical-image-upgrade-diagnostic" if diagnostic else "previous-qualified-image-upgrade"
    args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ragflow-previous-release-") as downloaded:
        previous_directory = args.previous_release_directory
        try:
            if args.bootstrap and (not args.historical_image or not args.historical_sha):
                raise ValueError("Bootstrap requires historical image and full source SHA")
            if args.historical_sha and not args.historical_image:
                raise ValueError("Select a complete historical diagnostic baseline or previous qualified release")
            if args.previous_tag:
                previous_directory = Path(downloaded)
                repository = os.getenv("RAGFLOW_RELEASE_REPOSITORY", "meownm/ragflow")
                command(
                    [
                        "gh",
                        "release",
                        "download",
                        args.previous_tag,
                        "--repo",
                        repository,
                        "--dir",
                        downloaded,
                        "--pattern",
                        "candidate-receipt.json",
                        "--pattern",
                        "publication-receipt.json",
                        "--pattern",
                        "release-manifest.json",
                    ]
                )
            report = qualify(
                previous_directory,
                args.image,
                args.sha,
                args.output,
                args.postgres_image,
                historical_image=args.historical_image,
                historical_sha=args.historical_sha,
                repository=args.baseline_repository,
                bootstrap=args.bootstrap,
            )
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
            report = dict(schema=1, purpose=purpose, status="INCOMPLETE" if diagnostic else "FAIL", source_sha=args.sha, image_digest=args.image, reason=redact(str(error)))
            if diagnostic:
                report["diagnostic_status"] = "FAIL"
        except KeyboardInterrupt:
            report = dict(schema=1, purpose=purpose, status="CANCELLED", source_sha=args.sha, image_digest=args.image, reason="Qualification cancelled")
            if diagnostic:
                report["diagnostic_status"] = "CANCELLED"
    (args.output / "qualified-upgrade.json").write_text(json.dumps(report, indent=2) + "\n")
    return {"PASS": 0, "FAIL": 1, "INCOMPLETE": 2, "CANCELLED": 130}[report["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
