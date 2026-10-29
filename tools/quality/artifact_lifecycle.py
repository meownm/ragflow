"""Owned artifact runs, leases, producer instrumentation and resumable retirement."""

from __future__ import annotations

import argparse
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from copy import deepcopy
from datetime import datetime, timezone
from functools import wraps
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.quality.artifact_registry import ROOT, receipt_pins, save, unlinked
from tools.quality.check_cache import cache_lock
from tools.quality.artifact_policy import CATEGORIES, PRESERVED_OUTPUT_CLASSES

PROJECT = "org.ragflow.artifact.project"
RUN = "org.ragflow.artifact.run"
OBJECT = "org.ragflow.artifact.object"
CLASS = "org.ragflow.artifact.class"
_current = ContextVar("artifact_run", default=None)
_live = {}
_mutating = ContextVar("artifact_mutation", default=None)


@contextmanager
def mutation(store: Path):
    """Brief shared commit boundary; producers wait rather than fail during a sweep."""
    if _mutating.get() == store:
        yield
        return
    deadline = time.monotonic() + 60
    while True:
        guard = cache_lock(store, "mutation")
        try:
            guard.__enter__()
            break
        except OSError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.05)
    token = _mutating.set(store)
    try:
        yield
    finally:
        _mutating.reset(token)
        guard.__exit__(None, None, None)


def utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def store_path() -> Path:
    default = ROOT / ".local/maintenance/artifacts" if (ROOT / ".git").exists() and os.environ.get("GITHUB_ACTIONS") != "true" else Path.home() / ".local/state/ragflow-artifacts"
    return Path(os.environ.get("RAGFLOW_ARTIFACT_STATE", default)).absolute()


def checksum(value: dict) -> str:
    return hashlib.sha256(json.dumps({k: v for k, v in value.items() if k != "seal"}, sort_keys=True).encode()).hexdigest()


def file_checksum(path: Path) -> str:
    unlinked(path)
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def run_path(store: Path, run_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", run_id):
        raise ValueError("Invalid artifact run ID")
    return store / "runs" / (run_id + ".json")


def write_run(store: Path, value: dict) -> None:
    value["seal"] = checksum(value)
    save(value, run_path(store, value["run_id"]))


def read_run(store: Path, run_id: str) -> dict:
    path = run_path(store, run_id)
    unlinked(path)
    value = json.loads(path.read_bytes())
    if value.get("schema") != 1 or value.get("run_id") != run_id or value.get("project") != "ragflow" or value.get("seal") != checksum(value):
        raise ValueError(f"Invalid lifecycle receipt: {path}")
    return value


def runs(store: Path) -> list[dict]:
    unlinked(store / "runs")
    return [merge_events(store, read_run(store, p.stem)) for p in sorted((store / "runs").glob("*.json"))]


def event(store: Path, run_id: str, **value):
    record = {"at_ns": time.time_ns(), **value}
    record["seal"] = checksum(record)
    with mutation(store):
        save(record, store / "events" / run_id / (uuid.uuid4().hex + ".json"))


def invalidate_consumption(run):
    if previous := run.pop("consumption_closed", None):
        run.setdefault("consumption_history", []).append(previous)


def merge_events(store: Path, run: dict):
    directory = store / "events" / run["run_id"]
    unlinked(directory)
    entries = []
    applied = set(run.get("applied_events", []))
    for path in directory.glob("*.json"):
        if path.stem in applied:
            continue
        unlinked(path)
        value = json.loads(path.read_bytes())
        if value.get("seal") != checksum(value):
            raise ValueError("Corrupt artifact event")
        entries.append({**value, "event_id": path.stem})
    for entry in sorted(entries, key=lambda v: v["at_ns"]):
        if resource := entry.get("resource"):
            values = {r["key"]: r for r in run["resources"]}
            previous = values.get(resource["key"], {})
            if previous.get("status") == "removed" and resource.get("status", "removed") != "removed":
                raise ValueError("Child event cannot reopen a retired resource")
            if any(key in previous and key in resource and previous[key] != resource[key] for key in ("kind", "identity", "token", "path")):
                raise ValueError("Child event cannot replace a recorded resource generation")
            values[resource["key"]] = {**previous, **resource}
            run["resources"] = list(values.values())
        if receipt := entry.get("receipt"):
            run["receipts"] = [r for r in run["receipts"] if r["path"] != receipt["path"]] + [receipt]
        if dependency := entry.get("image_dependency"):
            if dependency not in run.setdefault("image_dependencies", []):
                run["image_dependencies"].append(dependency)
        if intent := entry.get("creation_intent"):
            if intent not in run.setdefault("creation_intents", []):
                run["creation_intents"].append(intent)
        if activity := entry.get("producer_activity"):
            run["usage_epoch"] = activity
            invalidate_consumption(run)
        applied.add(entry["event_id"])
    run["applied_events"] = sorted(applied)
    return run


class LeaseUnavailable(OSError):
    """Another owner holds the lease, or its absence cannot be verified."""


@contextmanager
def lease(store: Path, run_id: str):
    unlinked(store / "leases")
    guard = cache_lock(store / "leases", run_id)
    try:
        guard.__enter__()
    except OSError as error:
        raise LeaseUnavailable("Artifact run lease is unavailable") from error
    try:
        yield
    finally:
        guard.__exit__(None, None, None)


def current():
    value = _current.get() or _live.get(os.environ.get("RAGFLOW_ARTIFACT_RUN_ID"))
    if value is not None:
        return value
    # A subprocess may label resources for its parent's still-leased run.
    identifier = os.environ.get("RAGFLOW_ARTIFACT_RUN_ID")
    if identifier:
        value = Run.__new__(Run)
        value.store = store_path()
        value.data = read_run(value.store, identifier)
        merge_events(value.store, value.data)
        value.mutex = threading.RLock()
        value.attached = True
        token = os.environ.get("RAGFLOW_ARTIFACT_OWNER_TOKEN", "")
        if not token or hashlib.sha256(token.encode()).hexdigest() != value.data.get("owner_token_sha256") or value.data["state"] not in {"running", "waiting"}:
            raise ValueError("Artifact owner capability is absent, expired or invalid")
        if value.data["state"] == "running":
            try:
                with lease(value.store, identifier):
                    pass
            except OSError:
                pass
            else:
                raise ValueError("Artifact owner's process lease has expired")
        return value
    return None


class Run:
    def __init__(self, producer: str, *, category: str = "disposable", days: int = 7, store: Path | None = None):
        if category not in CATEGORIES or days < 0:
            raise ValueError("Invalid artifact policy")
        self.store = (store or store_path()).absolute()
        self.data = {
            "schema": 1,
            "project": "ragflow",
            "run_id": uuid.uuid4().hex,
            "producer": producer,
            "purpose": producer,
            "task_ref": os.environ.get("RAGFLOW_TASK_REF") or os.environ.get("CODEX_THREAD_ID") or os.environ.get("GITHUB_RUN_ID") or producer,
            "state": "running",
            "task_status": "open",
            "created_at": utc(),
            "last_used_at": utc(),
            "category": category,
            "keep_days": days,
            "resources": [],
            "receipts": [],
            "engine": None,
        }
        self.mutex = threading.RLock()
        self.attached = False
        self.preserve = False
        self.failed = False
        if os.environ.get("GITHUB_ACTIONS") == "true":
            self.data["ci"] = {key: os.environ.get(key) for key in ("GITHUB_REPOSITORY", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_SHA")}

    def persist(self):
        if self.attached:
            raise ValueError("A child publishes events, not its owner's lifecycle state")
        with self.mutex, mutation(self.store):
            write_run(self.store, self.data)

    def __enter__(self):
        self.guard = lease(self.store, self.data["run_id"])
        self.guard.__enter__()
        self.previous = {key: os.environ.get(key) for key in ("RAGFLOW_ARTIFACT_RUN_ID", "RAGFLOW_ARTIFACT_STATE", "RAGFLOW_ARTIFACT_OWNER_TOKEN")}
        try:
            head = os.environ.get("SOURCE_SHA") or os.environ.get("GITHUB_SHA")
            if not head:
                try:
                    head = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL, timeout=15).strip()
                except (OSError, subprocess.SubprocessError):
                    head = None
            self.data["source"] = {"head": head if head and re.fullmatch(r"[0-9a-f]{40}", head) else None}
            entrypoint = Path(sys.argv[0]).absolute()
            if entrypoint.is_file() and entrypoint.suffix in {".py", ".sh"}:
                self.data["source"].update(entrypoint=str(entrypoint), entrypoint_sha256=file_checksum(entrypoint))
            self.owner_token = uuid.uuid4().hex + uuid.uuid4().hex
            self.data["owner_token_sha256"] = hashlib.sha256(self.owner_token.encode()).hexdigest()
            self.persist()  # Identity is durable before the first resource is created.
            os.environ.update(RAGFLOW_ARTIFACT_RUN_ID=self.data["run_id"], RAGFLOW_ARTIFACT_STATE=str(self.store), RAGFLOW_ARTIFACT_OWNER_TOKEN=self.owner_token)
            _live[self.data["run_id"]] = self
            self.token = _current.set(self)
            return self
        except BaseException:
            self.guard.__exit__(*sys.exc_info())
            raise

    def __exit__(self, exc_type, exc, traceback):
        try:
            unsuccessful = exc_type is not None and not (isinstance(exc, SystemExit) and (exc.code is None or exc.code == 0))
            capture_error = None
            try:
                self.capture()
            except Exception as error:
                capture_error = error
                self.failed = True
                self.data["capture_failure"] = type(error).__name__
            self.data.update(state="failed" if unsuccessful or self.failed else ("paused" if self.preserve else "completed"), completed_at=utc())
            if unsuccessful:
                self.data["failure"] = exc_type.__name__
            elif capture_error:
                self.data["failure"] = type(capture_error).__name__
            try:
                self.persist()
            except Exception as error:
                if not unsuccessful:
                    raise
                exc.add_note("Artifact lifecycle checkpoint incomplete: " + type(error).__name__)
            if capture_error:
                if not unsuccessful:
                    raise RuntimeError("Artifact lifecycle capture incomplete: " + type(capture_error).__name__) from None
                exc.add_note("Artifact lifecycle capture incomplete: " + type(capture_error).__name__)
        finally:
            _current.reset(self.token)
            _live.pop(self.data["run_id"], None)
            for key, value in self.previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            self.guard.__exit__(exc_type, exc, traceback)

    def engine(self):
        from tools.quality.artifact_docker import Engine

        if getattr(self, "_engine", None) is None:
            with self.mutex, mutation(self.store):
                engine = Engine(expected=self.data["engine"])
                if not self.data["engine"]:
                    self.data["engine"] = engine.identity
                    self.persist()
                self._engine = engine
        return self._engine

    def labels(self, kind: str) -> dict:
        if not self.data["engine"]:
            self.engine()
        ephemeral = self.data["category"] in {"disposable", "cache", "build", "image", "evidence", "log"}
        category = self.data["category"] if kind == "image" or not ephemeral else "disposable"
        result = {PROJECT: "ragflow", RUN: self.data["run_id"], OBJECT: uuid.uuid4().hex, CLASS: category}
        intent = {"object": result[OBJECT], "kind": kind, "category": category, "at": utc()}
        self.data.setdefault("creation_intents", []).append(intent)
        if self.attached:
            event(self.store, self.data["run_id"], creation_intent=intent)
        else:
            self.persist()
        return result

    def add(self, resource: dict):
        with self.mutex:
            previous = {r["key"]: r for r in self.data["resources"]}
            previous[resource["key"]] = {**previous.get(resource["key"], {}), **resource}
            self.data["resources"] = list(previous.values())
            if self.attached:
                event(self.store, self.data["run_id"], resource=resource)
            else:
                self.persist()

    def capture(self):
        merge_events(self.store, self.data)
        if self.data["engine"]:
            for resource in self.engine().owned(self.data["run_id"]):
                self.add(resource)
            for intent in self.data.get("legacy_images", []):
                from tools.quality.artifact_docker import labels, resource

                for info in self.engine().all("image"):
                    if labels(info, "image").get(intent["label"]) == intent["value"]:
                        target = resource(info, "image")
                        target.update(legacy_owner=intent, token=intent["value"], category="disposable")
                        self.add(target)

    def legacy_image(self, label: str, value: str):
        if label != "org.ragflow.ci-dry-run" or not re.fullmatch(r"[0-9a-f]{32}", value):
            raise ValueError("Unsupported legacy owner proof")
        self.engine()
        item = {"label": label, "value": value}
        if item not in self.data.setdefault("legacy_images", []):
            self.data["legacy_images"].append(item)
            self.persist()

    def alias(self, source: str, name: str):
        engine = self.engine()
        existing = engine.inspect("image", name)
        original = engine.inspect("image", source)
        if original is None or existing is not None:
            raise ValueError("Fresh image alias required")
        # A sealed creation intent accounts for tags whose source image is not owned.
        self.add(
            {
                "key": "image-alias:" + name,
                "kind": "image-alias",
                "name": name,
                "identity": original["Id"],
                "original_tags": original.get("RepoTags") or [],
                "category": "disposable",
                "status": "present",
            }
        )

    def path(self, path: Path, category: str = "evidence"):
        if category not in CATEGORIES:
            raise ValueError("Unknown artifact class")
        path = path.absolute()
        unlinked(path)
        if path.exists():
            info = path.stat()
            identity = [info.st_dev, info.st_ino, getattr(info, "st_birthtime_ns", 0)]
            entry = {
                "key": "path:" + checksum({"path": str(path), "identity": identity}),
                "kind": "path",
                "path": str(path),
                "identity": identity,
                "category": category,
                "status": "present",
                **({"content_sha256": file_checksum(path)} if path.is_file() else {}),
                **({"manifest_sha256": file_checksum(path / "manifest.json")} if path.is_dir() and (path / "manifest.json").is_file() else {}),
            }
            self.add(entry)
            if category in PRESERVED_OUTPUT_CLASSES:
                from tools.quality.artifact_paths import preserve_evidence

                if preserved := preserve_evidence(path, self.store):
                    self.add({**entry, "preserved_evidence": preserved})
        else:
            intent = {"kind": "path", "path": str(path), "category": category}
            if not any(all(value.get(key) == item for key, item in intent.items()) for value in self.data.get("creation_intents", [])):
                if self.attached:
                    event(self.store, self.data["run_id"], creation_intent=intent)
                else:
                    self.data.setdefault("creation_intents", []).append(intent)
                    self.persist()

    def use_image(self, reference: str):
        with self.mutex, mutation(self.store):
            self.resume_consumption()
            info = self.engine().inspect("image", reference)
            if info:
                dependency = {"image_id": info["Id"], "reference": reference}
                if dependency not in self.data.setdefault("image_dependencies", []):
                    self.data["image_dependencies"].append(dependency)
                    if self.attached:
                        event(self.store, self.data["run_id"], image_dependency=dependency)
                    else:
                        self.persist()

    def resume_consumption(self):
        self.data["usage_epoch"] = uuid.uuid4().hex
        invalidate_consumption(self.data)
        if self.attached:
            event(self.store, self.data["run_id"], producer_activity=self.data["usage_epoch"])
        else:
            self.persist()

    def close_consumption(self, kind, *, exit_code, cleanup, receipt=None):
        """Close this owner's verified QA use of inputs, not its product task."""
        if self.attached or _current.get() is not self or self.failed or self.preserve:
            raise ValueError("Only the active successful QA owner can close input consumption")
        with self.mutex, mutation(self.store):
            self.capture()
            self.data["consumption_closed"] = consumption_proof(self.data, kind, exit_code=exit_code, cleanup=cleanup, receipt=receipt, store=self.store)
            self.persist()

    def receipt(self, path: Path):
        unlinked(path)
        entry = {"path": str(path.absolute()), "sha256": file_checksum(path)}
        with self.mutex:
            self.data["receipts"] = [r for r in self.data["receipts"] if r["path"] != entry["path"]] + [entry]
            if self.attached:
                event(self.store, self.data["run_id"], receipt=entry)
            else:
                self.persist()

    def retire_owned(self):
        """Owning QA teardown may stop only its own labeled containers, then archive."""
        engine = self.engine()
        self.capture()
        engine.stop_owned(self.data["run_id"])
        self.capture()
        return retire(self.store, self.data["run_id"], engine, owner=self)

    def request_teardown(self, evidence: Path):
        """Delegate stopped live-QA Docker teardown if this owner is interrupted."""
        if self.attached or _current.get() is not self or self.data["producer"] != "live-regression-qa" or self.data["category"] != "disposable":
            raise ValueError("Only the active live-QA owner can delegate teardown")
        from tools.quality.artifact_paths import identity

        evidence = evidence.absolute()
        unlinked(evidence)
        if not evidence.is_dir():
            raise ValueError("QA teardown requires an existing evidence directory")
        self.path(evidence)
        self.data["teardown_request"] = {
            "schema": 1,
            "run_id": self.data["run_id"],
            "source": deepcopy(self.data["source"]),
            "engine": self.engine().identity,
            "recovery_epoch": self.data.get("recovery_epoch", 0),
            "evidence": str(evidence),
            "evidence_identity": identity(evidence),
        }
        self.persist()


def managed(producer: str, *, category: str = "disposable", days: int = 7):
    def decorate(function):
        @wraps(function)
        def execute(*args, **kwargs):
            with Run(producer, category=category, days=days) as run:
                result = function(*args, **kwargs)
                if (isinstance(result, int) and result) or (isinstance(result, dict) and result.get("status") in {"FAIL", "INCOMPLETE"}):
                    run.failed = True
                return result

        return execute

    return decorate


def image_has_user(store, run, resource):
    if resource["kind"] not in {"image", "image-alias"}:
        return False
    return any(
        other["run_id"] != run["run_id"]
        and other.get("engine") == run["engine"]
        and eligible(other, datetime.now(timezone.utc)) is not None
        and not consumption_closed(other)
        and (
            other.get("unresolved_image_dependencies")
            or any(
                dependency["image_id"] == resource["identity"] and (resource["kind"] == "image" or dependency["reference"] == resource["name"]) for dependency in other.get("image_dependencies", [])
            )
        )
        for other in runs(store)
    )


def consumption_subject(run):
    fields = ("key", "kind", "identity", "token", "config_sha256")
    helpers = {item["object"] for item in run.get("creation_intents", []) if item.get("purpose") in {"volume-archive-pin", "container-snapshot", "restore-probe"}}
    return {
        "run_id": run["run_id"],
        "source": run.get("source"),
        "engine": run.get("engine"),
        "ci": run.get("ci"),
        "usage_epoch": run.get("usage_epoch"),
        "recovery_epoch": run.get("recovery_epoch", 0),
        "images": run.get("image_dependencies", []),
        "unresolved_images": bool(run.get("unresolved_image_dependencies")),
        "generations": [{key: item.get(key) for key in fields} for item in sorted(run["resources"], key=lambda item: item["key"]) if item["kind"] != "path" and item.get("token") not in helpers],
    }


def consumer_kind_allowed(run, kind):
    if not isinstance(kind, str):
        return False
    allowed = kind == run["producer"] and kind in {"pytest-disposable", "blue-green-qa", "live-regression-qa", "blue-green-preflight"}
    return allowed or kind in {"ci-engine", "handoff-qa"} and bool(run.get("handoff"))


def consumption_proof(run, kind, *, exit_code, cleanup, receipt=None, store=None, expected_receipt_sha=None):
    if (
        not consumer_kind_allowed(run, kind)
        or type(exit_code) is not int
        or exit_code != 0
        or not isinstance(cleanup, dict)
        or cleanup.get("run_id") != run["run_id"]
        or cleanup.get("errors") != []
        or cleanup.get("blocked") != []
    ):
        raise ValueError("Input consumption requires this generation's successful QA and teardown")
    if any(item["kind"] != "path" and item.get("status") != "removed" for item in run["resources"]):
        raise ValueError("Input consumption still has live or unretired resources")
    proof = {"schema": 1, "kind": kind, "subject": deepcopy(consumption_subject(run)), "exit_code": 0, "cleanup": "PASS", "at": utc()}
    if receipt is not None:
        from tools.quality import artifact_blobs
        from tools.quality.artifact_docker import archive_root

        if store is None or _mutating.get() != store:
            raise ValueError("QA witness publication requires the lifecycle mutation boundary")
        unlinked(receipt)
        before = receipt.stat()
        with receipt.open("rb") as stream:
            raw = stream.read(4 * 1024 * 1024 + 1)
        after = receipt.stat()
        if len(raw) > 4 * 1024 * 1024 or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            raise ValueError("QA witness changed or exceeded its limit")
        if expected_receipt_sha is not None and hashlib.sha256(raw).hexdigest() != expected_receipt_sha:
            raise ValueError("QA witness changed after validation")
        root = archive_root(store)
        blob = artifact_blobs.put(BytesIO(raw), root)
        proof["receipt"] = {
            "path": str(artifact_blobs.path(root, blob)),
            "sha256": blob["sha256"],
            "bytes": blob["bytes"],
            "kind": "qa-witness",
            "source_sha256": blob["raw_sha256"],
            "source_path": str(receipt.absolute()),
        }
    return proof


def consumption_closed(run):
    proof = run.get("consumption_closed")
    if run["state"] != "completed" or not isinstance(proof, dict) or proof.get("subject") != consumption_subject(run) or not consumer_kind_allowed(run, proof.get("kind")):
        return False
    if type(proof.get("exit_code")) is not int or proof["exit_code"] != 0 or any(item["kind"] != "path" and item.get("status") != "removed" for item in run["resources"]):
        return False
    try:
        if receipt := proof.get("receipt"):
            from tools.quality.artifact_docker import verify_archive

            verify_archive(receipt)
    except (OSError, ValueError, KeyError, TypeError):
        return False
    return proof.get("schema") == 1 and proof.get("cleanup") == "PASS"


def qa_success_report(path, run):
    unlinked(path)
    with path.open("rb") as stream:
        raw = stream.read(4 * 1024 * 1024 + 1)
    if len(raw) > 4 * 1024 * 1024:
        raise ValueError("QA acceptance report exceeds its limit")
    value = json.loads(raw)
    kind = "ci-engine" if run["producer"].startswith("ci-engine:") else "handoff-qa"
    if not isinstance(value, dict) or value.get("schema") != 1 or value.get("project") != "ragflow" or value.get("kind") != kind or value.get("status") != "PASS":
        raise ValueError("Invalid QA acceptance report")
    if value.get("artifact_run_id") != run["run_id"] or value.get("artifact_subject") != consumption_subject(run) or value.get("sha") != (run.get("source") or {}).get("head"):
        raise ValueError("QA acceptance belongs to another generation or source")
    if kind == "ci-engine" and (not run.get("ci") or value.get("ci") != run["ci"]):
        raise ValueError("QA acceptance belongs to another CI attempt")
    return kind, hashlib.sha256(raw).hexdigest()


def compose_action(args):
    position = 2
    valued = {"-f", "--file", "-p", "--project-name", "--project-directory", "--env-file", "--profile", "--parallel", "--progress", "--ansi"}
    flags = {"--compatibility", "--dry-run", "--all-resources", "--help"}
    while position < len(args):
        value = args[position]
        if not value.startswith("-"):
            return position
        if value in valued:
            if position + 1 == len(args):
                raise ValueError("Missing managed Compose option value")
            position += 2
        elif value in flags or value.startswith("--") and "=" in value:
            position += 1
        else:
            raise ValueError("Unsupported managed Compose global option")
    return None


def command_labels(args):
    result = {}
    for index, value in enumerate(args):
        if value in {"--label", "--change"} and index + 1 < len(args):
            label = args[index + 1]
            if value == "--change":
                if not label.startswith("LABEL "):
                    continue
                label = label[6:]
        elif value.startswith("--label="):
            label = value[8:]
        else:
            continue
        if "=" in label:
            key, value = label.split("=", 1)
            result[key] = value
    return result


def docker_args(arguments, *, cwd=None):
    """Annotate creation only; never intercept runtime operations or force deletion."""
    if isinstance(arguments, (str, bytes)):
        return arguments
    args = list(arguments)
    privilege = args[:2] if args[:2] == ["sudo", "-n"] else []
    if privilege:
        args = args[2:]
    if not args or Path(args[0]).stem.casefold() != "docker":
        return arguments
    run = current()
    if not run:
        return arguments
    from tools.quality.artifact_docker import _transport

    if _transport.get():
        return arguments
    if len(args) < 2:
        return arguments
    if args[1] in {"context", "info"}:
        if args[1] == "context" and args[2:3] not in (["show"], ["ls"], ["inspect"]):
            raise ValueError("Managed QA cannot change Docker contexts")
        return arguments
    engine = run.engine()
    privilege = privilege or getattr(engine, "privilege", [])
    configuration = list(getattr(engine, "configuration", []))
    while len(args) > 2 and args[1] in {"--config", "--context", "--host"}:
        if args[1] == "--config":
            configuration = args[1:3]
        elif args[1:3] != engine.prefix[1:3]:
            raise ValueError("Managed QA cannot switch Docker transport")
        args = ["docker", *args[3:]]
    action = compose_action(args) if args[1:2] == ["compose"] else None
    read_only = args[1] in {"ps", "inspect", "logs", "port", "top", "stats", "version"} or args[2:3] in (["ls"], ["inspect"])
    if action is not None:
        read_only = args[action] in {"logs", "ps", "config", "version", "images", "top", "port", "ls", "events"}
    if not read_only:
        run.resume_consumption()
    if args[1:2] == ["compose"]:
        actions = {"up", "create", "build", "down", "run"}
        action = action if action is not None and args[action] in actions else None
        if action is not None and args[action] == "down":
            result = run.retire_owned()
            if result["errors"] or result["blocked"]:
                raise RuntimeError("Managed Compose retirement incomplete")
            return [sys.executable, "-B", "-c", "pass"]
        if action is not None:
            if not any(part in {"-f", "--file"} for part in args[2:action]):
                raise ValueError("Managed Compose creation requires an explicit -f configuration")
            command = [*privilege, *engine.prefix, *configuration, *args[1:action], "config", "--format", "json"]
            config = json.loads(subprocess.check_output(command, cwd=cwd, env=engine.env, timeout=120))
            for service in config.get("services", {}).values():
                if service.get("image"):
                    run.use_image(service["image"])
            overlay = {
                section: {name: {"labels": run.labels(kind)} for name, value in (config.get(section) or {}).items() if not (value or {}).get("external")}
                for section, kind in (("services", "container"), ("volumes", "volume"), ("networks", "network"))
            }
            overlay_file = run.store / "compose" / run.data["run_id"] / (uuid.uuid4().hex + ".json")
            save(overlay, overlay_file)
            run.path(overlay_file, "disposable")
            target = next(entry for entry in run.data["resources"] if entry.get("path") == str(overlay_file.absolute()))
            run.add({**target, "generated_by": "compose-overlay", "recovery": {"kind": "rebuildable-compose-overlay", "content": overlay}})
            args[action:action] = ["-f", str(overlay_file)]
    removal = 2 if args[1:2] in (["rm"], ["rmi"]) else (3 if args[2:3] == ["rm"] else None)
    if removal:
        kind = ("image" if args[1] == "rmi" else "container") if removal == 2 else args[1]
        force = any(value in {"-f", "--force", "-fv", "-vf"} for value in args[removal:])
        targets = [value for value in args[removal:] if not value.startswith("-")]
        for identifier in targets:
            info = engine.inspect(kind, identifier)
            if info is None:
                continue
            from tools.quality.artifact_docker import labels, resource

            alias = next((entry for entry in run.data["resources"] if entry["kind"] == "image-alias" and entry["name"] == identifier), None) if kind == "image" else None
            if not alias and (labels(info, kind).get(RUN) != run.data["run_id"] or labels(info, kind).get(PROJECT) != "ragflow" or not labels(info, kind).get(OBJECT)):
                raise ValueError("Managed QA cannot remove a foreign resource")
            if not alias and labels(info, kind).get(CLASS) not in {"disposable", "cache"}:
                raise ValueError("Managed QA cannot remove a persistent resource")
            if kind == "container" and info["State"]["Running"] and force:
                engine.run("container", "stop", "--time", "30", info["Id"])
                info = engine.inspect(kind, identifier)
            target = alias or resource(info, kind)
            errors = []
            pins = receipt_pins([ROOT], errors)
            if errors or engine.guard(target, run.data["run_id"], pins, set()):
                raise ValueError("Managed QA removal is protected")
            target["recovery"] = engine.backup(target, run.store)
            target["stage"] = "archived"
            run.add(target)
            engine.verify_backup(target)
            with mutation(run.store):
                errors = []
                pins = receipt_pins([ROOT], errors)
                if errors or image_has_user(run.store, run.data, target) or engine.guard(target, run.data["run_id"], pins, set()):
                    raise ValueError("Late managed QA removal protection")
                engine.verify_recovery(target["recovery"])
                engine.remove(target)
                run.add({**target, "status": "removed", "stage": "removed", "removed_at": utc()})
        return [sys.executable, "-B", "-c", "pass"]
    if args[1:2] in (["system"], ["image"], ["volume"], ["network"], ["builder"], ["buildx"], ["container"]) and args[2:3] == ["prune"]:
        raise ValueError("Global prune is forbidden in managed QA")
    if args[1:3] == ["image", "tag"] or args[1:2] == ["tag"]:
        run.alias(args[-2], args[-1])
    if args[1:3] == ["image", "inspect"]:
        position = 3
        while position < len(args):
            if args[position] in {"--format", "-f"}:
                position += 2
            elif args[position].startswith("-"):
                position += 1
            else:
                run.use_image(args[position])
                position += 1
    verbs = args[1:3]
    offset, kind = (2, "container") if args[1:2] in (["run"], ["create"]) else (None, None)
    if args[1:2] in (["build"], ["import"]):
        offset, kind = 2, "image"
    if len(verbs) == 2 and verbs[1] in {"create", "build", "import"} and verbs[0] in {"container", "volume", "network", "image"}:
        offset, kind = 3, verbs[0]
    if offset:
        if kind == "image" and "build" in verbs:
            from tools.quality.artifact_docker import base_references

            context = Path(args[-1])
            recipe = next((Path(args[i + 1]) for i, value in enumerate(args[:-1]) if value in {"-f", "--file"}), context / "Dockerfile")
            overrides = dict(args[i + 1].split("=", 1) for i, value in enumerate(args[:-1]) if value == "--build-arg" and "=" in args[i + 1])
            if recipe.is_file():
                for reference in base_references(recipe, overrides):
                    if "$" not in reference and reference != "scratch":
                        run.use_image(reference)
            else:
                run.data["unresolved_image_dependencies"] = True
                if not run.attached:
                    run.persist()
        elif kind == "container":
            boolean = {"--rm", "--detach", "-d", "--read-only", "--privileged", "--init", "--tty", "-t", "--interactive", "-i", "--publish-all", "-P", "--no-healthcheck"}
            position = offset
            while position < len(args):
                value = args[position]
                if not value.startswith("-"):
                    run.use_image(value)
                    break
                position += 1 if value in boolean or "=" in value or value in {"-it", "-ti"} else 2
        if kind == "volume" and args[-1] not in {"create", "local"} and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]+", args[-1]):
            existing = engine.inspect("volume", args[-1])
            if existing and (existing.get("Labels") or {}).get(RUN) != run.data["run_id"]:
                raise ValueError("Managed QA cannot adopt an existing volume by name")
        supplied = command_labels(args)
        if any(key in supplied for key in (PROJECT, RUN, OBJECT, CLASS)):
            merge_events(run.store, run.data)
            if (
                supplied.get(PROJECT) != "ragflow"
                or supplied.get(RUN) != run.data["run_id"]
                or not any(item.get("object") == supplied.get(OBJECT) and item.get("kind") == kind and item.get("category") == supplied.get(CLASS) for item in run.data.get("creation_intents", []))
            ):
                raise ValueError("Lifecycle creation labels require this owner's sealed intent")
            return [*privilege, *engine.prefix, *configuration, *args[1:]]
        labels = run.labels(kind)
        importing = "import" in verbs
        args[offset:offset] = [part for key, value in labels.items() for part in (("--change", f"LABEL {key}={value}") if importing else ("--label", f"{key}={value}"))]
    return [*privilege, *engine.prefix, *configuration, *args[1:]]


def annotate_compose(document: dict) -> dict:
    run = current()
    if run:
        for section, kind in (("services", "container"), ("volumes", "volume"), ("networks", "network")):
            entries = document.setdefault(section, {})
            if section == "networks":
                entries.setdefault("default", {})
            for key, value in list(entries.items()):
                value = value or {}
                entries[key] = value
                if not value.get("external"):
                    labels = value.get("labels") or {}
                    if isinstance(labels, list):
                        labels = dict(v.split("=", 1) for v in labels)
                    value["labels"] = {**labels, **run.labels(kind)}
    return document


def note(path: Path, *, receipt: bool = False, category: str = "evidence"):
    run = current()
    if run:
        run.path(path, category)
        if receipt:
            run.receipt(path)


def note_all(entries, *, primary=None):
    """Record independent outputs; only an explicitly escaping failure has priority."""
    errors = []
    for path, category, receipt in entries:
        try:
            note(path, category=category, receipt=receipt)
        except Exception as error:
            errors.append(error)
    if errors:
        if primary is not None and not (isinstance(primary, SystemExit) and (primary.code is None or primary.code == 0)):
            primary.add_note("Artifact output accounting incomplete: " + ", ".join(type(error).__name__ for error in errors))
        else:
            raise ExceptionGroup("Artifact output accounting incomplete", [RuntimeError(type(error).__name__) for error in errors])


def workspace_reserved(path: Path):
    """A manual task may outlive its reserving process; .keep remains its authority."""
    run = current()
    if run:
        run.path(path, "disposable")
        return
    identity = [path.stat().st_dev, path.stat().st_ino, getattr(path.stat(), "st_birthtime_ns", 0)]
    for previous in runs(store_path()):
        if any(r.get("path") == str(path.absolute()) and r["identity"] == identity and r["status"] == "present" for r in previous["resources"]):
            return
    run = Run("workspace:" + path.name)
    run.data["state"] = "waiting"
    run.path(path, "disposable")


def workspace_retired(path: Path, identity: list):
    for run in runs(store_path()):
        changed = False
        for entry in run["resources"]:
            if entry.get("path") == str(path.absolute()) and entry["identity"] == identity:
                entry.update(status="removed", removed_at=utc())
                changed = True
        if changed:
            if run["producer"].startswith("workspace:"):
                run.update(state="completed", task_status="completed", completed_at=utc())
            write_run(store_path(), run)
            if active := _live.get(run["run_id"]):
                active.data = run


def eligible(run: dict, now: datetime) -> str | None:
    if run["state"] != "completed":
        return "run-not-completed"
    evidence = run.get("completion_evidence")
    task_status = run.get("task_status", evidence.get("task_status") if isinstance(evidence, dict) else None)
    if task_status == "open":
        return "task-acceptance-open"
    if not isinstance(task_status, str) or task_status not in {"completed", "superseded"}:
        return "task-acceptance-unconfirmed"
    try:
        completed = datetime.fromisoformat(run["completed_at"])
        days = run["keep_days"]
        if completed.tzinfo is None or type(days) is not int or days < 0:
            return "invalid-retention"
        age = (now - completed).total_seconds()
    except (KeyError, ValueError, TypeError):
        return "invalid-retention"
    if age < days * 86400:
        return "retention"
    return None


def run_subject(run: dict) -> dict:
    """A public generation subject; configuration secrets and capabilities stay private."""
    fields = ("key", "kind", "identity", "token", "category", "path", "data_scope", "validation", "content_sha256", "manifest_sha256", "config_sha256", "status", "depends_on", "retained_by")
    return {
        "run_id": run["run_id"],
        "task_ref": run["task_ref"],
        "source": run.get("source"),
        "engine": run.get("engine"),
        "task_status": run.get("task_status"),
        "usage_epoch": run.get("usage_epoch"),
        "recovery_epoch": run.get("recovery_epoch", 0),
        "history_sha256": checksum({"history": run.get("invalidated_releases", [])}),
        "dependencies_sha256": checksum({"images": run.get("image_dependencies", []), "recovery": run.get("recovery_dependencies", [])}),
        "inputs_sha256": checksum({"inputs": run.get("input_files", [])}),
        "observations_sha256": checksum({"observations": run.get("runtime_observations", [])}),
        "consumption_sha256": checksum({"current": run.get("consumption_closed"), "history": run.get("consumption_history", [])}),
        **({"teardown_sha256": checksum(run["teardown_request"])} if run.get("teardown_request") else {}),
        "resources": [
            {**{key: item.get(key) for key in fields}, "recovery_sha256": checksum(item.get("recovery") or {}), "preserved_sha256": checksum(item.get("preserved_evidence") or {})}
            for item in sorted(run["resources"], key=lambda item: item["key"])
        ],
    }


def invalidate_release(run: dict, reason: str):
    """Revoke recovery expiry approval while retaining its referenced packets."""
    if run.get("recovery_released_at"):
        run.setdefault("invalidated_releases", []).append(
            {
                "reason": reason,
                "released_at": run["recovery_released_at"],
                "evidence": run.get("release_evidence"),
                "packets": deepcopy(
                    [*[item.get(key) for item in run["resources"] for key in ("recovery", "preserved_evidence")], *[item.get("preserved_evidence") for item in run.get("input_files", [])]]
                ),
                "expiry_files": run.get("expiry_files", {}),
            }
        )
    for key in ("recovery_released_at", "release_evidence", "expiry_files", "expiry_subject"):
        run.pop(key, None)


def operator_evidence(path: Path, run: dict, action: str) -> dict:
    unlinked(path)
    with path.open("rb") as stream:
        raw = stream.read(4 * 1024 * 1024 + 1)
    if len(raw) > 4 * 1024 * 1024:
        raise ValueError("Lifecycle decision exceeds 4 MiB")
    proof = json.loads(raw)
    if not isinstance(proof, dict) or proof.get("schema") != 1 or proof.get("project") != "ragflow" or proof.get("action") != action:
        raise ValueError("A structured lifecycle decision for this action is required")
    previous = run.get("completion_evidence" if action == "complete" else "release_evidence", {})
    repeated = run.get("state") == "completed" and previous.get("sha256") == hashlib.sha256(raw).hexdigest() and previous.get("applied_subject_sha256") == checksum(run_subject(run))
    if (proof.get("subject") != run_subject(run) and not repeated) or proof.get("execution_status") != "completed":
        raise ValueError("Lifecycle decision must match the exact run, source and resource generations")
    if not isinstance(proof.get("approved_by"), str) or not proof["approved_by"].strip() or proof.get("task_status") not in {"open", "completed", "superseded"}:
        raise ValueError("Lifecycle decision requires an explicit custodian and task status")
    if action == "release" and (proof["task_status"] == "open" or proof.get("recovery_needed") is not False):
        raise ValueError("Recovery release requires a closed task and explicit recovery release")
    return {"path": str(path.absolute()), "sha256": hashlib.sha256(raw).hexdigest(), "approved_by": proof["approved_by"], "task_status": proof["task_status"]}


def teardown_requested(run: dict) -> bool:
    request = run.get("teardown_request")
    return (
        isinstance(request, dict)
        and not request.get("finished_at")
        and request.get("schema") == 1
        and run["producer"] == "live-regression-qa"
        and run["category"] == "disposable"
        and request.get("run_id") == run["run_id"]
        and request.get("source") == run.get("source")
        and bool(request.get("engine"))
        and request["engine"] == run.get("engine")
        and request.get("recovery_epoch") == run.get("recovery_epoch", 0)
        and bool(request.get("evidence"))
        and isinstance(request.get("evidence_identity"), list)
    )


def teardown_evidence(run: dict) -> Path:
    from tools.quality.artifact_paths import identity

    evidence = Path(run["teardown_request"]["evidence"])
    unlinked(evidence)
    if not evidence.is_dir() or identity(evidence) != run["teardown_request"]["evidence_identity"]:
        raise ValueError("Delegated QA evidence generation changed or is missing")
    return evidence


def retire(store: Path, run_id: str, engine=None, *, owner: Run | None = None, resume_teardown: bool = False) -> dict:
    from tools.quality.artifact_docker import Engine

    def perform():
        run = owner.data if owner else read_run(store, run_id)
        merge_events(store, run)
        if resume_teardown:
            if owner or not teardown_requested(run):
                raise ValueError("Missing live-QA teardown delegation")
            from tools.quality.artifact_paths import preserve_evidence

            request = run["teardown_request"]
            evidence = teardown_evidence(run)
            request["preserved_evidence"] = preserve_evidence(evidence, store)
            for entry in run["resources"]:
                if entry.get("path") == str(evidence) and entry.get("identity") == request["evidence_identity"]:
                    entry["preserved_evidence"] = request["preserved_evidence"]
            write_run(store, run)

        def checkpoint_run():
            if owner and owner.attached:
                for entry in run["resources"]:
                    event(store, run_id, resource=entry)
            else:
                write_run(store, run)

        engine_client = engine or Engine(expected=run["engine"])
        existing = {entry["key"]: entry for entry in run["resources"]}
        reappeared = []
        for entry in engine_client.owned(run_id):
            # Discovery closes create-before-journal gaps; it must not rewrite the
            # sealed generation/configuration of an already recorded resource.
            if entry["key"] not in existing:
                existing[entry["key"]] = entry
            elif existing[entry["key"]].get("status") == "removed":
                reappeared.append(entry["key"])
        run["resources"] = list(existing.values())
        checkpoint_run()
        result = {"run_id": run_id, "removed": [], "blocked": [], "retained": [], "errors": []}
        result["blocked"].extend({"key": key, "reason": "retired-generation-reappeared"} for key in reappeared)
        if not owner and not resume_teardown and (reason := eligible(run, datetime.now(timezone.utc))):
            result["blocked"].append(reason)
            return result
        errors = []
        pins = receipt_pins([ROOT], errors)
        if errors:
            raise ValueError("Cannot verify image receipt pins")
        removable = {r["identity"] for r in run["resources"] if r["kind"] == "container" and r.get("category") == "disposable"}
        order = {"container": 0, "volume": 1, "network": 2, "image-alias": 3, "image": 4, "path": 5}
        for resource in sorted(run["resources"], key=lambda r: order[r["kind"]]):
            if resource.get("status") == "removed":
                continue
            if resume_teardown and (resource["kind"] not in {"container", "volume", "network"} or resource.get("category") != "disposable"):
                result["retained"].append({"key": resource["key"], "reason": "outside-delegated-qa-teardown"})
                continue
            if resource.get("category") not in {"disposable", "cache"}:
                from tools.quality.artifact_policy import POLICIES

                conditions = POLICIES.get(resource.get("category"), POLICIES["unknown"])[3]
                result["retained"].append({"key": resource["key"], "reason": "retirement-conditions-unverified", "conditions": conditions})
                continue
            try:
                if resource["kind"] == "path":
                    from tools.quality import artifact_paths

                    if resource.get("generated_by") == "compose-overlay":
                        path = Path(resource["path"])
                        expected = store / "compose" / run_id
                        unlinked(path)
                        if path.parent != expected or not re.fullmatch(r"[0-9a-f]{32}\.json", path.name):
                            raise ValueError("Invalid owned Compose metadata path")
                        with mutation(store):
                            if path.exists():
                                if artifact_paths.identity(path) != resource["identity"] or file_checksum(path) != resource["content_sha256"]:
                                    raise ValueError("Compose metadata generation changed")
                                path.unlink()
                            resource.update(status="removed", removed_at=utc(), stage="removed")
                            checkpoint_run()
                            result["removed"].append(resource["key"])
                        continue
                    if not Path(resource["path"]).exists():
                        resource.update(status="removed", removed_at=utc(), stage="absence-confirmed")
                        checkpoint_run()
                        continue
                    if not artifact_paths.cache_kind(Path(resource["path"])):
                        result["retained"].append({"key": resource["key"], "reason": "owning-path-cleaner-or-persistent-policy"})
                        continue

                    def checkpoint(packet):
                        resource.update(recovery=packet, stage="archived")
                        checkpoint_run()

                    outcome = artifact_paths.retire(resource, run, runs(store), engine_client, store, checkpoint=checkpoint)
                    if outcome.get("blocked") not in {None, "already-absent"}:
                        result["blocked"].append({"key": resource["key"], "reason": outcome["blocked"]})
                    else:
                        resource.update(status="removed", removed_at=utc(), stage="removed")
                        checkpoint_run()
                        result["removed"].append(resource["key"])
                    continue
                reason = engine_client.guard(resource, run_id, pins, removable)
                if reason == "already-absent":
                    resource.update(status="removed", removed_at=utc(), stage="absence-confirmed")
                    checkpoint_run()
                    continue
                if reason:
                    result["blocked"].append({"key": resource["key"], "reason": reason})
                    continue
                resource["recovery"] = engine_client.backup(resource, store)
                resource["stage"] = "archived"
                checkpoint_run()
                engine_client.verify_backup(resource)
                # Late consumer/engine/identity changes invalidate this exact operation.
                # Serialize final dependency publication against managed producers.
                with mutation(store):
                    late_errors = []
                    fresh_pins = receipt_pins([ROOT], late_errors)
                    if late_errors:
                        raise ValueError("Cannot verify late image pins")
                    reason = "other-unclosed-image-user" if image_has_user(store, run, resource) else engine_client.guard(resource, run_id, fresh_pins, set())
                    if not reason:
                        engine_client.verify_recovery(resource["recovery"])
                        engine_client.remove(resource)
                if reason:
                    result["blocked"].append({"key": resource["key"], "reason": reason})
                    continue
                resource.update(status="removed", removed_at=utc(), stage="removed")
                checkpoint_run()
                result["removed"].append(resource["key"])
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
                resource["cleanup_error"] = type(error).__name__
                checkpoint_run()
                result["errors"].append({"key": resource["key"], "error": type(error).__name__})
        run["cleanup"] = {**result, "checked_at": utc()}
        if (owner or resume_teardown) and teardown_requested(run) and not result["blocked"] and not result["errors"]:
            if all(item.get("status") == "removed" for item in run["resources"] if item["kind"] in {"container", "volume", "network"} and item.get("category") == "disposable"):
                run["teardown_request"]["finished_at"] = utc()
        checkpoint_run()
        if owner:
            owner.data = run
        return result

    if owner:
        return perform()
    with lease(store, run_id):
        return perform()


def reconcile_ci(store: Path, run: dict) -> bool:
    """Close abandoned CI generations only from a matching completed GitHub attempt."""
    ci = run.get("ci")
    if not ci or run["state"] not in {"waiting", "failed", "running"}:
        return False
    repository, identifier, attempt = (ci.get(key, "") for key in ("GITHUB_REPOSITORY", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT"))
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repository or "") or not str(identifier).isdigit() or not str(attempt).isdigit():
        raise ValueError("Invalid CI owner identity")
    value = json.loads(subprocess.check_output(["gh", "api", f"repos/{repository}/actions/runs/{identifier}/attempts/{attempt}"], timeout=60))
    if value.get("status") != "completed":
        return False
    if str(value.get("id")) != str(identifier) or str(value.get("run_attempt")) != str(attempt) or value.get("head_sha") != ci.get("GITHUB_SHA"):
        raise ValueError("CI completion proof does not match source/attempt")
    with lease(store, run["run_id"]):
        from tools.quality.artifact_docker import Engine

        existing = {entry["key"]: entry for entry in run["resources"]}
        if run["engine"]:
            engine = Engine(expected=run["engine"])
            for entry in engine.owned(run["run_id"]):
                if entry["key"] not in existing:
                    existing[entry["key"]] = entry
        run.update(
            resources=list(existing.values()), state="completed", completed_at=utc(), completion_evidence={key: value.get(key) for key in ("id", "run_attempt", "head_sha", "status", "conclusion")}
        )
        write_run(store, run)
    return True


def sweep(store: Path, *, apply: bool = False) -> dict:
    result = {"checked_at": utc(), "apply": apply, "runs": [], "errors": []}
    for run in runs(store):
        if apply and run.get("ci") and run["state"] in {"waiting", "running", "failed"}:
            try:
                reconcile_ci(store, run)
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
                result["runs"].append({"run_id": run["run_id"], "blocked": ["ci-completion-unverified"]})
                continue
        resume_teardown = teardown_requested(run)
        reason = None if resume_teardown else eligible(run, datetime.now(timezone.utc))
        if reason or (not resume_teardown and not any(r.get("status") != "removed" for r in run["resources"])):
            result["runs"].append({"run_id": run["run_id"], "blocked": [reason or "already-retired"]})
            continue
        try:
            if apply:
                retired = retire(store, run["run_id"], resume_teardown=resume_teardown)
                retired["delegated_teardown"] = resume_teardown
                result["runs"].append(retired)
            else:
                from tools.quality.artifact_docker import Engine
                from tools.quality import artifact_paths

                with lease(store, run["run_id"]):
                    if resume_teardown:
                        teardown_evidence(run)
                    engine = Engine(expected=run["engine"])
                    errors = []
                    pins = receipt_pins([ROOT], errors)
                    if errors:
                        raise ValueError("Cannot verify image pins")
                    removable = {r["identity"] for r in run["resources"] if r["kind"] == "container" and r.get("category") == "disposable"}
                    targets = []
                    for resource in run["resources"]:
                        if resource.get("status") == "removed":
                            continue
                        if resume_teardown and (resource["kind"] not in {"container", "volume", "network"} or resource.get("category") != "disposable"):
                            reason = "outside-delegated-qa-teardown"
                        elif resource.get("category") not in {"disposable", "cache"}:
                            reason = "retirement-conditions-unverified"
                        elif resource["kind"] == "path":
                            reason = artifact_paths.guard(resource, run, runs(store), engine)
                        else:
                            reason = "other-unclosed-image-user" if image_has_user(store, run, resource) else engine.guard(resource, run["run_id"], pins, removable)
                        targets.append({"key": resource["key"], "kind": resource["kind"], "reason": reason, "candidate": reason is None})
                    result["runs"].append({"run_id": run["run_id"], "eligible": True, "targets": targets})
        except LeaseUnavailable:
            result["runs"].append({"run_id": run["run_id"], "blocked": ["run-lease-unavailable"]})
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            result["errors"].append({"run_id": run["run_id"], "error": type(error).__name__})
    save(result, store / "cleanup.json")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("cleanup", "gc", "release", "restore", "complete", "subject", "record", "register", "exec", "begin", "finish", "docker"))
    parser.add_argument("--state", type=Path, default=store_path())
    parser.add_argument("--run")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--producer", default="manual-qa")
    parser.add_argument("--category", choices=sorted(CATEGORIES), default="disposable")
    parser.add_argument("--task-ref")
    parser.add_argument("--purpose")
    parser.add_argument("--related-receipt", type=Path)
    parser.add_argument("--data-scope")
    parser.add_argument("--validation", choices=("unverified", "checksum-only", "format-checked", "incomplete"), default="unverified")
    parser.add_argument("--keep-days", type=int, default=7)
    arguments = sys.argv[1:]
    separator = arguments.index("--") if "--" in arguments else len(arguments)
    args = parser.parse_args(arguments[:separator])
    forwarded = arguments[separator + 1 :]
    if args.action == "register":
        if not args.evidence or not args.evidence.exists() or not args.task_ref or not args.purpose or (args.category == "backup" and not args.data_scope):
            parser.error("register requires an existing --evidence, --task-ref and --purpose")
        with Run(args.producer, store=args.state, category=args.category, days=args.keep_days) as run:
            run.data.update(task_ref=args.task_ref, purpose=args.purpose)
            run.path(args.evidence, args.category)
            run.data["resources"][0].update(data_scope=args.data_scope, validation=args.validation)
            run.failed = args.validation == "incomplete"
            if args.related_receipt:
                run.data["resources"][0]["retained_by"] = [str(args.related_receipt.absolute())]
                run.persist()
        print(json.dumps({"run_id": run.data["run_id"], "category": args.category, "sha256": run.data["resources"][0].get("content_sha256")}))
        return 0
    if args.action == "gc":
        from tools.quality.artifact_retention import collect

        result = collect(args.state, apply=args.apply)
        print(json.dumps({"removed": len(result["removed"]), "blocked": result["blocked"], "errors": result["errors"]}))
        return int(bool(result["errors"]))
    if args.action == "record":
        if current() is None or not args.evidence:
            parser.error("record requires an owner run and --evidence")
        note(args.evidence, category=args.category)
        return 0
    if args.action == "begin":
        with Run(args.producer, store=args.state, category=args.category, days=args.keep_days) as run:
            run.engine()
            run.preserve = True
            run.data["handoff"] = True
        run.data["state"] = "waiting"
        run.persist()
        print(json.dumps({"run_id": run.data["run_id"], "owner_token": run.owner_token, "state": str(args.state)}))
        return 0
    if args.action in {"docker", "finish"}:
        attached = current()
        if attached is None:
            parser.error("An owner run is required")
        if args.action == "finish" and not attached.data.get("handoff"):
            parser.error("Only a handed-off run can be finished by a child")
        context = lease(attached.store, attached.data["run_id"]) if attached.data.get("handoff") else nullcontext()
        with context:
            if attached.data.get("handoff"):
                attached.data = merge_events(attached.store, read_run(attached.store, attached.data["run_id"]))
                if attached.data["state"] != "waiting":
                    parser.error("The handoff generation is already closed")
            if args.action == "docker":
                try:
                    return subprocess.run(docker_args(forwarded), check=False).returncode
                finally:
                    attached.capture()
            acceptance = None
            verified_sha = None
            acceptance_error = None
            if args.evidence is not None:
                try:
                    acceptance, verified_sha = qa_success_report(args.evidence, attached.data)
                except (OSError, ValueError, KeyError, TypeError):
                    acceptance_error = "invalid-generation-bound-qa-report"
            result = attached.retire_owned()
            with mutation(attached.store):
                run = merge_events(attached.store, read_run(attached.store, attached.data["run_id"]))
                failed = bool(result["errors"] or result["blocked"] or acceptance_error)
                if acceptance and not failed:
                    try:
                        kind, final_sha = qa_success_report(args.evidence, run)
                        if final_sha != verified_sha:
                            raise ValueError("QA verification changed during teardown")
                        run["consumption_closed"] = consumption_proof(run, kind, exit_code=0, cleanup=result, receipt=args.evidence, store=attached.store, expected_receipt_sha=verified_sha)
                    except (OSError, ValueError, KeyError, TypeError):
                        acceptance_error = "qa-report-changed-during-teardown"
                        failed = True
                run.update(state="waiting" if failed else "completed", completed_at=utc(), cleanup=result)
                if acceptance_error:
                    run["qa_acceptance_error"] = acceptance_error
                else:
                    run.pop("qa_acceptance_error", None)
                write_run(attached.store, run)
            print(
                json.dumps(
                    {
                        "run_id": run["run_id"],
                        "removed": len(result["removed"]),
                        "errors": len(result["errors"]),
                        "blocked": len(result["blocked"]),
                        "qa_acceptance_error": acceptance_error,
                        "inputs_consumption": "closed" if consumption_closed(run) else "protected",
                    }
                )
            )
            return int(failed)
    if args.action == "cleanup":
        result = sweep(args.state, apply=args.apply)
        from tools.quality.sandbox_pool import cleanup as cleanup_sandbox_pool

        result["sandbox_pool"] = cleanup_sandbox_pool(args.state, apply=args.apply)
        save(result, args.state / "cleanup.json")
        pending = [r["run_id"] for r in result["runs"] if r.get("delegated_teardown") and r.get("blocked")]
        print(
            json.dumps(
                {
                    "report": str(args.state / "cleanup.json"),
                    "runs": len(result["runs"]),
                    "removed": sum(len(r.get("removed", [])) for r in result["runs"]),
                    "errors": len(result["errors"]) + sum(len(r.get("errors", [])) for r in result["runs"]),
                    "pending_teardowns": pending,
                    "sandbox_pool": result["sandbox_pool"],
                }
            )
        )
        return int(bool(result["errors"] or pending or result["sandbox_pool"]["errors"] or result["sandbox_pool"]["blocked"] or any(r.get("errors") for r in result["runs"])))
    if args.action == "exec":
        command = forwarded
        if not command:
            parser.error("exec requires -- COMMAND")
        if any("pytest" in str(part) for part in command[:4]):
            environment = {
                **os.environ,
                "RAGFLOW_ARTIFACT_STATE": str(args.state),
                "RAGFLOW_ARTIFACT_MANAGED_QA": "1",
                "PYTEST_PLUGINS": ",".join(filter(None, (os.environ.get("PYTEST_PLUGINS"), "tools.quality.pytest_artifacts"))),
            }
            return subprocess.run(command, env=environment, check=False).returncode
        with Run(args.producer, store=args.state, category=args.category, days=args.keep_days) as run:
            run.engine()
            result = subprocess.run(docker_args(command), check=False)
            if result.returncode:
                run.failed = True
            return result.returncode
    if not args.run:
        parser.error("--run is required")
    if args.action == "subject":
        print(json.dumps(run_subject(merge_events(args.state, read_run(args.state, args.run))), ensure_ascii=False, indent=2))
        return 0
    if args.action in {"complete", "release"}:
        if not args.evidence:
            parser.error("--evidence is required for explicit completion")
        unlinked(args.evidence)
        with lease(args.state, args.run), mutation(args.state):
            run = merge_events(args.state, read_run(args.state, args.run))
            try:
                evidence = operator_evidence(args.evidence, run, args.action)
            except (OSError, ValueError, KeyError, TypeError):
                parser.error("Invalid lifecycle decision: expected current subject, action, custodian and task/recovery status")
            evidence_key = "release_evidence" if args.action == "release" else "completion_evidence"
            previous = run.get(evidence_key, {})
            if run["state"] == "completed" and previous.get("sha256") == evidence["sha256"] and previous.get("applied_subject_sha256") == checksum(run_subject(run)):
                if previous["path"] != evidence["path"]:
                    previous["path"] = evidence["path"]
                    write_run(args.state, run)
                return 0
            if args.action == "release":
                if run["state"] != "completed" or any(r["kind"] != "path" and r.get("status") != "removed" for r in run["resources"]):
                    parser.error("Release requires a completed run with all Docker resources retired")
                run.update(recovery_released_at=utc(), release_evidence=evidence)
            else:
                invalidate_release(run, "generation-completed-again")
                run.update(state="completed", completed_at=utc(), completion_evidence=evidence)
            run["task_status"] = evidence["task_status"]
            evidence["applied_subject_sha256"] = checksum(run_subject(run))
            write_run(args.state, run)
        return 0
    from tools.quality.artifact_docker import restore

    with lease(args.state, args.run), Run("restore:" + args.run, store=args.state) as recovery:
        if read_run(args.state, args.run).get("recovery_expired_at"):
            parser.error("Recovery packet was explicitly released and expired")
        recovery.preserve = True
        restore(read_run(args.state, args.run), recovery)
    print(
        json.dumps(
            {
                "run_id": recovery.data["run_id"],
                "state": "paused",
                "containers_started": False,
                "rebuild_required": recovery.data.get("rebuild_required", []),
                "required_input_snapshots": recovery.data.get("required_input_snapshots", []),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
