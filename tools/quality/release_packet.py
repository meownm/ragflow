"""Assemble only named current-attempt artifacts and verify suite completion."""

import argparse
import json
import os
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET

from tools.quality.redact_ci_log import redact
from tools.quality.api_case_evidence import SUITES, verify_report
from tools.quality.release_origin import qualification_producers
from deployment.runner.portable_cli_probe import verify_portable_cli_proof


def sanitize_reports(packet: Path) -> None:
    """Remove credentials before reports become hashed public release assets."""
    for path in packet.rglob("*"):
        if path.suffix in {".log", ".txt"}:
            path.write_text(redact(path.read_text(encoding="utf-8")), encoding="utf-8")
        elif path.suffix == ".xml":
            tree = ET.parse(path)
            for element in tree.iter():
                # Case identity is part of inventory; redact diagnostic payloads only.
                if element.tag in {"failure", "error", "system-out", "system-err", "skipped", "property"}:
                    if element.text:
                        element.text = redact(element.text)
                    for key in ("message", "value"):
                        if key in element.attrib:
                            element.attrib[key] = redact(element.attrib[key])
            tree.write(path, encoding="utf-8", xml_declaration=True)


def verify_api_reports(root: Path, engine: str, *, identity: dict, inventory: Path, image_digest: str) -> None:
    expected_image = image_digest.split("@", 1)[1]
    for suite in SUITES:
        report = root / (suite + "-cases.json")
        summary = verify_report(report, suite=suite, engine=engine, identity={**identity, "image_digest": expected_image}, inventory=inventory)
        if summary != json.loads((root / (suite + "-qualified.json")).read_text()):
            raise ValueError("API gate differs from executed report")
    for name in ("test_sdk_api", "restful_api", "test_web_api", "test_admin_api", "live-browser"):
        report = ET.parse(root / (name + ".xml")).getroot()
        cases = list(report.iter("testcase"))
        if not cases or any(case.find("failure") is not None or case.find("error") is not None for case in cases):
            raise ValueError(f"failed or empty acceptance suite: {name}")
        for case in cases:
            skip = case.find("skipped")
            if skip is not None and name == "live-browser":
                raise ValueError(f"unapproved skipped acceptance case: {name}/{case.get('name')}")


def assemble(incoming: Path, packet: Path, *, identity: dict | None = None, source_root: Path = Path("."), origin_path: Path = Path("producer-origins.json")) -> None:
    identity = identity or {"repository": os.environ["GITHUB_REPOSITORY"], "sha": os.environ["SOURCE_SHA"], "run_id": os.environ["GITHUB_RUN_ID"], "run_attempt": os.environ["GITHUB_RUN_ATTEMPT"]}
    run = identity["run_id"] + "-" + identity["run_attempt"]
    expected = set(qualification_producers(identity["run_id"], int(identity["run_attempt"])))
    if {path.name for path in incoming.iterdir()} != expected:
        raise ValueError("missing or foreign qualification artifact")
    if any(path.is_symlink() for path in incoming.rglob("*")):
        raise ValueError("symlink in qualification artifact")
    combined = incoming / ("browser-results-" + run)
    for browser in ("chromium", "firefox", "webkit"):
        individual = incoming / f"browser-result-{browser}-{run}"
        if {path.name for path in individual.iterdir()} != {browser + ".json", browser + ".xml"}:
            raise ValueError("browser producer lacks receipt or executed cases")
        for suffix in (".json", ".xml"):
            matches = list(combined.rglob(browser + suffix))
            if len(matches) != 1 or matches[0].read_bytes() != (individual / (browser + suffix)).read_bytes():
                raise ValueError("browser gate differs from producer artifact")
    packet.mkdir(parents=True, exist_ok=False)
    origins = json.loads(origin_path.read_text(encoding="utf-8"))
    if len(origins) != len(expected) or {item["name"] for item in origins} != expected or len({item["id"] for item in origins}) != len(expected):
        raise ValueError("incomplete qualification producer origins")
    shutil.copy2(origin_path, packet / "producer-origins.json")
    (packet / "source-inventories").mkdir()
    for source, name in (
        ("test/playwright/mandatory-browser-cases.json", "browser.json"),
        ("test/mandatory-api-cases.json", "api.json"),
        ("tools/quality/release-bootstrap-baseline.json", "bootstrap-baseline.json"),
    ):
        shutil.copy2(source_root / source, packet / "source-inventories" / name)
    (packet / "installers").mkdir()
    for name in ("install.sh", "install.ps1"):
        shutil.copy2(source_root / "tools/scripts" / name, packet / "installers" / name)
    image = incoming / ("image-" + run)
    for name in ("image-build.json", "browser-build.json"):
        shutil.copy2(image / name, packet / name)
    shutil.copytree(image / "native", packet / "native")
    shutil.copytree(image / "upgrade", packet / "upgrade")
    shutil.copy2(image / "runtime-images.json", packet / "runtime-images.json")
    for source, destination in (("python-", "python"), ("preflight-", "preflight"), ("browser-results-", "browser-results")):
        shutil.copytree(incoming / (source + run), packet / destination)
    for engine in ("infinity", "elasticsearch"):
        source = incoming / f"engine-{engine}-{run}"
        verify_api_reports(
            source, engine, identity=identity, inventory=source_root / "test/mandatory-api-cases.json", image_digest=json.loads((source / f"engine-{engine}.json").read_text())["image_digest"]
        )
        shutil.copytree(source, packet / ("engine-" + engine))
        shutil.copy2(source / f"engine-{engine}.json", packet / f"engine-{engine}.json")
    (packet / "cli").mkdir()
    (packet / "cli-proofs").mkdir()
    for system in ("linux", "darwin", "windows"):
        for arch in ("amd64", "arm64"):
            source = incoming / f"cli-{system}-{arch}-{run}"
            files = list(source.iterdir())
            expected_name = f"ragflow-cli-{system}-{arch}" + (".exe" if system == "windows" else "")
            proof_name = expected_name + ".proof.json"
            if {path.name for path in files} != {expected_name, proof_name}:
                raise ValueError("invalid CLI artifact contents")
            verify_portable_cli_proof(
                source / expected_name,
                json.loads((source / proof_name).read_text()),
                source_sha=identity["sha"],
                repository=identity["repository"],
                run_id=identity["run_id"],
                attempt=identity["run_attempt"],
                platform_name=f"{system}-{arch}",
            )
            shutil.copy2(source / expected_name, packet / "cli" / expected_name)
            shutil.copy2(source / proof_name, packet / "cli-proofs" / proof_name)
    sanitize_reports(packet)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("incoming", type=Path)
    parser.add_argument("packet", type=Path)
    args = parser.parse_args()
    assemble(args.incoming, args.packet)
