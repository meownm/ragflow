"""Prepare and supervise the T3 Python architecture Docker boundary."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import secrets
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

VERSION = "0.1.0"
SCHEMA_VERSION = 1
RUNTIME_UID = 65534
REPORT_NAME = "architecture-python.json"
REQUIRED_NEGATIVE_CHECKS = (
    "candidate_write_denied",
    "evidence_read_denied",
    "evidence_write_denied",
    "network_denied",
    "forbidden_environment_absent",
    "sensitive_paths_absent",
    "git_config_sanitized",
    "unprivileged_identity",
    "capabilities_absent",
    "no_new_privileges",
)
FORBIDDEN_CONFIG_MARKERS = (
    "credential",
    "extraheader",
    "include",
    "askpass",
    "proxy",
    "insteadof",
    "sslkey",
    "cookiefile",
)
REQUIRED_CANDIDATE_DIRECTORIES = (
    "services/asr-online-service",
    "web",
)
RESERVED_DEPENDENCY_PATHS = (
    ".venv",
    "services/asr-online-service/.architecture-venv",
    "web/node_modules",
)


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _sha256_path(path: Path) -> str:
    return _sha256(path.read_bytes())


def _commit(value: str, label: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40}", value):
        raise ValueError(f"{label} must be an exact SHA-1 commit")
    return value


def _safe_mount_path(path: Path, label: str) -> Path:
    resolved = path.resolve()
    if not resolved.is_absolute() or "," in str(resolved) or "\n" in str(resolved) or "\r" in str(resolved):
        raise ValueError(f"{label} is unsafe for a Docker bind mount")
    return resolved


def _git_environment(home: Path) -> dict[str, str]:
    environment = {
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "HOME": str(home),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": os.environ.get("PATH", ""),
    }
    if "SYSTEMROOT" in os.environ:
        environment["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
    return environment


def _run(
    command: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: int = 120,
    check: bool = True,
    text: bool = True,
) -> subprocess.CompletedProcess:
    completed = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=text, timeout=timeout, check=False)
    if check and completed.returncode != 0:
        stderr = completed.stderr[-4000:] if text else completed.stderr[-4000:].decode("utf-8", errors="replace")
        raise ValueError(f"Command failed with {completed.returncode}: {command[0]}: {stderr.strip()}")
    return completed


def _git(root: Path, *arguments: str, environment: dict[str, str]) -> str:
    return _run(["git", "-C", str(root), *arguments], env=environment).stdout.strip()


def _unsafe_git_config_keys(content: bytes) -> list[str]:
    keys = [item.decode("utf-8", errors="replace").lower() for item in content.split(b"\0") if item]
    return sorted(key for key in keys if any(marker in key for marker in FORBIDDEN_CONFIG_MARKERS))


def _clone_state(root: Path, environment: dict[str, str]) -> dict:
    config = _run(
        ["git", "-C", str(root), "config", "--local", "--name-only", "--null", "--list"],
        env=environment,
        text=False,
    ).stdout
    unsafe = _unsafe_git_config_keys(config)
    if unsafe:
        raise ValueError("Sanitized clone contains unsafe local Git configuration: " + ", ".join(unsafe))
    if _git(root, "remote", environment=environment):
        raise ValueError("Sanitized clone retains a remote")
    if (root / ".git/objects/info/alternates").exists():
        raise ValueError("Sanitized clone retains an object alternate")
    status = _git(root, "status", "--porcelain=v1", "--untracked-files=all", environment=environment)
    if status:
        raise ValueError("Sanitized clone is not clean")
    return {
        "head": _git(root, "rev-parse", "HEAD^{commit}", environment=environment),
        "tree": _git(root, "rev-parse", "HEAD^{tree}", environment=environment),
        "config_sha256": _sha256(config),
    }


def _validate_dependency_targets(root: Path) -> None:
    for relative in REQUIRED_CANDIDATE_DIRECTORIES:
        target = root.joinpath(*PurePosixPath(relative).parts)
        if target.is_symlink() or not target.is_dir():
            raise ValueError(f"Candidate dependency parent must be a real directory: {relative}")
    for relative in RESERVED_DEPENDENCY_PATHS:
        target = root.joinpath(*PurePosixPath(relative).parts)
        if os.path.lexists(target):
            raise ValueError(f"Candidate pre-populates a reserved dependency path: {relative}")


def _write_json_exclusive(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    path.chmod(0o600)


def prepare_candidate(source_root: Path, candidate_root: Path, head: str, manifest_path: Path) -> dict:
    source_root = source_root.resolve(strict=True)
    candidate_root = candidate_root.resolve()
    manifest_path = manifest_path.resolve()
    head = _commit(head, "Candidate head")
    if candidate_root.exists() or manifest_path.exists():
        raise ValueError("Refusing a stale candidate workspace or sandbox manifest")
    if candidate_root.is_relative_to(source_root) or manifest_path.is_relative_to(source_root) or manifest_path.is_relative_to(candidate_root):
        raise ValueError("Candidate workspace and manifest must be outside the source checkout")
    candidate_root.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ragflow-architecture-git-home-", dir=candidate_root.parent) as git_home:
        environment = _git_environment(Path(git_home))
        observed_head = _git(source_root, "rev-parse", f"{head}^{{commit}}", environment=environment)
        if observed_head != head:
            raise ValueError("Candidate head does not resolve exactly in the source checkout")
        try:
            _run(
                [
                    "git",
                    "-c",
                    "core.hooksPath=/dev/null",
                    "clone",
                    "--no-local",
                    "--no-hardlinks",
                    "--no-checkout",
                    str(source_root),
                    str(candidate_root),
                ],
                env=environment,
                timeout=600,
            )
            _git(candidate_root, "remote", "remove", "origin", environment=environment)
            _run(
                [
                    "git",
                    "-c",
                    "core.hooksPath=/dev/null",
                    "-c",
                    "core.autocrlf=false",
                    "-c",
                    "core.symlinks=true",
                    "-C",
                    str(candidate_root),
                    "checkout",
                    "--detach",
                    "--force",
                    head,
                ],
                env=environment,
                timeout=600,
            )
            state = _clone_state(candidate_root, environment)
            if state["head"] != head:
                raise ValueError("Sanitized clone checked out a different candidate head")
            _validate_dependency_targets(candidate_root)
        except Exception:
            if candidate_root.is_dir():
                shutil.rmtree(candidate_root)
            raise
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "tool": {"name": "run_architecture_sandbox", "version": VERSION},
        "candidate_root": str(candidate_root),
        "candidate_head": state["head"],
        "candidate_tree": state["tree"],
        "git_config_sha256": state["config_sha256"],
        "clone_controls": {
            "no_local": True,
            "no_hardlinks": True,
            "detached": True,
            "remote_absent": True,
            "alternates_absent": True,
            "external_git_config_disabled": True,
            "reserved_dependency_paths_absent": True,
        },
    }
    _write_json_exclusive(manifest_path, manifest)
    return manifest


def _validate_manifest(manifest: dict, candidate_root: Path, head: str, state: dict) -> None:
    if not isinstance(manifest, dict) or manifest.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Unsupported sandbox manifest")
    expected_controls = {
        "no_local": True,
        "no_hardlinks": True,
        "detached": True,
        "remote_absent": True,
        "alternates_absent": True,
        "external_git_config_disabled": True,
        "reserved_dependency_paths_absent": True,
    }
    expected = {
        "candidate_root": str(candidate_root),
        "candidate_head": head,
        "candidate_tree": state["tree"],
        "git_config_sha256": state["config_sha256"],
        "clone_controls": expected_controls,
    }
    for field, value in expected.items():
        if manifest.get(field) != value:
            raise ValueError(f"Sandbox manifest mismatch: {field}")


def _tar_entry(archive: tarfile.TarFile, name: str, *, mode: int = 0o755, content: bytes | None = None, link: str | None = None) -> None:
    info = tarfile.TarInfo(name)
    info.mode = mode
    info.uid = 0
    info.gid = 0
    info.mtime = 0
    if link is not None:
        info.type = tarfile.SYMTYPE
        info.linkname = link
        archive.addfile(info)
    elif content is not None:
        info.size = len(content)
        archive.addfile(info, io.BytesIO(content))
    else:
        info.type = tarfile.DIRTYPE
        archive.addfile(info)


def _create_rootfs(path: Path, producer_uid: int, producer_gid: int, runtime_gid: int) -> str:
    passwd = (
        f"root:x:0:0:root:/root:/usr/sbin/nologin\n"
        f"producer:x:{producer_uid}:{producer_gid}:architecture producer:/tmp:/usr/sbin/nologin\n"
        f"runtime:x:{RUNTIME_UID}:{runtime_gid}:candidate runtime:/tmp:/usr/sbin/nologin\n"
    ).encode()
    group_ids = sorted({0, producer_gid, runtime_gid})
    groups = "".join(f"sandbox{gid}:x:{gid}:\n" if gid else "root:x:0:\n" for gid in group_ids).encode()
    with tarfile.open(path, "w") as archive:
        for directory in ("etc", "home", "lib", "lib64", "opt", "root", "sbin", "tmp", "usr", "workspace", "trusted", "evidence"):
            _tar_entry(archive, directory, mode=0o1777 if directory == "tmp" else 0o755)
        for name, target in (("bin", "usr/bin"),):
            _tar_entry(archive, name, link=target)
        _tar_entry(archive, "etc/passwd", mode=0o644, content=passwd)
        _tar_entry(archive, "etc/group", mode=0o644, content=groups)
        _tar_entry(archive, "etc/nsswitch.conf", mode=0o644, content=b"passwd: files\ngroup: files\nhosts: files dns\n")
        _tar_entry(archive, "etc/hosts", mode=0o644, content=b"127.0.0.1 localhost\n::1 localhost\n")
    return _sha256_path(path)


def _bind(source: Path, target: str, *, readonly: bool) -> str:
    value = f"type=bind,source={source},target={target}"
    return value + (",readonly" if readonly else "")


def _docker_security_arguments(
    *,
    image: str,
    name: str,
    candidate_root: Path,
    trusted_root: Path,
    evidence_dir: Path,
    python_prefix: Path | None,
    uid: int,
    gid: int,
    runtime_producer: bool,
) -> list[str]:
    command = [
        "docker",
        "run",
        "--rm",
        "--name",
        name,
        "--network",
        "none",
        "--ipc",
        "none",
        "--init",
        "--read-only",
        "--pids-limit",
        "256",
        "--memory",
        "5g",
        "--cpus",
        "2",
        "--security-opt",
        "no-new-privileges:true",
        "--cap-drop",
        "ALL",
        "--user",
        f"{uid}:{gid}",
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,noexec,size=1073741824,mode=1777",
        "--workdir",
        "/workspace",
        "--mount",
        _bind(candidate_root, "/workspace", readonly=True),
        "--mount",
        _bind(trusted_root, "/trusted", readonly=True),
        "--mount",
        _bind(evidence_dir, "/evidence", readonly=False),
        "--mount",
        _bind(Path("/lib").resolve(), "/lib", readonly=True),
        "--mount",
        _bind(Path("/lib64").resolve(), "/lib64", readonly=True),
        "--mount",
        _bind(Path("/usr"), "/usr", readonly=True),
    ]
    if python_prefix is not None:
        command.extend(["--mount", _bind(python_prefix, str(python_prefix), readonly=True)])
    if runtime_producer:
        command.extend(["--cap-add", "SETUID", "--cap-add", "SETGID"])
    environment = {
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "HOME": "/tmp",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": "/workspace/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
    }
    if runtime_producer:
        environment.update(
            {
                "RAGFLOW_ARCHITECTURE_OS_SANDBOX": "1",
                "RAGFLOW_ARCHITECTURE_RUNTIME_GID": str(gid),
                "RAGFLOW_ARCHITECTURE_RUNTIME_UID": str(RUNTIME_UID),
            }
        )
    for key, value in sorted(environment.items()):
        command.extend(["--env", f"{key}={value}"])
    command.append(image)
    return command


def _validate_negative_probe(payload: dict) -> dict[str, bool]:
    if not isinstance(payload, dict) or payload.get("schema_version") != 1 or payload.get("status") != "PASS":
        raise ValueError("Architecture sandbox negative probe did not pass")
    tool = payload.get("tool")
    if not isinstance(tool, dict) or tool.get("name") != "probe_architecture_sandbox":
        raise ValueError("Architecture sandbox negative probe has an unexpected producer")
    checks = payload.get("checks")
    if not isinstance(checks, dict) or set(checks) != set(REQUIRED_NEGATIVE_CHECKS) or any(checks[name] is not True for name in REQUIRED_NEGATIVE_CHECKS):
        raise ValueError("Architecture sandbox negative probe omitted or failed a required check")
    return {name: True for name in REQUIRED_NEGATIVE_CHECKS}


def _parse_probe(completed: subprocess.CompletedProcess) -> dict[str, bool]:
    if completed.returncode != 0:
        raise ValueError(f"Architecture sandbox negative probe exited with {completed.returncode}: {completed.stderr[-2000:]}")
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    if len(lines) != 1:
        raise ValueError("Architecture sandbox negative probe produced an ambiguous result")
    try:
        payload = json.loads(lines[0])
    except json.JSONDecodeError as error:
        raise ValueError("Architecture sandbox negative probe produced invalid JSON") from error
    return _validate_negative_probe(payload)


def _inject_attestation(
    report: dict,
    *,
    candidate_head: str,
    candidate_tree: str,
    comparison_base: str,
    selection_sha256: str,
    producer_exit_code: int,
    producer_uid: int,
    producer_gid: int,
    runtime_gid: int,
    rootfs_sha256: str,
    negative_checks: dict[str, bool],
    sandbox_runner_sha256: str,
    sandbox_probe_sha256: str,
) -> dict:
    if not isinstance(report, dict) or report.get("schema_version") != 1 or report.get("os_isolation") is not None:
        raise ValueError("Python architecture producer returned an invalid or pre-attested report")
    report_input = report.get("input")
    if not isinstance(report_input, dict):
        raise TypeError("Python architecture report omitted input identity")
    expected_identity = {
        "candidate_sha": candidate_head,
        "pr_base_sha": comparison_base,
        "selection_sha256": selection_sha256,
    }
    for field, value in expected_identity.items():
        if report_input.get(field) != value:
            raise ValueError(f"Python architecture report identity mismatch: {field}")
    runtime_execution = report_input.get("runtime_execution")
    expected_runtime = {
        "mode": "os-sandbox-unprivileged",
        "verified": True,
        "child_uid": RUNTIME_UID,
        "child_gid": runtime_gid,
        "supplementary_groups": [],
        "effective_capabilities": "0000000000000000",
        "no_new_privileges": True,
    }
    if runtime_execution != expected_runtime:
        raise ValueError("Python architecture report lacks the verified unprivileged runtime identity")
    if report.get("exit_code") != producer_exit_code or producer_exit_code not in {0, 1, 2}:
        raise ValueError("Python architecture report and producer exit code disagree")
    report_input["sandbox_runner_sha256"] = sandbox_runner_sha256
    report_input["sandbox_probe_sha256"] = sandbox_probe_sha256
    producer_payload_sha256 = _sha256(_canonical(report))
    report["os_isolation"] = {
        "schema_version": 1,
        "status": "PASS",
        "backend": "docker",
        "candidate_head": candidate_head,
        "candidate_tree": candidate_tree,
        "comparison_base": comparison_base,
        "selection_sha256": selection_sha256,
        "producer_payload_sha256": producer_payload_sha256,
        "rootfs_sha256": rootfs_sha256,
        "constraints": {
            "candidate_mount": "read-only",
            "trusted_mount": "read-only",
            "root_filesystem": "read-only",
            "evidence_access": "producer-only",
            "network": "none",
            "ipc": "none",
            "init_process": True,
            "no_new_privileges": True,
            "producer_capabilities": ["SETGID", "SETUID"],
            "system_runtime_mounts": ["/lib", "/lib64", "/usr"],
            "producer_uid": producer_uid,
            "producer_gid": producer_gid,
            "runtime_uid": RUNTIME_UID,
            "runtime_gid": runtime_gid,
            "runtime_supplementary_groups": [],
            "pids_limit": 256,
            "memory_limit": "5g",
            "cpu_limit": "2",
        },
        "negative_probe": negative_checks,
    }
    return report


def _atomic_json_replace(path: Path, payload: dict) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    if temporary.exists():
        raise ValueError("Refusing stale architecture report staging path")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _python_prefix(candidate_root: Path) -> Path | None:
    launcher = candidate_root / ".venv/bin/python"
    if not launcher.exists():
        raise ValueError("Candidate root Python contract environment is missing")
    real_python = launcher.resolve(strict=True)
    if real_python.is_relative_to(candidate_root) or real_python.is_relative_to(Path("/usr")):
        return None
    prefix = _safe_mount_path(real_python.parent.parent, "Python runtime prefix")
    if not prefix.is_dir():
        raise ValueError("Python runtime prefix is missing")
    return prefix


def _validated_roots(candidate_root: Path, trusted_root: Path, evidence_dir: Path) -> tuple[Path, Path, Path, int, int]:
    if os.name != "posix" or not hasattr(os, "geteuid") or not hasattr(os, "getegid"):
        raise ValueError("Architecture OS isolation requires a POSIX Docker host")
    if shutil.which("docker") is None:
        raise ValueError("Architecture OS isolation requires Docker")
    roots = (
        _safe_mount_path(candidate_root, "Candidate root"),
        _safe_mount_path(trusted_root, "Trusted root"),
        _safe_mount_path(evidence_dir, "Evidence directory"),
    )
    if any(not root.is_dir() for root in roots):
        raise ValueError("Sandbox roots must be existing directories")
    candidate_root, trusted_root, evidence_dir = roots
    if evidence_dir.is_relative_to(candidate_root) or evidence_dir.is_relative_to(trusted_root):
        raise ValueError("Evidence directory must be outside candidate and trusted sources")
    producer_uid = os.geteuid()
    producer_gid = os.getegid()
    if producer_uid in {0, RUNTIME_UID}:
        raise ValueError("Architecture sandbox producer requires a distinct non-root host UID")
    evidence_dir.chmod(0o700)
    return candidate_root, trusted_root, evidence_dir, producer_uid, producer_gid


def _validated_candidate_state(candidate_root: Path, manifest_path: Path, head: str, comparison_base: str) -> dict:
    with tempfile.TemporaryDirectory(prefix="ragflow-architecture-git-home-") as git_home:
        environment = _git_environment(Path(git_home))
        state = _clone_state(candidate_root, environment)
        if state["head"] != head:
            raise ValueError("Candidate workspace head changed after preparation")
        if _git(candidate_root, "rev-parse", f"{comparison_base}^{{commit}}", environment=environment) != comparison_base:
            raise ValueError("Comparison base is unavailable in the sanitized clone")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    _validate_manifest(manifest, candidate_root, head, state)
    return state


def _trusted_sources(trusted_root: Path) -> dict[str, Path]:
    sources = {
        "runner": trusted_root / "tools/quality/run_architecture_sandbox.py",
        "probe": trusted_root / "tools/quality/probe_architecture_sandbox.py",
        "isolated_runner": trusted_root / "tools/quality/run_isolated_python.py",
        "checker": trusted_root / "tools/quality/check_architecture.py",
        "policy": trusted_root / "tools/quality/python-boundaries.yaml",
        "module_map": trusted_root / "tools/quality/module-map.yaml",
        "upstream_base": trusted_root / "tools/quality/upstream-base.json",
    }
    missing = [name for name, path in sources.items() if not path.is_file()]
    if missing:
        raise ValueError("Protected sandbox bundle is incomplete: " + ", ".join(sorted(missing)))
    return sources


def _negative_probe_command(
    *,
    image: str,
    name: str,
    candidate_root: Path,
    trusted_root: Path,
    evidence_dir: Path,
    python_prefix: Path | None,
    runtime_gid: int,
    sentinel_name: str,
) -> list[str]:
    command = _docker_security_arguments(
        image=image,
        name=name,
        candidate_root=candidate_root,
        trusted_root=trusted_root,
        evidence_dir=evidence_dir,
        python_prefix=python_prefix,
        uid=RUNTIME_UID,
        gid=runtime_gid,
        runtime_producer=False,
    )
    command.extend(
        [
            "/workspace/.venv/bin/python",
            "-I",
            "-B",
            "/trusted/tools/quality/probe_architecture_sandbox.py",
            "--workspace",
            "/workspace",
            "--evidence",
            "/evidence",
            "--sentinel",
            sentinel_name,
            "--expected-uid",
            str(RUNTIME_UID),
            "--expected-gid",
            str(runtime_gid),
        ]
    )
    return command


def _producer_command(
    *,
    image: str,
    name: str,
    candidate_root: Path,
    trusted_root: Path,
    evidence_dir: Path,
    python_prefix: Path | None,
    producer_uid: int,
    producer_gid: int,
    comparison_base: str,
    selection_sha256: str,
) -> list[str]:
    command = _docker_security_arguments(
        image=image,
        name=name,
        candidate_root=candidate_root,
        trusted_root=trusted_root,
        evidence_dir=evidence_dir,
        python_prefix=python_prefix,
        uid=producer_uid,
        gid=producer_gid,
        runtime_producer=True,
    )
    command.extend(
        [
            "/workspace/.venv/bin/python",
            "-I",
            "-B",
            "/trusted/tools/quality/run_isolated_python.py",
            "/trusted",
            "tools/quality/check_architecture.py",
            "--root",
            "/workspace",
            "--policy",
            "/trusted/tools/quality/python-boundaries.yaml",
            "--module-map",
            "/trusted/tools/quality/module-map.yaml",
            "--upstream-base",
            "/trusted/tools/quality/upstream-base.json",
            "--base-ref",
            comparison_base,
            "--selection-sha256",
            selection_sha256,
            "--output",
            f"/evidence/{REPORT_NAME}",
        ]
    )
    return command


def _execute_docker_boundary(
    *,
    candidate_root: Path,
    trusted_root: Path,
    evidence_dir: Path,
    python_prefix: Path | None,
    producer_uid: int,
    producer_gid: int,
    comparison_base: str,
    selection_sha256: str,
    report_path: Path,
    sentinel_path: Path,
) -> tuple[str, dict[str, bool], int]:
    unique = secrets.token_hex(8)
    image = f"ragflow-architecture-sandbox:{unique}"
    negative_name = f"ragflow-architecture-negative-{unique}"
    producer_name = f"ragflow-architecture-producer-{unique}"
    runtime_gid = producer_gid
    with tempfile.TemporaryDirectory(prefix="ragflow-architecture-rootfs-") as directory:
        tar_path = Path(directory) / "rootfs.tar"
        rootfs_sha256 = _create_rootfs(tar_path, producer_uid, producer_gid, runtime_gid)
        try:
            _run(["docker", "import", str(tar_path), image], timeout=300)
            negative = _negative_probe_command(
                image=image,
                name=negative_name,
                candidate_root=candidate_root,
                trusted_root=trusted_root,
                evidence_dir=evidence_dir,
                python_prefix=python_prefix,
                runtime_gid=runtime_gid,
                sentinel_name=sentinel_path.name,
            )
            negative_checks = _parse_probe(_run(negative, timeout=120, check=False))
            sentinel_path.unlink()
            producer = _run(
                _producer_command(
                    image=image,
                    name=producer_name,
                    candidate_root=candidate_root,
                    trusted_root=trusted_root,
                    evidence_dir=evidence_dir,
                    python_prefix=python_prefix,
                    producer_uid=producer_uid,
                    producer_gid=producer_gid,
                    comparison_base=comparison_base,
                    selection_sha256=selection_sha256,
                ),
                timeout=1200,
                check=False,
            )
            if producer.returncode not in {0, 1, 2}:
                raise ValueError(f"Python architecture producer exited unexpectedly with {producer.returncode}: {producer.stderr[-4000:]}")
            if not report_path.is_file():
                raise ValueError(f"Python architecture producer emitted no report: {producer.stderr[-4000:]}")
            return rootfs_sha256, negative_checks, producer.returncode
        finally:
            for container in (negative_name, producer_name):
                _run(["docker", "rm", "--force", container], timeout=30, check=False)
            _run(["docker", "image", "rm", "--force", image], timeout=60, check=False)
            if sentinel_path.exists():
                sentinel_path.unlink()


def run_sandbox(
    candidate_root: Path,
    trusted_root: Path,
    evidence_dir: Path,
    manifest_path: Path,
    head: str,
    comparison_base: str,
    selection_sha256: str,
) -> int:
    candidate_root, trusted_root, evidence_dir, producer_uid, producer_gid = _validated_roots(candidate_root, trusted_root, evidence_dir)
    manifest_path = manifest_path.resolve(strict=True)
    head = _commit(head, "Candidate head")
    comparison_base = _commit(comparison_base, "Comparison base")
    if not re.fullmatch(r"[0-9a-f]{64}", selection_sha256):
        raise ValueError("Selection digest must be exact SHA-256")
    runtime_gid = producer_gid
    state = _validated_candidate_state(candidate_root, manifest_path, head, comparison_base)
    required_trusted = _trusted_sources(trusted_root)
    python_prefix = _python_prefix(candidate_root)
    report_path = evidence_dir / REPORT_NAME
    if report_path.exists():
        raise ValueError("Refusing stale Python architecture evidence")
    sentinel_name = f"architecture-sandbox-sentinel-{secrets.token_hex(8)}"
    sentinel_path = evidence_dir / sentinel_name
    sentinel_path.write_bytes(secrets.token_bytes(32))
    sentinel_path.chmod(0o600)
    rootfs_sha256, negative_checks, producer_exit = _execute_docker_boundary(
        candidate_root=candidate_root,
        trusted_root=trusted_root,
        evidence_dir=evidence_dir,
        python_prefix=python_prefix,
        producer_uid=producer_uid,
        producer_gid=producer_gid,
        comparison_base=comparison_base,
        selection_sha256=selection_sha256,
        report_path=report_path,
        sentinel_path=sentinel_path,
    )

    report = json.loads(report_path.read_text(encoding="utf-8"))
    attested = _inject_attestation(
        report,
        candidate_head=head,
        candidate_tree=state["tree"],
        comparison_base=comparison_base,
        selection_sha256=selection_sha256,
        producer_exit_code=producer_exit,
        producer_uid=producer_uid,
        producer_gid=producer_gid,
        runtime_gid=runtime_gid,
        rootfs_sha256=rootfs_sha256,
        negative_checks=negative_checks,
        sandbox_runner_sha256=_sha256_path(required_trusted["runner"]),
        sandbox_probe_sha256=_sha256_path(required_trusted["probe"]),
    )
    _atomic_json_replace(report_path, attested)
    print(
        json.dumps(
            {
                "status": attested["policy_status"],
                "producer_exit_code": producer_exit,
                "candidate_head": head,
                "candidate_tree": state["tree"],
                "negative_checks": len(negative_checks),
                "output": str(report_path),
            },
            sort_keys=True,
        )
    )
    return producer_exit


def _append_github_output(path: Path, manifest_path: Path, candidate_root: Path) -> None:
    output = path.resolve(strict=True)
    values = {"candidate_root": str(candidate_root), "manifest": str(manifest_path)}
    if any("\n" in value or "\r" in value for value in values.values()):
        raise ValueError("Unsafe GitHub output value")
    with output.open("a", encoding="utf-8", newline="\n") as stream:
        for key, value in values.items():
            stream.write(f"{key}={value}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--source-root", type=Path, required=True)
    prepare.add_argument("--candidate-root", type=Path, required=True)
    prepare.add_argument("--head", required=True)
    prepare.add_argument("--manifest", type=Path, required=True)
    prepare.add_argument("--github-output", type=Path)
    run = subparsers.add_parser("run")
    run.add_argument("--candidate-root", type=Path, required=True)
    run.add_argument("--trusted-root", type=Path, required=True)
    run.add_argument("--evidence-dir", type=Path, required=True)
    run.add_argument("--manifest", type=Path, required=True)
    run.add_argument("--head", required=True)
    run.add_argument("--comparison-base", required=True)
    run.add_argument("--selection-sha256", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            manifest = prepare_candidate(args.source_root, args.candidate_root, args.head, args.manifest)
            if args.github_output:
                _append_github_output(args.github_output, args.manifest.resolve(), args.candidate_root.resolve())
            print(json.dumps(manifest, sort_keys=True))
            return 0
        return run_sandbox(
            args.candidate_root,
            args.trusted_root,
            args.evidence_dir,
            args.manifest,
            args.head,
            args.comparison_base,
            args.selection_sha256,
        )
    except (json.JSONDecodeError, OSError, subprocess.SubprocessError, TypeError, ValueError) as error:
        print(f"INCOMPLETE: {error}", file=os.sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
