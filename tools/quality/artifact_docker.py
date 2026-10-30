"""Identity-bound Docker retirement and recovery; no force, prune or service start."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import gzip
import hashlib
import http.client
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
import tempfile
import threading
from urllib.parse import quote
import uuid

from tools.quality.artifact_registry import save, unlinked

PROJECT = "org.ragflow.artifact.project"
RUN = "org.ragflow.artifact.run"
OBJECT = "org.ragflow.artifact.object"
CLASS = "org.ragflow.artifact.class"
_transport = ContextVar("artifact_engine_transport", default=False)


def _native(function, *args, **kwargs):
    """Do not re-instrument the adapter's own verified helper operations."""
    token = _transport.set(True)
    try:
        return function(*args, **kwargs)
    finally:
        _transport.reset(token)


def digest(path: Path) -> str:
    unlinked(path)
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def labels(info: dict, kind: str) -> dict:
    return ((info.get("Config") or {}).get("Labels") if kind in {"image", "container"} else info.get("Labels")) or {}


def bind_identity(source: str):
    path = Path(re.sub(r"^/run/desktop/mnt/host/([a-zA-Z])/", r"\1:/", source))
    unlinked(path)
    info = path.stat()
    return [info.st_dev, info.st_ino, getattr(info, "st_birthtime_ns", 0)]


def volume_identity(info: dict | None) -> dict | None:
    if info is None:
        return None
    return {
        "identity": resource(info, "volume")["identity"],
        "owner": {key: labels(info, "volume").get(key) for key in (PROJECT, RUN, OBJECT)},
    }


def archive_name(value: str) -> str:
    """Canonical Linux archive path, independent of the Windows control host."""
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value or re.match(r"^[A-Za-z]:", value):
        raise ValueError("Unsafe archive member path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("Archive member escapes its destination")
    return str(path)


def validate_volume_archive(path: Path):
    """Validate metadata before tar can write to a fresh isolated volume.

    Symlink targets are preserved as data. No member or hardlink may traverse
    one, regardless of entry order. Devices and ambiguous duplicate paths are
    outside the disposable-volume recovery format.
    """
    unlinked(path)
    entries = {}
    try:
        with tarfile.open(path, mode="r:gz") as bundle:
            for member in bundle:
                name = archive_name(member.name)
                if name in entries or (name == "." and not member.isdir()):
                    raise ValueError("Duplicate or invalid archive member")
                if not (member.isfile() or member.isdir() or member.issym() or member.islnk()):
                    raise ValueError("Unsupported special archive member")
                entries[name] = ("directory" if member.isdir() else "symlink" if member.issym() else "hardlink" if member.islnk() else "file", member.linkname)
    except (tarfile.TarError, EOFError) as error:
        raise ValueError("Invalid volume archive") from error
    for name, (kind, target) in entries.items():
        for parent in PurePosixPath(name).parents:
            if str(parent) in entries and entries[str(parent)][0] != "directory":
                raise ValueError("Archive member traverses a non-directory")
        if kind == "hardlink":
            seen = {name}
            while True:
                target = archive_name(target)
                if target in seen or target not in entries:
                    raise ValueError("Unresolved or cyclic archive hardlink")
                seen.add(target)
                target_kind, next_target = entries[target]
                if target_kind == "file":
                    break
                if target_kind != "hardlink":
                    raise ValueError("Archive hardlink requires a regular file")
                target = next_target


def base_references(recipe_path: Path, overrides: dict | None = None) -> list[str]:
    """Resolve external FROM inputs shared by builders and lifecycle accounting."""
    recipe = recipe_path.read_text()
    defaults = {**dict(re.findall(r"^ARG (\w+)=([^\s]+)", recipe, re.M)), **(overrides or {})}
    result, stages = [], set()
    for image, stage in re.findall(r"^FROM (\S+)(?: AS (\S+))?", recipe, re.M):
        for name, value in defaults.items():
            image = image.replace("${" + name + "}", value)
        if image != "scratch" and image not in stages:
            result.append(image)
        if stage:
            stages.add(stage)
    return result


def _configuration_sha256(info: dict) -> str:
    return hashlib.sha256(json.dumps({k: info[k] for k in ("Config", "HostConfig", "Mounts")}, sort_keys=True).encode()).hexdigest()


def _configuration_matches(info: dict, expected: str | None) -> bool:
    """Keep the complete seal, allowing Docker's empty DNS array/null encoding."""
    host = info["HostConfig"]
    fields = [key for key in ("Dns", "DnsOptions", "DnsSearch") if key in host and (host[key] is None or host[key] == [])]
    for mask in range(1 << len(fields)):
        candidate = dict(host)
        for index, key in enumerate(fields):
            candidate[key] = [] if mask & (1 << index) else None
        if _configuration_sha256({**info, "HostConfig": candidate}) == expected:
            return True
    return False


def resource(info: dict, kind: str) -> dict:
    tag = labels(info, kind)
    identity = info["Id"] if kind != "volume" else {k: info.get(k) for k in ("Name", "Driver", "CreatedAt")}
    key = kind + ":" + hashlib.sha256(json.dumps([identity, tag.get(OBJECT)], sort_keys=True).encode()).hexdigest()
    result = {"key": key, "kind": kind, "identity": identity, "name": info.get("Name", info.get("Id", "")).lstrip("/"), "token": tag.get(OBJECT), "category": tag.get(CLASS), "status": "present"}
    if kind == "container":
        result["config_sha256"] = _configuration_sha256(info)
    if kind == "image":
        result["tags"] = sorted(info.get("RepoTags") or [])
    return result


class Engine:
    def __init__(self, *, expected: str | None = None):
        # Pin the CLI transport; changing the user's default context cannot retarget a run.
        self.env = os.environ.copy()
        self.privilege = ["sudo", "-n"] if self.env.get("RAGFLOW_ARTIFACT_DOCKER_SUDO") == "1" else []
        self.configuration = ["--config", self.env["CI_DOCKER_CONFIG"]] if self.env.get("CI_DOCKER_CONFIG") else []
        host = self.env.get("DOCKER_HOST")
        context = _native(subprocess.check_output, [*self.privilege, "docker", *self.configuration, "context", "show"], text=True, timeout=30).strip()
        self.prefix = ["docker", "--host", host] if host else ["docker", "--context", context]
        self.identity = self.run("info", "--format", "{{.ID}}")
        if not self.identity or (expected and expected != self.identity):
            raise ValueError("Docker engine identity changed")

    def process(self, *arguments, **kwargs):
        return _native(subprocess.Popen, [*self.privilege, *self.prefix, *self.configuration, *arguments], env=self.env, **kwargs)

    def run(self, *arguments, data=None, timeout=120) -> str:
        result = _native(subprocess.run, [*self.privilege, *self.prefix, *self.configuration, *arguments], env=self.env, input=data, capture_output=True, timeout=timeout)
        if result.returncode:
            raise RuntimeError(f"Docker {arguments[0]} failed ({result.returncode})")
        return result.stdout.decode().strip()

    def assert_engine(self):
        if self.run("info", "--format", "{{.ID}}") != self.identity:
            raise ValueError("Docker engine identity changed")

    def inspect(self, kind: str, identifier: str) -> dict | None:
        result = _native(subprocess.run, [*self.privilege, *self.prefix, *self.configuration, kind, "inspect", identifier], env=self.env, capture_output=True, timeout=60)
        if result.returncode:
            if b"no such" in result.stderr.lower() or b"not found" in result.stderr.lower():
                return None
            raise RuntimeError("Cannot verify Docker resource")
        return json.loads(result.stdout)[0]

    def all(self, kind: str, run_id: str | None = None) -> list[dict]:
        options = ["-q"] if kind in {"volume", "network"} else ["-aq", "--no-trunc"]
        if run_id:
            options += ["--filter", f"label={PROJECT}=ragflow", "--filter", f"label={RUN}={run_id}"]
        identifiers = sorted(set(self.run(kind, "ls", *options).split()))
        values = []
        for offset in range(0, len(identifiers), 40):
            result = _native(subprocess.run, [*self.privilege, *self.prefix, *self.configuration, kind, "inspect", *identifiers[offset : offset + 40]], env=self.env, capture_output=True, timeout=120)
            errors = result.stderr.decode(errors="replace").lower().splitlines()
            if result.returncode and (not errors or any("no such" not in line and "not found" not in line for line in errors if line.strip())):
                raise RuntimeError("Cannot enumerate Docker consumers")
            values.extend(json.loads(result.stdout or b"[]"))
        return values

    def owned(self, run_id: str) -> list[dict]:
        return [resource(info, kind) for kind in ("container", "volume", "network", "image") for info in self.all(kind, run_id)]

    def stop_owned(self, run_id: str):
        self.assert_engine()
        for info in self.all("container", run_id):
            if labels(info, "container").get(CLASS) != "disposable" or not labels(info, "container").get(OBJECT):
                raise ValueError("Refusing to stop a persistent container")
            for exec_id in info.get("ExecIDs") or []:
                execution = self.api("GET", f"/exec/{exec_id}/json", {})
                if execution.get("ContainerID") != info["Id"] or execution.get("Running") is not False:
                    raise ValueError("Refusing to stop a container with an active or unverifiable exec")
            if info["State"]["Running"]:
                # Only the owner in its teardown calls this, never the sweeper.
                self.run("container", "stop", "--time", "30", info["Id"])

    def guard(self, target: dict, run_id: str, pins: dict, removable: set) -> str | None:
        self.assert_engine()
        kind = target["kind"]
        if kind == "image-alias":
            info = self.inspect("image", target["name"])
            if info is None:
                return "already-absent"
            if info["Id"] != target["identity"]:
                return "alias-replaced"
            if not set(target["original_tags"]) & set(info.get("RepoTags") or []):
                return "alias-would-remove-unowned-image"
            if any(c.get("Config", {}).get("Image") == target["name"] for c in self.all("container")):
                return "container-reference"
            return None
        identifier = target["name"] if kind == "volume" else target["identity"]
        info = self.inspect(kind, identifier)
        if info is None:
            return "already-absent"
        tags = labels(info, kind)
        legacy = target.get("legacy_owner")
        legacy_matches = bool(kind == "image" and legacy and legacy.get("label") == "org.ragflow.ci-dry-run" and tags.get(legacy["label"]) == legacy["value"])
        if not legacy_matches and (tags.get(PROJECT) != "ragflow" or tags.get(RUN) != run_id or not target.get("token") or tags.get(OBJECT) != target["token"]):
            return "ownership-or-generation-changed"
        actual = resource(info, kind)
        if actual["identity"] != target["identity"]:
            return "identity-or-configuration-changed"
        if actual.get("config_sha256") != target.get("config_sha256") and (kind != "container" or not _configuration_matches(info, target.get("config_sha256"))):
            return "identity-or-configuration-changed"
        if kind == "container":
            if any(info["State"].get(key) for key in ("Running", "Paused", "Restarting")):
                return "container-active"
            return None
        if kind == "image":
            if set(info.get("RepoTags") or []) - set(target.get("tags", [])):
                return "image-tags-changed"
            if any(pins.get(key) for key in [info["Id"], *(info.get("RepoDigests") or [])]):
                return "receipt-pin"
        if kind == "volume" and (info.get("Driver") != "local" or info.get("Options")):
            return "external-or-custom-volume-storage"
        for container in self.all("container"):
            references = (kind == "image" and container["Image"] == target["identity"]) or (
                kind == "volume" and any(m["Type"] == "volume" and m["Name"] == target["name"] for m in container.get("Mounts", []))
            )
            if references and (container["Id"] not in removable or container["State"]["Running"]):
                return "container-reference"
        if kind == "network" and any(identifier not in removable for identifier in info.get("Containers", {})):
            return "network-consumer"
        return None

    def api(self, method: str, path: str, body: dict) -> dict:
        self.assert_engine()
        data = json.dumps(body).encode()
        request = (f"{method} {path} HTTP/1.1\r\nHost: docker\r\nConnection: close\r\nContent-Type: application/json\r\nContent-Length: {len(data)}\r\n\r\n").encode() + data
        process = self.process("system", "dial-stdio", stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        timeout = threading.Timer(120, process.kill)
        timeout.daemon = True
        timeout.start()
        try:
            process.stdin.write(request)
            process.stdin.flush()

            class Transport:
                def makefile(self, *_args, **_kwargs):
                    return process.stdout

            response = http.client.HTTPResponse(Transport())
            response.begin()
            payload = response.read()
            if not 200 <= response.status < 300:
                raise RuntimeError(f"Docker Engine request failed ({response.status})")
            return json.loads(payload) if payload else {}
        finally:
            timeout.cancel()
            process.stdin.close()
            process.stdout.close()
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()

    def export(self, arguments: list[str], destination: Path) -> str:
        unlinked(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        raw_hash = hashlib.sha256()
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as stream:
                temporary = Path(stream.name)
                with self.process(*arguments, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as process:
                    with gzip.GzipFile(fileobj=stream, mode="wb", compresslevel=1, mtime=0) as compressed:
                        while chunk := process.stdout.read(1024 * 1024):
                            raw_hash.update(chunk)
                            compressed.write(chunk)
                    if process.wait(timeout=120):
                        raise RuntimeError("Docker archive export failed")
                stream.flush()
                os.fsync(stream.fileno())
            unlinked(destination)
            temporary.replace(destination)
            return raw_hash.hexdigest()
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)

    def pipe_archive(self, arguments: list[str], archive: Path):
        with archive.open("rb") as stream:
            result = _native(
                subprocess.run, [*self.privilege, *self.prefix, *self.configuration, *arguments], env=self.env, stdin=stream, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=1800
            )
        if result.returncode:
            raise RuntimeError("Docker archive verification/restoration failed")

    def helper(self) -> str:
        # Never pull or trust a mutable tag at deletion time. GNU tar preserves directory metadata.
        reference = os.environ.get("RAGFLOW_ARTIFACT_TAR_IMAGE", "ubuntu@sha256:68ee22004a9109b44973313b02ff7b1de250f1de0f5ecb628d73f579aaff33ac")
        info = self.inspect("image", reference)
        if info is None:
            raise ValueError("Recovery requires an installed GNU tar image (RAGFLOW_ARTIFACT_TAR_IMAGE)")
        return info["Id"]

    @contextmanager
    def volume_access(self, info: dict, helper: str, store: Path, *, writable=False):
        """Pin an exact volume generation before starting any archive I/O."""
        from tools.quality.artifact_lifecycle import event

        owner = labels(info, "volume")
        if not owner.get(RUN):
            raise ValueError("Volume archive operation requires a lifecycle owner")
        token = uuid.uuid4().hex
        tags = {PROJECT: "ragflow", RUN: owner[RUN], OBJECT: token, CLASS: "disposable"}
        event(store, owner[RUN], creation_intent={"kind": "container", "object": token, "purpose": "volume-archive-pin"})
        body = {
            "Image": helper,
            "Entrypoint": ["sleep"],
            "Cmd": ["infinity"],
            "Labels": tags,
            "HostConfig": {"NetworkMode": "none", "ReadonlyRootfs": True, "Mounts": [{"Type": "volume", "Source": info["Name"], "Target": "/data", "ReadOnly": not writable}]},
        }
        created = self.api("POST", "/containers/create?name=ragflow-volume-pin-" + token, body)
        identifier = created["Id"]
        try:
            pin = self.inspect("container", identifier)
            if pin is None or labels(pin, "container").get(OBJECT) != token:
                raise ValueError("Volume pin ownership collision")
            event(store, owner[RUN], resource=resource(pin, "container"))
            if volume_identity(self.inspect("volume", info["Name"])) != volume_identity(info):
                raise ValueError("Volume generation changed before archive operation")
            self.run("container", "start", identifier)
            if "GNU tar" not in self.run("exec", identifier, "tar", "--version"):
                raise ValueError("Recovery helper must provide GNU tar")
            yield identifier
        finally:
            current = self.inspect("container", identifier)
            if current and labels(current, "container").get(OBJECT) == token:
                if current.get("State", {}).get("Running"):
                    self.run("container", "stop", "--time", "1", identifier)
                self.run("container", "rm", identifier)
                event(store, owner[RUN], resource={**resource(current, "container"), "status": "removed"})

    def volume_io(self, pin: str, action: str, archive: Path):
        arguments = ["exec", "-i", pin, "tar"]
        if action == "export":
            return self.export(["exec", pin, "tar", "-cpf", "-", "-C", "/data", "."], archive)
        if action == "restore":
            validate_volume_archive(archive)
        arguments += ["-xzpf", "-", "-C", "/data"] if action == "restore" else ["--compare", "-zpf", "-", "-C", "/data"]
        return self.pipe_archive(arguments, archive)

    def image_archive(self, identifier: str, directory: Path) -> dict:
        from tools.quality import artifact_blobs

        metadata = directory / "images" / (identifier.removeprefix("sha256:") + ".image.json")
        unlinked(metadata)
        known = {}
        for previous in (directory / "images").glob("*.image.json"):
            unlinked(previous)
            known.update({member["blob"]["raw_sha256"]: member["blob"] for member in json.loads(previous.read_bytes())["members"]})
        if metadata.exists():
            existing = archive_info(metadata, image_id=identifier, kind="image-cas")
            self.verify_image(existing)
            return existing
        expected = identifier.removeprefix("sha256:")
        members, found = [], False
        with self.process("image", "save", identifier, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as process:
            with tarfile.open(fileobj=process.stdout, mode="r|") as bundle:
                for member in bundle:
                    if member.isdir():
                        continue
                    if not member.isfile():
                        raise ValueError("Unsupported image archive member")
                    expected_blob = member.name.rsplit("/", 1)[-1] if member.name.startswith("blobs/sha256/") else None
                    blob = artifact_blobs.put(bundle.extractfile(member), directory, reuse=known.get(expected_blob))
                    value = blob["raw_sha256"]
                    if member.name.startswith("blobs/sha256/") and value != member.name.rsplit("/", 1)[-1]:
                        raise ValueError("Image archive blob digest mismatch")
                    if member.name in {f"blobs/sha256/{expected}", f"{expected}.json"}:
                        if value != expected:
                            raise ValueError("Image config identity mismatch")
                        found = True
                    if member.name == "manifest.json":
                        with gzip.open(artifact_blobs.path(directory, blob), "rb") as stream:
                            if any(entry.get("RepoTags") for entry in json.load(stream)):
                                raise ValueError("Recovery images must be saved by immutable ID without tags")
                    members.append({"header": {k: v for k, v in member.get_info().items() if k != "type"}, "blob": blob})
            # Streaming tar stops at end markers before CLI pipe padding is drained.
            while process.stdout.read(1024 * 1024):
                pass
            if process.wait(timeout=120):
                raise RuntimeError("Image preservation failed")
        if not found or len({m["header"]["name"] for m in members}) != len(members):
            raise ValueError("Image archive lacks expected config")
        save({"schema": 1, "image_id": identifier, "members": members}, metadata)
        return archive_info(metadata, image_id=identifier, kind="image-cas")

    def verify_image(self, packet: dict):
        verify_archive(packet)
        if packet.get("kind") == "image-cas":
            from tools.quality import artifact_blobs

            manifest = json.loads(Path(packet["path"]).read_bytes())
            if manifest.get("schema") != 1 or manifest.get("image_id") != packet.get("image_id"):
                raise ValueError("Image manifest identity mismatch")
            root = Path(packet["path"]).parent.parent
            names = set()
            for member in manifest["members"]:
                name = archive_name(member["header"]["name"])
                if name == "." or name in names or member["header"]["size"] != member["blob"]["raw_bytes"]:
                    raise ValueError("Invalid or duplicate image archive member")
                names.add(name)
                artifact_blobs.path(root, member["blob"])

    def load_image(self, packet: dict):
        self.verify_image(packet)
        if packet.get("kind") != "image-cas":
            return self.pipe_archive(["image", "load"], Path(packet["path"]))
        from tools.quality import artifact_blobs

        manifest = json.loads(Path(packet["path"]).read_bytes())
        root = Path(packet["path"]).parent.parent
        with self.process("image", "load", stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) as process:
            with tarfile.open(fileobj=process.stdin, mode="w|") as bundle:
                for member in manifest["members"]:
                    header = member["header"]
                    item = tarfile.TarInfo(header["name"])
                    for name in ("size", "mode", "uid", "gid", "mtime", "uname", "gname"):
                        setattr(item, name, header[name])
                    with gzip.open(artifact_blobs.path(root, member["blob"]), "rb") as stream:
                        bundle.addfile(item, stream)
            process.stdin.close()
            if process.wait(timeout=1800):
                raise RuntimeError("Image restoration failed")

    def snapshot(self, info: dict, root: Path, *, store: Path, preserve=True) -> dict:
        if info["State"].get("Running") or info["State"].get("Paused"):
            raise ValueError("Cannot snapshot an active container")
        token = uuid.uuid4().hex
        original = labels(info, "container")
        tags = {PROJECT: "ragflow", RUN: original[RUN], OBJECT: token, CLASS: "disposable"}
        from tools.quality.artifact_lifecycle import event

        event(store, original[RUN], creation_intent={"kind": "image", "object": token, "purpose": "container-snapshot"})
        arguments = [part for key, value in tags.items() for part in ("--change", f"LABEL {key}={value}")]
        identifier = self.run("container", "commit", *arguments, info["Id"], timeout=1800)
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", identifier):
            raise ValueError("Invalid snapshot image identity")
        try:
            snapshot = self.inspect("image", identifier)
            if snapshot is None or labels(snapshot, "image").get(OBJECT) != token:
                raise ValueError("Snapshot image ownership collision")
            event(store, original[RUN], resource=resource(snapshot, "image"))
            return {**({"archive": self.image_archive(identifier, root)} if preserve else {}), "layers": snapshot["RootFS"]["Layers"]}
        finally:
            current = self.inspect("image", identifier)
            if current and labels(current, "image").get(OBJECT) == token:
                self.run("image", "rm", "--no-prune", identifier)
                event(store, original[RUN], resource={**resource(current, "image"), "status": "removed"})

    def backup(self, target: dict, store: Path) -> dict:
        root = archive_root(store)
        folder = root / "resources" / target["key"].replace(":", "-")
        unlinked(folder)
        folder.mkdir(parents=True, exist_ok=True)
        metadata_path = folder / "recovery.json"
        identifier = target["name"] if target["kind"] in {"volume", "image-alias"} else target["identity"]
        info = self.inspect("image" if target["kind"] == "image-alias" else target["kind"], identifier)
        if info is None:
            raise ValueError("Resource disappeared before backup")
        if metadata_path.exists():
            existing = json.loads(metadata_path.read_bytes())
            verify_archive(existing["inspect"])
            if json.loads(Path(existing["inspect"]["path"]).read_bytes()).get("Id") != info.get("Id"):
                raise ValueError("Preserved resource generation changed")
            if target["kind"] == "container":
                previous = json.loads(Path(existing["inspect"]["path"]).read_bytes())
                if not _configuration_matches(info, _configuration_sha256(previous)):
                    raise ValueError("Preserved container configuration changed")
            for archive in existing.get("archives", []):
                self.verify_image(archive)
            existing.setdefault("state_store", str(store.absolute()))
            if target["kind"] == "container" and existing.get("base_image") and not existing.get("base_image_archive"):
                existing["base_image_archive"] = self.image_archive(existing["base_image"], root)
                save(existing, metadata_path)
            self.verify_backup({**target, "recovery": existing})
            return existing
        # Inspect can contain secrets: retained only in ignored local recovery, never printed.
        save(info, folder / "inspect.json")
        recovery = {"schema": 1, "kind": target["kind"], "inspect": archive_info(folder / "inspect.json"), "archives": [], "state_store": str(store.absolute())}
        if target["kind"] in {"image", "image-alias"}:
            recovery["archives"] = [self.image_archive(info["Id"], root)]
            recovery["tags"] = [target["name"]] if target["kind"] == "image-alias" else (info.get("RepoTags") or [])
        elif target["kind"] == "container":
            recovery["bind_sources"] = {mount["Source"]: bind_identity(mount["Source"]) for mount in info.get("Mounts", []) if mount["Type"] == "bind"}
            recovery["volume_sources"] = {mount["Name"]: volume_identity(self.inspect("volume", mount["Name"])) for mount in info.get("Mounts", []) if mount["Type"] == "volume"}
            if any(value is None for value in recovery["volume_sources"].values()):
                raise ValueError("Container volume disappeared before preservation")
            if not self.run("container", "diff", info["Id"]):
                recovery["base_image"] = info["Image"]
                recovery["base_image_archive"] = self.image_archive(info["Image"], root)
            else:
                snapshot = self.snapshot(info, root, store=store)
                recovery["snapshot"] = snapshot
                recovery["archives"] = [snapshot["archive"]]
                if snapshot["layers"] != self.snapshot(info, root, store=store, preserve=False)["layers"]:
                    raise ValueError("Container filesystem changed during preservation")
        elif target["kind"] == "volume":
            helper = self.helper()
            recovery["helper"] = self.image_archive(helper, root)
            archive = folder / "data.tar.gz"
            with self.volume_access(info, helper, store) as pin:
                self.volume_io(pin, "export", archive)
                self.volume_io(pin, "compare", archive)
            probe = "ragflow-recovery-probe-" + uuid.uuid4().hex
            token = uuid.uuid4().hex
            from tools.quality.artifact_lifecycle import event

            event(store, labels(info, "volume")[RUN], creation_intent={"kind": "volume", "object": token, "purpose": "restore-probe"})
            self.api(
                "POST", "/volumes/create", {"Name": probe, "Labels": {PROJECT: "ragflow", RUN: labels(info, "volume")[RUN], OBJECT: token, CLASS: "disposable", "org.ragflow.artifact.probe": token}}
            )
            probe_info = self.inspect("volume", probe)
            if labels(probe_info, "volume").get("org.ragflow.artifact.probe") != token:
                raise ValueError("Recovery probe ownership collision")
            event(store, labels(info, "volume")[RUN], resource=resource(probe_info, "volume"))
            try:
                with self.volume_access(probe_info, helper, store, writable=True) as pin:
                    self.volume_io(pin, "restore", archive)
                    self.volume_io(pin, "compare", archive)
            finally:
                current = self.inspect("volume", probe)
                if current and labels(current, "volume").get("org.ragflow.artifact.probe") == token:
                    self.run("volume", "rm", probe)
                    event(store, labels(info, "volume")[RUN], resource={**resource(current, "volume"), "status": "removed"})
            recovery["archives"] = [archive_info(archive)]
            recovery["restore_probe"] = "PASS"
        save(recovery, metadata_path)
        return recovery

    def container_digest(self, identifier: str) -> str:
        with self.process("container", "export", identifier, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as process:
            value = hashlib.file_digest(process.stdout, "sha256").hexdigest()
            if process.wait(timeout=120):
                raise RuntimeError("Container preservation verification failed")
        return value

    def verify_recovery(self, packet: dict):
        verify_archive(packet["inspect"])
        for archive in packet.get("archives", []):
            self.verify_image(archive)
            if packet.get("kind") == "volume":
                validate_volume_archive(Path(archive["path"]))
        for key in ("base_image_archive", "helper"):
            if packet.get(key):
                self.verify_image(packet[key])

    def verify_backup(self, target: dict):
        self.verify_recovery(target["recovery"])
        if target["kind"] == "container":
            packet = target["recovery"]
            if packet.get("base_image"):
                if self.run("container", "diff", target["identity"]):
                    raise ValueError("Container filesystem changed after backup")
            elif packet.get("snapshot"):
                info = self.inspect("container", target["identity"])
                if self.snapshot(info, Path(packet["archives"][0]["path"]).parent.parent, store=Path(packet["state_store"]), preserve=False)["layers"] != packet["snapshot"]["layers"]:
                    raise ValueError("Container filesystem changed after backup")
            elif self.container_digest(target["identity"]) != packet["raw_sha256"]:
                raise ValueError("Container filesystem changed after backup")
        if target["kind"] == "volume":
            packet = target["recovery"]
            info = self.inspect("volume", target["name"])
            if info is None or resource(info, "volume")["identity"] != target["identity"] or labels(info, "volume").get(OBJECT) != target["token"]:
                raise ValueError("Preserved volume generation changed")
            with self.volume_access(info, packet["helper"]["image_id"], Path(packet["state_store"])) as pin:
                self.volume_io(pin, "compare", Path(packet["archives"][0]["path"]))

    def remove(self, target: dict):
        self.assert_engine()
        identifier = target["name"] if target["kind"] in {"volume", "image-alias"} else target["identity"]
        kind = "image" if target["kind"] == "image-alias" else target["kind"]
        self.run(kind, "rm", *(["--no-prune"] if kind == "image" else []), identifier)
        if self.inspect(kind, identifier) is not None:
            raise RuntimeError("Retired resource still exists")


def archive_root(store: Path) -> Path:
    configured = os.environ.get("RAGFLOW_ARTIFACT_ARCHIVES")
    if configured:
        return Path(configured).absolute()
    if store.name == "artifacts" and store.parent.name == "maintenance":
        return store.parents[1] / "archives/artifact-lifecycle"
    return store / "recovery"


def archive_info(path: Path, **extra) -> dict:
    return {"path": str(path.absolute()), "sha256": digest(path), "bytes": path.stat().st_size, **extra}


def verify_archive(archive: dict):
    path = Path(archive["path"])
    if path.stat().st_size != archive["bytes"] or digest(path) != archive["sha256"]:
        raise ValueError("Recovery archive integrity mismatch")


def restore(run: dict, recovery_run):
    """Restore removed resources to unused original names; create containers stopped."""
    from tools.quality.artifact_lifecycle import invalidate_release, merge_events, write_run

    if run.get("recovery_expired_at"):
        raise ValueError("Recovery was explicitly released and expired")
    merge_events(recovery_run.store, run)
    resources = [r for r in run["resources"] if r.get("status") == "removed" and r.get("recovery")]
    input_snapshots = [item["preserved_evidence"] for item in run.get("input_files", []) if item.get("preserved_evidence")]
    if resources or input_snapshots:
        from copy import deepcopy

        dependency = {"run_id": run["run_id"], "epoch": run.get("recovery_epoch", 0), "packets": deepcopy([*[target["recovery"] for target in resources], *input_snapshots])}
        if dependency not in recovery_run.data.setdefault("recovery_dependencies", []):
            recovery_run.data["recovery_dependencies"].append(dependency)
            recovery_run.persist()
        run["recovery_epoch"] = run.get("recovery_epoch", 0) + 1
        invalidate_release(run, "recovery-resumed")
        write_run(recovery_run.store, run)
    engine = recovery_run.engine() if any(target["kind"] != "path" for target in resources) else None
    for item in run.get("input_files", []):
        packet = item.get("preserved_evidence")
        if not packet:
            continue
        if packet.get("kind") != "evidence-file" or item.get("content_sha256") != packet.get("archive", {}).get("sha256"):
            raise ValueError("Runtime input recovery requires its exact metadata file snapshot")
        verify_archive(packet["archive"])
        recovery_run.data.setdefault("required_input_snapshots", []).append({"path": item["path"], "content_sha256": item["content_sha256"], "snapshot": packet})
    inspections = {}
    for target in resources:
        packet = target["recovery"]
        if target["kind"] == "path":
            verify_archive(packet["metadata"])
            destination = Path(packet["destination"])
            unlinked(destination)
            if packet["kind"] == "cache-file" and destination.exists():
                raise ValueError("Restore destination already exists; no data overwritten")
            continue
        verify_archive(packet["inspect"])
        for archive in packet.get("archives", []):
            engine.verify_image(archive)
        if packet.get("helper"):
            engine.verify_image(packet["helper"])
        if packet.get("base_image_archive"):
            engine.verify_image(packet["base_image_archive"])
        info = json.loads(Path(packet["inspect"]["path"]).read_bytes())
        inspections[target["key"]] = info
        if target["kind"] not in {"image", "image-alias"} and engine.inspect(target["kind"], info["Name"].lstrip("/")) is not None:
            raise ValueError("Restore destination already exists; no data overwritten")
        if target["kind"] in {"image", "image-alias"}:
            for tag in packet.get("tags", []) if target["kind"] == "image-alias" else []:
                existing = engine.inspect("image", tag)
                if existing and existing["Id"] != info["Id"]:
                    raise ValueError("Restore image tag conflicts with current image")
        if target["kind"] == "container":
            for mount in info.get("Mounts", []):
                if mount["Type"] == "bind":
                    expected = packet.get("bind_sources", {}).get(mount["Source"])
                    if expected is None or bind_identity(mount["Source"]) != expected:
                        raise ValueError("Required bind source generation must be restored before container")
    network_map, volume_map = {}, {}

    def check_mounts(info, packet):
        for mount in info.get("Mounts", []):
            if mount["Type"] == "bind":
                expected = packet.get("bind_sources", {}).get(mount["Source"])
                if expected is None or bind_identity(mount["Source"]) != expected:
                    raise ValueError("Required bind source generation changed before container creation")
            elif mount["Type"] == "volume":
                expected = volume_map.get(mount["Name"]) or packet.get("volume_sources", {}).get(mount["Name"])
                if expected is None or volume_identity(engine.inspect("volume", mount["Name"])) != expected:
                    raise ValueError("Required volume generation must be restored before container")

    order = {"image": 0, "image-alias": 0, "network": 1, "volume": 2, "container": 3, "path": 4}
    for target in sorted(resources, key=lambda r: order[r["kind"]]):
        packet, kind = target["recovery"], target["kind"]
        if kind == "path":
            if packet["kind"] == "rebuildable-native-cache":
                recovery_run.data.setdefault("rebuild_required", []).append({"path": packet["destination"], "command": packet["rebuild"]})
                recovery_run.persist()
            else:
                from tools.quality.artifact_paths import cache_kind

                destination = Path(packet["destination"])
                if cache_kind(destination) != "receipt":
                    raise ValueError("Restore cache destination is outside owned cache policy")
                unlinked(destination)
                destination.parent.mkdir(parents=True, exist_ok=True)
                with destination.open("xb") as stream:
                    stream.write(Path(packet["metadata"]["path"]).read_bytes())
                recovery_run.path(destination, "cache")
            continue
        info = inspections[target["key"]]
        owner_labels = recovery_run.labels(kind)
        if kind in {"image", "image-alias"}:
            if engine.inspect("image", info["Id"]) is None:
                engine.load_image(packet["archives"][0])
            recovery_run.use_image(info["Id"])
            for tag in packet.get("tags", []) if kind == "image-alias" else []:
                existing = engine.inspect("image", tag)
                if existing and existing["Id"] != info["Id"]:
                    raise ValueError("Late restore tag collision")
                engine.run("image", "tag", info["Id"], tag)
                recovery_run.use_image(tag)
            # Immutable loaded images retain original labels; resume their original ledger entry.
            target.update(status="present", restored_by=recovery_run.data["run_id"])
            from tools.quality.artifact_lifecycle import write_run

            write_run(recovery_run.store, run)
        elif kind == "network":
            body = {k: info[k] for k in ("Name", "Driver", "Internal", "Attachable", "EnableIPv6", "IPAM", "Options") if k in info}
            body["CheckDuplicate"] = True
            body["Labels"] = {**(info.get("Labels") or {}), **owner_labels}
            created = engine.api("POST", "/networks/create", body)
            network = engine.inspect("network", created["Id"])
            if network is None or labels(network, "network").get(OBJECT) != owner_labels[OBJECT]:
                raise ValueError("Late restore network collision")
            network_map[info["Id"]] = network_map[info["Name"]] = created["Id"]
        elif kind == "volume":
            helper = packet["helper"]
            if engine.inspect("image", helper["image_id"]) is None:
                engine.load_image(helper)
            body = {"Name": info["Name"], "Driver": info["Driver"], "DriverOpts": info.get("Options") or {}, "Labels": {**(info.get("Labels") or {}), **owner_labels}}
            engine.api("POST", "/volumes/create", body)
            created = engine.inspect("volume", info["Name"])
            if labels(created, "volume").get(OBJECT) != owner_labels[OBJECT]:
                raise ValueError("Late restore volume collision")
            with engine.volume_access(created, helper["image_id"], recovery_run.store, writable=True) as pin:
                engine.volume_io(pin, "restore", Path(packet["archives"][0]["path"]))
                engine.volume_io(pin, "compare", Path(packet["archives"][0]["path"]))
            volume_map[info["Name"]] = volume_identity(created)
        else:
            # Import preserves filesystem bytes; Config/HostConfig are restored via Engine JSON.
            imported = packet.get("base_image") or packet.get("snapshot", {}).get("archive", {}).get("image_id")
            if imported:
                if engine.inspect("image", imported) is None:
                    preserved = packet.get("snapshot", {}).get("archive") or packet.get("base_image_archive")
                    if not preserved:
                        manifest = archive_root(recovery_run.store) / "images" / (imported.removeprefix("sha256:") + ".image.json")
                        if manifest.exists():
                            preserved = archive_info(manifest, image_id=imported, kind="image-cas")
                        else:
                            manifest = manifest.with_name(imported.removeprefix("sha256:") + ".tar.json")
                            unlinked(manifest)
                            preserved = json.loads(manifest.read_bytes())
                    engine.load_image(preserved)
            else:
                with Path(packet["archives"][0]["path"]).open("rb") as stream:
                    changes = [part for key, value in owner_labels.items() for part in ("--change", f"LABEL {key}={value}")]
                    result = _native(
                        subprocess.run, [*engine.privilege, *engine.prefix, *engine.configuration, "image", "import", *changes, "-"], env=engine.env, stdin=stream, capture_output=True, timeout=1800
                    )
                if result.returncode:
                    raise RuntimeError("Container filesystem import failed")
                imported = result.stdout.decode().strip()
            recovery_run.use_image(imported)
            image = engine.inspect("image", imported)
            if image is None:
                raise ValueError("Restored image disappeared before container creation")
            if labels(image, "image").get(PROJECT) == "ragflow" and labels(image, "image").get(RUN) == run["run_id"]:
                loaded = resource(image, "image")
                existing = next((entry for entry in run["resources"] if entry["key"] == loaded["key"]), None)
                if existing:
                    existing.update(status="present", restored_by=recovery_run.data["run_id"])
                else:
                    loaded["restored_by"] = recovery_run.data["run_id"]
                    run["resources"].append(loaded)
                write_run(recovery_run.store, run)
            host = info["HostConfig"].copy()
            host["NetworkMode"] = network_map.get(host.get("NetworkMode"), host.get("NetworkMode"))
            body = {**info["Config"], "Image": imported, "Labels": {**(info["Config"].get("Labels") or {}), **owner_labels}, "HostConfig": host}
            endpoints = {}
            for name, endpoint in info.get("NetworkSettings", {}).get("Networks", {}).items():
                endpoints[network_map.get(name, name)] = {k: endpoint[k] for k in ("Aliases", "IPAMConfig") if endpoint.get(k)}
            if endpoints:
                body["NetworkingConfig"] = {"EndpointsConfig": endpoints}
            from tools.quality.artifact_lifecycle import mutation

            with mutation(recovery_run.store):
                check_mounts(info, packet)
                created = engine.api("POST", "/containers/create?name=" + quote(info["Name"].lstrip("/"), safe=""), body)
                restored = engine.inspect("container", created["Id"])
                if restored is None or labels(restored, "container").get(OBJECT) != owner_labels[OBJECT]:
                    raise ValueError("Late restore container ownership collision")
                if restored["State"]["Running"]:
                    raise RuntimeError("Recovery unexpectedly started a container")
                try:
                    check_mounts(info, packet)
                except (OSError, ValueError):
                    engine.run("container", "rm", created["Id"])
                    raise
        recovery_run.capture()
