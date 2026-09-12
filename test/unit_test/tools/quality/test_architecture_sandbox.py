"""Exact positive and negative contracts for the T3 Docker supervisor."""

from __future__ import annotations

import copy
import importlib.util
import os
import stat
import sys
import tarfile
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(os.environ.get("ARCHITECTURE_CANDIDATE_ROOT", Path(__file__).resolve().parents[4])).resolve()


def _load(name: str, path: Path):
    with patch.object(sys, "path", [str(ROOT / "tools/quality"), *sys.path]):
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


sandbox = _load("architecture_sandbox_supervisor", ROOT / "tools/quality/run_architecture_sandbox.py")
probe = _load("architecture_sandbox_probe", ROOT / "tools/quality/probe_architecture_sandbox.py")
policy_checker = _load("architecture_sandbox_policy_checker", ROOT / "tools/quality/check_architecture_policy.py")


def _producer_report() -> dict:
    return {
        "schema_version": 1,
        "tool": {"name": "check_architecture", "version": "fixture"},
        "policy_status": "PASS",
        "exit_code": 0,
        "input": {
            "candidate_sha": "b" * 40,
            "pr_base_sha": "a" * 40,
            "selection_sha256": "c" * 64,
            "runtime_execution": {
                "mode": "os-sandbox-unprivileged",
                "verified": True,
                "child_uid": 65534,
                "child_gid": 65534,
                "supplementary_groups": [],
                "effective_capabilities": "0000000000000000",
                "no_new_privileges": True,
            },
        },
        "findings": [],
    }


def _negative_checks() -> dict[str, bool]:
    return {name: True for name in sandbox.REQUIRED_NEGATIVE_CHECKS}


def test_sandbox_attestation_binds_report_and_negative_probe():
    report = sandbox._inject_attestation(
        _producer_report(),
        candidate_head="b" * 40,
        candidate_tree="d" * 40,
        comparison_base="a" * 40,
        selection_sha256="c" * 64,
        producer_exit_code=0,
        evidence_owner_uid=1000,
        producer_gid=1001,
        rootfs_sha256="e" * 64,
        negative_checks=_negative_checks(),
        sandbox_runner_sha256="f" * 64,
        sandbox_probe_sha256="1" * 64,
    )
    assert policy_checker._os_isolation_problem(report) is None
    assert report["input"]["sandbox_runner_sha256"] == "f" * 64
    assert report["input"]["sandbox_probe_sha256"] == "1" * 64

    forged = copy.deepcopy(report)
    forged["findings"].append({"kind": "forged-after-attestation"})
    assert policy_checker._os_isolation_problem(forged) == "architecture OS-isolation attestation does not bind the producer payload"

    docker_arguments = sandbox._docker_security_arguments(
        image="fixture:latest",
        name="fixture",
        candidate_root=Path("/candidate"),
        trusted_root=Path("/trusted-source"),
        evidence_mounts=((Path("/supervisor-evidence/architecture-python.json"), "/evidence/architecture-python.json"),),
        python_runtime_mounts=((Path("/runtime/python-real"), "/runtime/python-alias"),),
        uid=0,
        gid=1001,
        runtime_producer=True,
    )
    assert ["--network", "none"] == docker_arguments[docker_arguments.index("--network") : docker_arguments.index("--network") + 2]
    assert ["--security-opt", "no-new-privileges:true"] == docker_arguments[docker_arguments.index("--security-opt") : docker_arguments.index("--security-opt") + 2]
    assert "--read-only" in docker_arguments
    assert "--init" in docker_arguments
    assert docker_arguments.count("--cap-drop") == 1
    assert docker_arguments.count("--cap-add") == 2
    assert "RAGFLOW_ARCHITECTURE_RUNTIME_UID=65534" in docker_arguments
    assert "RAGFLOW_ARCHITECTURE_RUNTIME_GID=65534" in docker_arguments
    assert "0:1001" == docker_arguments[docker_arguments.index("--user") + 1]
    mounts = [docker_arguments[index + 1] for index, argument in enumerate(docker_arguments) if argument == "--mount"]
    assert any("target=/lib,readonly" in mount for mount in mounts)
    assert any("target=/lib64,readonly" in mount for mount in mounts)
    runtime_mounts = [mount for mount in mounts if "target=/runtime/python-alias" in mount]
    assert len(runtime_mounts) == 1
    assert runtime_mounts[0].startswith("type=bind,source=")
    assert runtime_mounts[0].endswith(",target=/runtime/python-alias,readonly")
    evidence_mounts = [mount for mount in mounts if "target=/evidence" in mount]
    assert evidence_mounts == [sandbox._bind(Path("/supervisor-evidence/architecture-python.json"), "/evidence/architecture-python.json", readonly=False)]

    weakened = copy.deepcopy(report)
    weakened["os_isolation"]["constraints"]["report_mount"] = "directory"
    assert policy_checker._os_isolation_problem(weakened) == "architecture OS-isolation attestation weakens a required constraint"


def test_sandbox_commands_separate_negative_directory_from_producer_report_file():
    shared = {
        "image": "fixture:latest",
        "candidate_root": Path("/candidate"),
        "trusted_root": Path("/trusted-source"),
        "report_path": Path("/supervisor-evidence/architecture-python.json"),
        "python_runtime_mounts": (),
    }
    negative = sandbox._negative_probe_command(
        **shared,
        name="negative",
        evidence_dir=Path("/supervisor-evidence"),
        sentinel_name="sentinel",
    )
    negative_mounts = [negative[index + 1] for index, argument in enumerate(negative) if argument == "--mount"]
    assert sandbox._bind(Path("/supervisor-evidence"), "/negative-evidence", readonly=False) in negative_mounts
    assert (
        sandbox._bind(
            Path("/supervisor-evidence/architecture-python.json"),
            "/evidence/architecture-python.json",
            readonly=False,
        )
        in negative_mounts
    )
    assert negative[negative.index("--user") + 1] == "65534:65534"
    assert negative[negative.index("--report") + 1] == "/evidence/architecture-python.json"

    producer = sandbox._producer_command(
        **shared,
        name="producer",
        producer_gid=1001,
        comparison_base="a" * 40,
        selection_sha256="b" * 64,
    )
    producer_mounts = [producer[index + 1] for index, argument in enumerate(producer) if argument == "--mount"]
    writable_evidence = [mount for mount in producer_mounts if ",target=/evidence" in mount]
    assert writable_evidence == [
        sandbox._bind(
            Path("/supervisor-evidence/architecture-python.json"),
            "/evidence/architecture-python.json",
            readonly=False,
        )
    ]
    assert producer[producer.index("--user") + 1] == "0:1001"


def test_sandbox_rejects_failed_negative_probe_and_unsafe_clone_config(tmp_path):
    payload = {
        "schema_version": 1,
        "tool": {"name": "probe_architecture_sandbox", "version": "fixture"},
        "status": "PASS",
        "checks": _negative_checks(),
    }
    assert sandbox._validate_negative_probe(payload) == _negative_checks()

    failed = copy.deepcopy(payload)
    failed["checks"]["network_denied"] = False
    with pytest.raises(ValueError, match="omitted or failed"):
        sandbox._validate_negative_probe(failed)

    unsafe = b"core.repositoryformatversion\0http.https://example.invalid/.extraheader\0credential.helper\0"
    assert sandbox._unsafe_git_config_keys(unsafe) == ["credential.helper", "http.https://example.invalid/.extraheader"]
    candidate = tmp_path / "candidate"
    (candidate / "services/asr-online-service").mkdir(parents=True)
    (candidate / "web").mkdir()
    sandbox._validate_dependency_targets(candidate)

    (candidate / ".venv").mkdir()
    with pytest.raises(ValueError, match="reserved dependency path"):
        sandbox._validate_dependency_targets(candidate)

    rootfs = tmp_path / "rootfs.tar"
    sandbox._create_rootfs(rootfs, producer_gid=1001)
    with tarfile.open(rootfs) as archive:
        assert archive.getmember("sbin").isdir()
        assert archive.getmember("lib").isdir()
        assert archive.getmember("lib64").isdir()
        passwd = archive.extractfile("etc/passwd").read().decode()
        assert "root:x:0:0:" in passwd
        assert "runtime:x:65534:65534:" in passwd
        assert "producer:" not in passwd


@pytest.mark.skipif(os.name != "posix", reason="POSIX ownership is part of the Docker-host contract")
def test_report_staging_file_has_exact_owner_group_and_mode(tmp_path):
    owner_uid = os.geteuid()
    owner_gid = os.getegid()
    if owner_uid in {0, 65534} or owner_gid in {0, 65534}:
        pytest.skip("Fixture needs distinct non-root host ownership")
    report = tmp_path / sandbox.REPORT_NAME
    sandbox._prepare_report_file(report, owner_uid, owner_gid)
    metadata = report.lstat()
    assert (metadata.st_uid, metadata.st_gid) == (owner_uid, owner_gid)
    assert stat.S_IMODE(metadata.st_mode) == sandbox.REPORT_MODE
    sandbox._validate_report_file(report, owner_uid, owner_gid, empty=True)

    report.write_bytes(b"{}\n")
    sandbox._validate_report_file(report, owner_uid, owner_gid, empty=False)
    report.chmod(0o600)
    with pytest.raises(ValueError, match="mode changed"):
        sandbox._validate_report_file(report, owner_uid, owner_gid, empty=False)


def test_python_runtime_mounts_preserve_only_narrow_aliases(tmp_path):
    runtime = tmp_path / "managed-python"
    runtime_bin = runtime / "bin"
    runtime_bin.mkdir(parents=True)
    real_python = runtime_bin / "python3.13"
    real_python.write_bytes(b"fixture")

    assert sandbox._runtime_mounts_for_python(real_python, real_python) == ((runtime.resolve(), str(runtime)),)

    broad_target = tmp_path / "runner" / "bin" / "python3.13"
    broad_target.parent.mkdir(parents=True)
    with pytest.raises(ValueError, match="narrow runtime prefix"):
        sandbox._runtime_mounts_for_python(broad_target, real_python)

    if os.name != "nt":
        alias = tmp_path / "managed-python-alias"
        alias.symlink_to(runtime, target_is_directory=True)
        assert sandbox._runtime_mounts_for_python(alias / "bin/python3.13", real_python) == (
            (runtime.resolve(), str(alias)),
            (runtime.resolve(), str(runtime.resolve())),
        )


def test_negative_probe_allows_only_its_exact_read_only_workspace(tmp_path):
    workspace = tmp_path / "candidate"
    workspace.mkdir()
    completed = probe.subprocess.CompletedProcess(args=[], returncode=0, stdout=b"", stderr=b"")
    with patch.object(probe.subprocess, "run", side_effect=(completed, completed)) as run:
        assert probe._git_config_sanitized(workspace)

    expected_prefix = ["git", "-c", f"safe.directory={workspace}", "-C", str(workspace)]
    assert run.call_args_list[0].args[0][:5] == expected_prefix
    assert run.call_args_list[1].args[0][:5] == expected_prefix


def test_failed_negative_probe_preserves_trusted_json_diagnostic():
    completed = sandbox.subprocess.CompletedProcess(
        args=[],
        returncode=2,
        stdout='{"status":"FAIL","checks":{"git_config_sanitized":false}}\n',
        stderr="",
    )
    with pytest.raises(ValueError, match="git_config_sanitized"):
        sandbox._parse_probe(completed)


def test_negative_probe_identity_allows_only_redundant_primary_group():
    expected_uid = 65534
    expected_gid = 65534
    assert probe._identity_matches({"uid": expected_uid, "gid": expected_gid, "groups": []}, expected_uid, expected_gid)
    assert probe._identity_matches({"uid": expected_uid, "gid": expected_gid, "groups": [expected_gid]}, expected_uid, expected_gid)
    assert not probe._identity_matches({"uid": expected_uid, "gid": expected_gid, "groups": [0]}, expected_uid, expected_gid)
    assert not probe._identity_matches({"uid": expected_uid, "gid": expected_gid, "groups": [expected_gid, expected_gid]}, expected_uid, expected_gid)
