"""Closed proof, binary identity and executable portable-command qualification."""

from copy import deepcopy
from pathlib import Path
import subprocess

import pytest
import yaml

from deployment.runner import portable_cli_probe as probe

ROOT = Path(__file__).parents[4]
SHA = "a" * 40


def proof_for(binary, platform_name):
    system, architecture = platform_name.split("-")
    return dict(
        schema=1,
        purpose="portable-cli-final-binary",
        status="PASS",
        profile="portable-no-cgo",
        source_sha=SHA,
        repository="meownm/ragflow",
        run_id="123",
        attempt="2",
        platform=platform_name,
        runner={"system": probe.SYSTEMS[system], "machine": "x86_64" if architecture == "amd64" else "arm64"},
        binary={"name": binary.name, "size": binary.stat().st_size, "sha256": probe.sha256(binary)},
        toolchain={"go": "go1.26.4", "build": {"CGO_ENABLED": "0", "GOOS": system, "GOARCH": architecture}},
        cases=[dict(name=name, status="PASS", returncode=0 if name in {"help", "local-text"} else 1, stdout_sha256="b" * 64, stderr_sha256="c" * 64) for name in probe.CASES],
    )


def verify(binary, proof, platform_name="linux-amd64"):
    probe.verify_portable_cli_proof(binary, proof, source_sha=SHA, repository="meownm/ragflow", run_id="123", attempt=2, platform_name=platform_name)


@pytest.mark.parametrize("platform_name", [f"{system}-{arch}" for system in probe.SYSTEMS for arch in probe.ARCHITECTURES])
def test_all_six_native_platform_proofs(tmp_path, platform_name):
    binary = tmp_path / ("ragflow-cli-" + platform_name + (".exe" if platform_name.startswith("windows-") else ""))
    binary.write_bytes(b"the tested binary")
    verify(binary, proof_for(binary, platform_name), platform_name)


@pytest.mark.parametrize(
    "field,value",
    [
        ("status", "INCOMPLETE"),
        ("source_sha", "d" * 40),
        ("repository", "other/ragflow"),
        ("run_id", "124"),
        ("attempt", "1"),
        ("profile", "native"),
        ("platform", "windows-arm64"),
    ],
)
def test_foreign_or_incomplete_proof_rejected(tmp_path, field, value):
    binary = tmp_path / "ragflow-cli-linux-amd64"
    binary.write_bytes(b"the tested binary")
    proof = proof_for(binary, "linux-amd64")
    proof[field] = value
    with pytest.raises(ValueError):
        verify(binary, proof)


@pytest.mark.parametrize("mutation", ["missing", "extra", "duplicate", "failed", "crashed", "wrong-target", "cgo", "cross-runner", "extra-field"])
def test_negative_binary_qualification_cases(tmp_path, mutation):
    binary = tmp_path / "ragflow-cli-linux-amd64"
    binary.write_bytes(b"the tested binary")
    proof = proof_for(binary, "linux-amd64")
    if mutation == "missing":
        proof["cases"].pop()
    elif mutation == "extra":
        proof["cases"].append({"name": "unknown"})
    elif mutation == "duplicate":
        proof["cases"][-1] = deepcopy(proof["cases"][0])
    elif mutation == "failed":
        proof["cases"][0]["status"] = "FAIL"
    elif mutation == "crashed":
        proof["cases"][-1]["returncode"] = 137
    elif mutation == "wrong-target":
        proof["toolchain"]["build"]["GOARCH"] = "arm64"
    elif mutation == "cgo":
        proof["toolchain"]["build"]["CGO_ENABLED"] = "1"
    elif mutation == "cross-runner":
        proof["runner"]["machine"] = "arm64"
    else:
        proof["hidden"] = True
    with pytest.raises(ValueError):
        verify(binary, proof)


def test_substituted_binary_rejected(tmp_path):
    binary = tmp_path / "ragflow-cli-linux-amd64"
    binary.write_bytes(b"the tested binary")
    proof = proof_for(binary, "linux-amd64")
    binary.write_bytes(b"different binary")
    with pytest.raises(ValueError, match="differs"):
        verify(binary, proof)


@pytest.mark.parametrize("failure", [None, "native-success", "generic-error", "empty-text", "binary-mutated"])
def test_ready_binary_is_exercised_without_rebuilding(tmp_path, monkeypatch, failure):
    binary = tmp_path / "ragflow-cli-linux-amd64"
    binary.write_bytes(b"the tested binary")
    monkeypatch.setenv("GITHUB_REPOSITORY", "meownm/ragflow")
    monkeypatch.setenv("GITHUB_RUN_ID", "123")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
    monkeypatch.setattr(probe.platform, "system", lambda: "Linux")
    monkeypatch.setattr(probe.platform, "machine", lambda: "x86_64")
    calls = []

    def run(arguments, **kwargs):
        calls.append(arguments)
        if arguments[0] == "go":
            assert arguments[:3] == ["go", "version", "-m"]
            return subprocess.CompletedProcess(arguments, 0, b"binary: go1.26.4\n\tbuild\tCGO_ENABLED=0\n\tbuild\tGOOS=linux\n\tbuild\tGOARCH=amd64\n", b"")
        assert arguments[0] == str(binary)
        assert (Path(kwargs["cwd"]) / "qualification.txt").is_file()
        request = arguments[1]
        if request == "--help":
            if failure == "binary-mutated":
                binary.write_bytes(b"substituted during execution")
            return subprocess.CompletedProcess(arguments, 0, b"RAGFlow CLI Client", b"")
        if "qualification.txt" in request:
            output = b"SUCCESS" if failure == "empty-text" else b"RAGFlow portable text qualification marker"
            return subprocess.CompletedProcess(arguments, 0, output, b"")
        if failure == "native-success":
            return subprocess.CompletedProcess(arguments, 0, b"SUCCESS", b"")
        if failure == "generic-error":
            return subprocess.CompletedProcess(arguments, 1, b"unknown command", b"")
        if "qualification.pdf" in request:
            return subprocess.CompletedProcess(arguments, 1, b"PDF backend unavailable in this build", b"")
        extension = request.split("qualification.", 1)[1].split("'", 1)[0]
        return subprocess.CompletedProcess(arguments, 1, f"{extension.upper()} parser requires CGO (office_oxide)".encode(), b"")

    monkeypatch.setattr(probe.subprocess, "run", run)
    report = probe.exercise(binary, "linux-amd64", SHA)
    assert report["status"] == ("PASS" if failure is None else "FAIL")
    assert [case["name"] for case in report["cases"]] == list(probe.CASES)
    assert len(calls) == 11
    assert not any("build" in call for call in calls)


def test_portable_workflow_uses_same_probe_for_build_and_replay():
    workflow = yaml.safe_load((ROOT / ".github/workflows/release-cli.yml").read_text())
    job = workflow["jobs"]["build_cli"]
    assert {(item["goos"], item["goarch"]) for item in job["strategy"]["matrix"]["include"]} == {(system, architecture) for system in probe.SYSTEMS for architecture in probe.ARCHITECTURES}
    steps = job["steps"]
    python_setup = next(step for step in steps if step.get("uses", "").startswith("actions/setup-python@"))
    assert python_setup["with"]["python-version"] == "3.13"
    script = next(step["run"] for step in steps if step.get("name") == "Build and exercise portable CLI")
    assert 'if [[ -n "$REPLAY_ARTIFACT" ]]' in script
    assert 'test -f "replay/qualification/cli/' in script
    assert script.index("portable_cli_probe.py") > script.index("fi\n")
    assert '--output "$output.proof.json"' in script
    wrapper = (ROOT / "deployment/runner/build-portable-cli.sh").read_text()
    assert 'test "${CGO_ENABLED:?}" = 0' in wrapper
    assert "download_deps" not in wrapper
