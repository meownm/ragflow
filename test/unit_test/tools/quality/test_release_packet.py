"""Exercise transport provenance with synthetic jobs, never claim live qualification."""

import base64
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import shutil
import zipfile

import pytest

from tools.quality.api_case_evidence import ENGINES, SUITES, verify_report
from tools.quality import release_origin as origin
from tools.quality.release_packet import assemble
from test.unit_test.tools.quality.test_portable_cli_probe import proof_for


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


@pytest.fixture
def packet(tmp_path, monkeypatch):
    identity = {"repository": "meownm/ragflow", "sha": "a" * 40, "run_id": "123", "run_attempt": "2"}
    source = tmp_path / "source"
    browser = {"schema": 1, "purpose": "mandatory-isolated-browser-regression", "expected_count": 1, "nodeids": ["test/playwright/e2e/test_example.py::test_required"]}
    policy = {
        "schema": 1,
        "purpose": "mandatory-release-api-cases",
        "model_profile": "local",
        "suites": {suite: {engine: {"required": [f"test/testcases/{suite}/test_example.py::test_required"], "not_applicable": {}} for engine in ENGINES} for suite in SUITES},
    }
    write_json(source / "test/playwright/mandatory-browser-cases.json", browser)
    write_json(source / "test/mandatory-api-cases.json", policy)
    from tools.quality.release_evidence import BOOTSTRAP_BASELINE

    baseline = source / "tools/quality/release-bootstrap-baseline.json"
    baseline.parent.mkdir(parents=True, exist_ok=True)
    baseline.write_bytes(BOOTSTRAP_BASELINE.read_bytes())
    for name in ("install.sh", "install.ps1"):
        target = source / "tools/scripts" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("installer from qualified source", encoding="utf-8")
    incoming = tmp_path / "incoming"
    for name in origin.qualification_producers("123", 2):
        (incoming / name).mkdir(parents=True)
    image = incoming / "image-123-2"
    for name in ("image-build.json", "browser-build.json", "runtime-images.json", "native/proof.json"):
        write_json(image / name, {"transport_fixture": name})
    write_json(
        image / "upgrade/qualified-upgrade.json",
        {
            "purpose": "previous-qualified-image-upgrade",
            "status": "PASS",
            "cleanup": "PASS",
            "source_sha": identity["sha"],
            "image_digest": "registry/staging@sha256:" + "b" * 64,
            "previous_receipt_sha256": "c" * 64,
            "repeated_initializer": True,
            "previous_source_sha": "d" * 40,
            "previous_image_digest": "registry/previous@sha256:" + "e" * 64,
            "candidate_restore": True,
            "previous_restore": True,
        },
    )
    for browser_name in ("chromium", "firefox", "webkit"):
        value = {
            "browser": browser_name,
            "sha": identity["sha"],
            "result": "success",
            "tests": 1,
            "inventory_sha256": hashlib.sha256((source / "test/playwright/mandatory-browser-cases.json").read_bytes()).hexdigest(),
            "manifest_sha256": hashlib.sha256((image / "browser-build.json").read_bytes()).hexdigest(),
        }
        write_json(incoming / "browser-results-123-2" / (browser_name + ".json"), value)
        write_json(incoming / f"browser-result-{browser_name}-123-2" / (browser_name + ".json"), value)
        for directory in (incoming / "browser-results-123-2", incoming / f"browser-result-{browser_name}-123-2"):
            (directory / (browser_name + ".xml")).write_text('<testsuite tests="1"><testcase classname="test.playwright.e2e.test_example" name="test_required" /></testsuite>')
    for engine in ENGINES:
        root = incoming / f"engine-{engine}-123-2"
        write_json(root / f"engine-{engine}.json", {"image_digest": "registry/staging@sha256:" + "b" * 64})
        for suite in SUITES:
            node = policy["suites"][suite][engine]["required"][0]
            execution = {
                "schema": 1,
                "purpose": "api-case-execution",
                "exit_status": 0,
                "collection_errors": [],
                "identity": {**identity, "image_digest": "sha256:" + "b" * 64, "suite": suite, "engine": engine, "model_profile": "local"},
                "collected": [node],
                "cases": [{"nodeid": node, "phases": [{"when": phase, "outcome": "passed", "wasxfail": False} for phase in ("setup", "call", "teardown")]}],
            }
            write_json(root / (suite + "-cases.json"), execution)
            summary = verify_report(
                root / (suite + "-cases.json"), suite=suite, engine=engine, identity={**identity, "image_digest": "sha256:" + "b" * 64}, inventory=source / "test/mandatory-api-cases.json"
            )
            write_json(root / (suite + "-qualified.json"), summary)
        for suite in (*SUITES, "live-browser"):
            (root / (suite + ".xml")).write_text('<testsuite><testcase name="test_required" /></testsuite>', encoding="utf-8")
    for system in ("linux", "darwin", "windows"):
        for architecture in ("amd64", "arm64"):
            platform = system + "-" + architecture
            binary = incoming / f"cli-{platform}-123-2" / ("ragflow-cli-" + platform + (".exe" if system == "windows" else ""))
            binary.write_bytes(b"synthetic tested binary")
            write_json(binary.with_name(binary.name + ".proof.json"), proof_for(binary, platform))
    archives, origins, jobs = {}, [], []
    for identifier, (name, producer) in enumerate(origin.qualification_producers("123", 2).items(), 10):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            for path in (incoming / name).rglob("*"):
                if path.is_file():
                    archive.writestr(path.relative_to(incoming / name).as_posix(), path.read_bytes())
        archives[identifier] = stream.getvalue()
        origins.append(
            {
                "id": identifier,
                "name": name,
                "expired": False,
                "digest": "sha256:" + hashlib.sha256(stream.getvalue()).hexdigest(),
                "created_at": "2026-10-11T10:01:00Z",
                "workflow_run": {"id": 123, "head_sha": identity["sha"], "repository_id": 1, "head_repository_id": 1},
            }
        )
        jobs.append(
            {
                "name": "qualification / " + producer,
                "run_attempt": 2,
                "head_sha": identity["sha"],
                "conclusion": "success",
                "started_at": "2026-10-11T10:00:00Z",
                "completed_at": "2026-10-11T10:02:00Z",
            }
        )
    origin_path = tmp_path / "producer-origins.json"
    write_json(origin_path, origins)
    destination = tmp_path / "packet"
    assemble(incoming, destination, identity=identity, source_root=source, origin_path=origin_path)
    receipt = {
        "ci": {"repository": identity["repository"], "run_id": "123", "attempt": 2},
        "source_revision": identity["sha"],
        "image": {"staging_digest": "registry/staging@sha256:" + "b" * 64},
        "artifacts": {"browser_inventory_sha256": hashlib.sha256((source / "test/playwright/mandatory-browser-cases.json").read_bytes()).hexdigest()},
    }
    from tools.quality.release_evidence import verify_runtime_upgrade

    receipt["recovery"] = verify_runtime_upgrade(json.loads((destination / "upgrade/qualified-upgrade.json").read_text()), identity["sha"], receipt["image"]["staging_digest"])
    run = {
        "id": 123,
        "run_attempt": 2,
        "head_sha": identity["sha"],
        "repository": {"id": 1, "full_name": identity["repository"]},
        "head_repository": {"id": 1},
        "event": "schedule",
        "path": ".github/workflows/release.yml",
    }

    def api(path, *, raw=False):
        if "/contents/" in path:
            name = path.split("/contents/", 1)[1].split("?", 1)[0]
            assert path.endswith("?ref=" + identity["sha"])
            return {"encoding": "base64", "path": name, "content": base64.b64encode((source / name).read_bytes()).decode()}
        if path.endswith("/attempts/2"):
            return run
        if "/jobs?" in path:
            return {"jobs": jobs}
        identifier = int(path.split("/artifacts/", 1)[1].split("/", 1)[0])
        if path.endswith("/zip"):
            assert raw
            return archives[identifier]
        return next(item for item in origins if item["id"] == identifier)

    monkeypatch.setattr(origin, "api", api)
    return destination, receipt, origins


def test_packet_reassembled_from_current_producers_and_source(packet):
    path, receipt, _ = packet
    origin.verify_source_inventories(receipt, path)
    origin.verify_producer_packet(receipt, path)
    assert len(list((path / "cli-proofs").iterdir())) == 6


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("foreign_sha", [False, True])
def test_engine_workflow_finalizer_checks_actual_packet_identity(packet, tmp_path, monkeypatch, engine, foreign_sha):
    path, receipt, _ = packet
    work = tmp_path / "engine-workflow"
    root = work / "output/ci-engine"
    shutil.copytree(path / ("engine-" + engine), root)
    write_json(work / "test/mandatory-api-cases.json", json.loads((path / "source-inventories/api.json").read_text()))
    write_json(root / "models-after.json", [{"name": "fixture-model", "digest": "f" * 64}])
    identity = receipt["ci"]
    for key, value in {
        "GITHUB_REPOSITORY": identity["repository"],
        "GITHUB_RUN_ID": identity["run_id"],
        "GITHUB_RUN_ATTEMPT": str(identity["attempt"]),
        "SOURCE_SHA": "f" * 40 if foreign_sha else receipt["source_revision"],
        "RAGFLOW_IMAGE": receipt["image"]["staging_digest"],
        "DOC_ENGINE": engine,
        "HTTP_API_TEST_LEVEL": "p3",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.chdir(work)
    script = (Path(__file__).resolve().parents[4] / "deployment/runner/test-engine.sh").read_text()
    finalizer = script.split("python - <<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
    output = root / ("engine-" + engine + ".json")
    previous = output.read_bytes()
    if foreign_sha:
        with pytest.raises(ValueError):
            exec(compile(finalizer, "test-engine.sh:finalizer", "exec"), {})
        assert output.read_bytes() == previous
    else:
        exec(compile(finalizer, "test-engine.sh:finalizer", "exec"), {})
        assert json.loads(output.read_text())["sha"] == receipt["source_revision"]


@pytest.mark.parametrize("name", ["installers/install.sh", "native/proof.json", "cli/ragflow-cli-linux-amd64"])
def test_collector_cannot_substitute_qualified_bytes(packet, name):
    path, receipt, _ = packet
    (path / name).write_bytes(b"substituted by collector")
    with pytest.raises(ValueError, match="packet differs"):
        origin.verify_producer_packet(receipt, path)


def test_collector_cannot_add_unowned_file(packet):
    path, receipt, _ = packet
    (path / "extra.json").write_text("{}")
    with pytest.raises(ValueError, match="packet differs"):
        origin.verify_producer_packet(receipt, path)


def test_origin_metadata_tampering_blocks_before_archive_download(packet):
    path, receipt, _ = packet
    origins = json.loads((path / "producer-origins.json").read_text())
    origins[0]["digest"] = "sha256:" + "c" * 64
    write_json(path / "producer-origins.json", origins)
    with pytest.raises(ValueError, match="origin differs"):
        origin.verify_producer_packet(receipt, path)


def test_inventory_must_be_the_qualified_source_snapshot(packet):
    path, receipt, _ = packet
    (path / "source-inventories/api.json").write_text("{}")
    with pytest.raises(ValueError, match="source snapshot"):
        origin.verify_source_inventories(receipt, path)


def test_successful_summary_cannot_hide_skipped_required_case(packet):
    path, receipt, _ = packet
    report = path / "engine-infinity/test_sdk_api-cases.json"
    data = json.loads(report.read_text())
    data["cases"][0]["phases"][1].update(outcome="skipped", skip_reason="bug")
    write_json(report, data)
    with pytest.raises(ValueError, match="unapproved skip"):
        origin.verify_source_inventories(receipt, path)


def test_summary_must_match_executed_case_report(packet):
    path, receipt, _ = packet
    summary = path / "engine-infinity/test_sdk_api-qualified.json"
    data = deepcopy(json.loads(summary.read_text()))
    data["passed"] += 1
    write_json(summary, data)
    with pytest.raises(ValueError, match="summary differs"):
        origin.verify_source_inventories(receipt, path)


def test_browser_summary_cannot_hide_skipped_mandatory_case(packet):
    path, receipt, _ = packet
    (path / "browser-results/webkit.xml").write_text('<testsuite tests="1"><testcase classname="test.playwright.e2e.test_example" name="test_required"><skipped /></testcase></testsuite>')
    with pytest.raises(ValueError, match="skipped"):
        origin.verify_source_inventories(receipt, path)
