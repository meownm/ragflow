"""Observe a persistent deployment without acquiring ownership of its services."""

from contextlib import ExitStack
from contextvars import ContextVar
import hashlib
import json
import sys

from tools.quality.artifact_docker import resource
from tools.quality.artifact_lifecycle import Run, docker_args
from tools.quality.artifact_paths import preserve_evidence
from tools.quality.artifact_registry import unlinked


_active = ContextVar("persistent_deployment_accounting", default=None)


def current_runtime():
    return _active.get()


def file_identity(path):
    unlinked(path)
    if not path.exists():
        return None
    if not path.is_file():
        raise ValueError("Controller metadata must be a regular file")
    stat = path.stat()
    with path.open("rb") as stream:
        checksum = hashlib.file_digest(stream, "sha256").hexdigest()
    return {"kind": "path", "path": str(path.absolute()), "identity": [stat.st_dev, stat.st_ino, getattr(stat, "st_birthtime_ns", 0)], "content_sha256": checksum}


class RuntimeAccounting:
    def __init__(self, project, root, *, lock, config=None, output=None):
        self.project = project
        self.lock = lock
        root = root.absolute()
        self.root = root
        self.run = Run("blue-green-runtime", category="runtime", days=30)
        self.outputs = {root / name: "config" for name in ("compose.json", "state.json", "pending.json", "nginx.conf")}
        if output is not None:
            self.outputs[output.absolute()] = "evidence"
        for path in list(self.outputs):
            if path.name != "deployment.lock":
                self.outputs[path.with_suffix(".tmp")] = "metadata"
        self.inputs = set(self.outputs)
        if config is not None:
            self.inputs.add(config.absolute())

    def __enter__(self):
        self.stack = ExitStack()
        self.stack.enter_context(self.run)
        try:
            self.run.data.update(runtime_project=self.project, purpose="Persistent blue-green configuration and runtime dependencies")
            self.engine = self.run.engine()
            self.before = {}
            self.captured_inputs = False
            for path, category in self.outputs.items():
                self.run.data.setdefault("creation_intents", []).append({"kind": "path", "path": str(path), "category": category})
            self.run.data["creation_intents"].append({"kind": "path", "path": str(self.root / "deployment.lock"), "category": "metadata", "purpose": "deployment-synchronization"})
            self.run.persist()
            self.stack.enter_context(self.lock)
            self.capture_inputs()
            self.token = _active.set(self)
            return self
        except BaseException:
            self.stack.__exit__(*sys.exc_info())
            raise

    def capture_inputs(self):
        """Take previous metadata snapshots only after acquiring deployment_lock."""
        for path in sorted(self.inputs):
            item = file_identity(path)
            self.before[path] = item
            if item:
                # Private recovery retains the previous config; its producer
                # is not reassigned to this observing deployment.
                item = {**item, "category": self.outputs.get(path, "config")}
                self.run.data.setdefault("input_files", []).append(item)
                self.run.persist()
                item["preserved_evidence"] = preserve_evidence(path, self.run.store, metadata_owner=self.run)
                if path.name == "state.json":
                    self.use_config_images(json.loads(path.read_bytes()).get("config", {}))
        self.observe()
        self.captured_inputs = True

    def read_config(self, path):
        data = path.read_bytes()
        expected = self.before[path.absolute()]
        if expected is None or hashlib.sha256(data).hexdigest() != expected["content_sha256"]:
            raise ValueError("Resolved deployment configuration changed after snapshot")
        return json.loads(data.decode("utf-8-sig"))

    def observe(self):
        self.engine.assert_engine()
        observations = {}
        lock_path = self.root / "deployment.lock"
        if lock_path.exists():
            stat = lock_path.stat()
            item = {
                "key": "path:lock:" + str(lock_path),
                "kind": "path",
                "path": str(lock_path),
                "identity": [stat.st_dev, stat.st_ino, getattr(stat, "st_birthtime_ns", 0)],
                "category": "metadata",
                "custodian": self.project,
                "ownership_source": "observation",
                "purpose": "OS deployment lock; recreated by deployment_lock",
            }
            observations[item["key"]] = item
        for container in self.engine.all("container"):
            labels = (container.get("Config") or {}).get("Labels") or {}
            if labels.get("com.docker.compose.project") != self.project and labels.get("org.ragflow.blue-green") != self.project:
                continue
            item = {
                **resource(container, "container"),
                "category": "runtime",
                "engine": self.engine.identity,
                "ownership_source": "observation",
                "custodian": self.project,
                "running": container["State"]["Running"],
            }
            observations[item["key"]] = item
            self.run.use_image(container["Image"])
            for mount in container.get("Mounts", []):
                if mount.get("Type") == "volume":
                    volume = self.engine.inspect("volume", mount["Name"])
                    if volume is None:
                        raise ValueError("Cannot observe a runtime volume generation")
                    item = {**resource(volume, "volume"), "category": "data", "engine": self.engine.identity, "ownership_source": "observation", "custodian": self.project}
                    observations[item["key"]] = item
            for name, network in (container.get("NetworkSettings") or {}).get("Networks", {}).items():
                info = self.engine.inspect("network", network.get("NetworkID") or name)
                if info is None:
                    raise ValueError("Cannot observe a runtime network generation")
                item = {**resource(info, "network"), "category": "runtime", "engine": self.engine.identity, "ownership_source": "observation", "custodian": self.project}
                observations[item["key"]] = item
        previous = {item["key"]: item for item in self.run.data.get("runtime_observations", [])}
        # Retain the pre-switch identities for fallback and interrupted recovery.
        self.run.data["runtime_observations"] = list({**previous, **observations}.values())
        self.run.persist()

    def use_config_images(self, config):
        for service in config.get("services", {}).values():
            if image := service.get("image"):
                self.run.use_image(image)

    def arguments(self, arguments):
        if arguments[0] != "docker":
            raise ValueError("Persistent deployment transport accepts Docker commands only")
        self.engine.assert_engine()
        return [*getattr(self.engine, "privilege", []), *self.engine.prefix, *getattr(self.engine, "configuration", []), *arguments[1:]], self.engine.env

    def probe(self, arguments, execute, **kwargs):
        # This command creates an ephemeral container. It is not a persistent
        # Compose service and must keep its own creation intent and ownership.
        self.engine.assert_engine()
        with Run("blue-green-preflight", store=self.run.store, days=0) as probe:
            probe._engine = self.engine
            probe.data.update(engine=self.engine.identity, parent_task_ref=self.run.data["task_ref"])
            probe.persist()
            result = execute(docker_args(arguments), env=self.engine.env, **kwargs)
            probe.failed = bool(result.returncode)
            if not probe.failed:
                probe.close_consumption("blue-green-preflight", exit_code=0, cleanup={"run_id": probe.data["run_id"], "errors": [], "blocked": []})
            return result

    def __exit__(self, exc_type, exc, traceback):
        errors = []
        try:
            for path, category in self.outputs.items() if self.captured_inputs else []:
                try:
                    after = file_identity(path)
                    if after is not None and after != self.before.get(path):
                        self.run.path(path, category)
                        entry = next(item for item in self.run.data["resources"] if item.get("path") == str(path) and item.get("identity") == after["identity"])
                        packet = entry.get("preserved_evidence") or preserve_evidence(path, self.run.store, metadata_owner=self.run)
                        self.run.add({**entry, "preserved_evidence": packet, "generated_by": "blue-green-controller"})
                except Exception as error:
                    errors.append(error)
            try:
                self.observe()
            except Exception as error:
                errors.append(error)
            if errors:
                self.run.failed = True
                self.run.data["observation_errors"] = [type(error).__name__ for error in errors]
                if exc is None or isinstance(exc, SystemExit) and (exc.code is None or exc.code == 0):
                    failure = RuntimeError("Persistent deployment accounting incomplete")
                    self.stack.__exit__(type(failure), failure, None)
                    raise failure from None
                exc.add_note("Persistent deployment accounting incomplete: " + ", ".join(type(error).__name__ for error in errors))
            self.stack.__exit__(exc_type, exc, traceback)
        finally:
            _active.reset(self.token)
