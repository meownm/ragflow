"""Exercise an already-built portable CLI on its native release runner."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import tempfile

SYSTEMS = {"linux": "Linux", "darwin": "Darwin", "windows": "Windows"}
ARCHITECTURES = {"amd64": {"amd64", "x86_64"}, "arm64": {"arm64", "aarch64"}}
CASES = ("help", "local-text", "pdf-deepdoc-unavailable", "pdf-plain-text-unavailable", *(f"office-{extension}-unavailable" for extension in ("doc", "docx", "xls", "xlsx", "ppt", "pptx")))
HEX = re.compile(r"^[0-9a-f]{64}$")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_portable_cli_proof(binary: Path, proof: dict, *, source_sha: str, repository: str, run_id: str, attempt: str | int, platform_name: str) -> None:
    """Verify exact current-attempt proof and the tested binary before packaging."""
    expected_fields = {"schema", "purpose", "status", "profile", "source_sha", "repository", "run_id", "attempt", "platform", "runner", "binary", "toolchain", "cases"}
    if not isinstance(proof, dict) or set(proof) != expected_fields:
        raise ValueError("Portable CLI proof schema is not closed")
    if (
        type(proof["schema"]) is not int
        or proof["schema"] != 1
        or proof["purpose"] != "portable-cli-final-binary"
        or proof["status"] != "PASS"
        or proof["profile"] != "portable-no-cgo"
        or proof["source_sha"] != source_sha
        or proof["repository"] != repository
        or proof["run_id"] != str(run_id)
        or proof["attempt"] != str(attempt)
        or proof["platform"] != platform_name
        or not re.fullmatch(r"[0-9a-f]{40}", source_sha)
    ):
        raise ValueError("Portable CLI proof identity or status differs from qualification")
    system, architecture = platform_name.split("-", 1)
    if system not in SYSTEMS or architecture not in ARCHITECTURES:
        raise ValueError("Unsupported portable CLI platform")
    runner = proof["runner"]
    if (
        not isinstance(runner, dict)
        or set(runner) != {"system", "machine"}
        or runner["system"] != SYSTEMS[system]
        or not isinstance(runner["machine"], str)
        or runner["machine"].lower() not in ARCHITECTURES[architecture]
    ):
        raise ValueError("Portable CLI was not exercised on the required native runner")
    expected_name = "ragflow-cli-" + platform_name + (".exe" if system == "windows" else "")
    identity = proof["binary"]
    if (
        not isinstance(identity, dict)
        or set(identity) != {"name", "size", "sha256"}
        or binary.name != expected_name
        or identity["name"] != binary.name
        or binary.is_symlink()
        or not binary.is_file()
        or type(identity["size"]) is not int
        or identity["size"] != binary.stat().st_size
        or identity["size"] <= 0
        or identity["sha256"] != sha256(binary)
    ):
        raise ValueError("Portable CLI binary differs from exercised binary")
    toolchain = proof["toolchain"]
    if (
        not isinstance(toolchain, dict)
        or set(toolchain) != {"go", "build"}
        or not isinstance(toolchain["go"], str)
        or not re.fullmatch(r"go1\.\d+(?:\.\d+)?(?:[a-z0-9.-]+)?", toolchain["go"])
        or toolchain["build"] != {"CGO_ENABLED": "0", "GOOS": system, "GOARCH": architecture}
    ):
        raise ValueError("Portable CLI build metadata does not identify no-CGO target")
    cases = proof["cases"]
    if not isinstance(cases, list) or not all(isinstance(case, dict) for case in cases) or [case.get("name") for case in cases] != list(CASES):
        raise ValueError("Portable CLI cases missing, duplicated, extra or out of order")
    for case in cases:
        if (
            set(case) != {"name", "status", "returncode", "stdout_sha256", "stderr_sha256"}
            or case["status"] != "PASS"
            or type(case["returncode"]) is not int
            or case["returncode"] != (0 if case["name"] in {"help", "local-text"} else 1)
            or not isinstance(case["stdout_sha256"], str)
            or not HEX.fullmatch(case["stdout_sha256"])
            or not isinstance(case["stderr_sha256"], str)
            or not HEX.fullmatch(case["stderr_sha256"])
        ):
            raise ValueError("Portable CLI case result is incomplete or failed")


def build_metadata(binary: Path) -> dict:
    result = subprocess.run(["go", "version", "-m", str(binary)], capture_output=True, check=True, timeout=60)
    lines = result.stdout.decode().splitlines()
    if not lines:
        raise ValueError("Go build metadata unavailable")
    version = lines[0].rsplit(": ", 1)[-1].strip()
    settings = {}
    for line in lines[1:]:
        if line.strip().startswith("build\t"):
            value = line.strip().split("\t", 1)[1]
            key, separator, value = value.partition("=")
            if separator:
                settings[key] = value
    return {"go": version, "build": {key: settings.get(key) for key in ("CGO_ENABLED", "GOOS", "GOARCH")}}


def exercise(binary: Path, platform_name: str, source_sha: str) -> dict:
    if binary.is_symlink():
        raise ValueError("Portable CLI must not be a symlink")
    binary = binary.resolve(strict=True)
    before = sha256(binary)
    proof = dict(
        schema=1,
        purpose="portable-cli-final-binary",
        status="INCOMPLETE",
        profile="portable-no-cgo",
        source_sha=source_sha,
        repository=os.environ["GITHUB_REPOSITORY"],
        run_id=os.environ["GITHUB_RUN_ID"],
        attempt=os.environ["GITHUB_RUN_ATTEMPT"],
        platform=platform_name,
        runner={"system": platform.system(), "machine": platform.machine()},
        binary={"name": binary.name, "size": binary.stat().st_size, "sha256": before},
        toolchain=build_metadata(binary),
        cases=[],
    )
    with tempfile.TemporaryDirectory(prefix="ragflow-portable-cli-") as folder:
        work = Path(folder)
        (work / "qualification.txt").write_text("RAGFlow portable text qualification marker", encoding="utf-8")
        # Non-empty PDF is sufficient to reach the supported no-CGO unavailable
        # contract. It must not silently accept an empty placeholder PDF result.
        (work / "qualification.pdf").write_bytes(b"%PDF-1.4\nportable qualification native-disabled input\n%%EOF\n")
        for extension in ("doc", "docx", "xls", "xlsx", "ppt", "pptx"):
            (work / f"qualification.{extension}").write_bytes(b"portable qualification native-disabled input")
        environment = {**os.environ, "HOME": folder, "USERPROFILE": folder}
        requests = [
            ("help", ["--help"], 0, "RAGFlow CLI"),
            ("local-text", ["parse file 'qualification.txt';"], 0, "RAGFlow portable text qualification marker"),
            ("pdf-deepdoc-unavailable", ["parse file 'qualification.pdf';"], 1, "PDF backend unavailable in this build"),
            ("pdf-plain-text-unavailable", ["parse file 'qualification.pdf' doc_parse 'plain_text';"], 1, "PDF backend unavailable in this build"),
            *(
                (f"office-{extension}-unavailable", [f"parse file 'qualification.{extension}';"], 1, f"{extension.upper()} parser requires CGO (office_oxide)")
                for extension in ("doc", "docx", "xls", "xlsx", "ppt", "pptx")
            ),
        ]
        for name, arguments, exit_code, marker in requests:
            result = subprocess.run([str(binary), *arguments], cwd=work, env=environment, capture_output=True, check=False, timeout=60)
            output = (result.stdout + result.stderr).decode(errors="replace")
            passed = result.returncode == exit_code and marker in output
            proof["cases"].append(
                dict(
                    name=name,
                    status="PASS" if passed else "FAIL",
                    returncode=result.returncode,
                    stdout_sha256=hashlib.sha256(result.stdout).hexdigest(),
                    stderr_sha256=hashlib.sha256(result.stderr).hexdigest(),
                )
            )
    proof["status"] = "PASS" if all(case["status"] == "PASS" for case in proof["cases"]) and sha256(binary) == before else "FAIL"
    if proof["status"] == "PASS":
        verify_portable_cli_proof(binary, proof, source_sha=source_sha, repository=proof["repository"], run_id=proof["run_id"], attempt=proof["attempt"], platform_name=platform_name)
    return proof


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", type=Path)
    parser.add_argument("--platform", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        proof = exercise(args.binary, args.platform, args.source_sha)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        proof = {"schema": 1, "purpose": "portable-cli-final-binary", "status": "FAIL", "reason": type(error).__name__}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": proof["status"], "platform": args.platform, "output": str(args.output)}))
    return 0 if proof["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
