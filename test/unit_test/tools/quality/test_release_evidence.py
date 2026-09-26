"""Release evidence must reject mismatched or incomplete candidate proof."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json

import pytest

from tools.quality.release_evidence import make_receipt, validate_receipt, verify_release


REVISION = "a" * 40
IMAGE = f"registry.example:5443/ragflow:{REVISION}"
DIGEST = f"registry.example:5443/ragflow@sha256:{'b' * 64}"
JOBS = {name: "success" for name in ("ragflow_preflight", "ragflow_tests_infinity", "ragflow_tests_elasticsearch")}


def _write(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


@pytest.fixture()
def packet(tmp_path):
    receipt = make_receipt(REVISION, "meownm/ragflow", "123", IMAGE, DIGEST, JOBS)
    receipt_path = _write(tmp_path / "candidate-receipt.json", receipt)
    backup = tmp_path / "postgres.dump"
    backup.write_bytes(b"verified backup bytes")
    deployment = {
        "schema": 1,
        "mode": "release",
        "candidate_revision": REVISION,
        "candidate_image": IMAGE,
        "candidate_image_id": "sha256:local-image-id",
        "candidate_repo_digests": [DIGEST],
        "candidate_receipt_sha256": hashlib.sha256(receipt_path.read_bytes()).hexdigest(),
        "candidate_receipt_status": "verified",
        "containers": [{"service": "ragflow-cpu", "image": "sha256:local-image-id", "status": "running", "health": "healthy"}],
        "backup": {"path": str(backup), "sha256": hashlib.sha256(backup.read_bytes()).hexdigest()},
        "health": {"status": "ok"},
    }
    deployment_path = _write(tmp_path / "deployment.json", deployment)
    workbench_path = _write(
        tmp_path / "source-workbench.json",
        {
            "source_revision": REVISION,
            "source_dirty": False,
            "status": "fail",
            "metrics": {},
            "cases": [{"id": "query-1"}],
            "index": {"state": "ready"},
            "model": {"embedding": "model", "weights": {"digest": "sha256:weights"}},
            "corpus_sha256": "a" * 64,
        },
    )
    business_path = _write(
        tmp_path / "business-documents.json",
        {
            "source_revision": REVISION,
            "source_dirty": False,
            "status": "FAIL",
            "case_results": [{"case_id": "M01"}],
            "ai": {"model": "model", "execution_count": 1},
            "model_weights_digest": "sha256:weights",
        },
    )
    return receipt_path, deployment_path, workbench_path, business_path, deployment


def test_quality_threshold_failure_is_recorded_without_replacing_release_identity_gate(packet):
    result = verify_release(*packet[:4])
    assert result["status"] == "pass"
    assert result["evidence_status"] == "pass"
    assert result["quality_status"] == "fail"
    assert result["quality_baseline"] == {"source_workbench": "fail", "business_documents": "fail"}
    assert result["source_revision"] == REVISION
    assert result["image_digest"] == DIGEST


@pytest.mark.parametrize(
    "change,failed_check",
    [
        ({"candidate_revision": "c" * 40}, "source_revision"),
        ({"candidate_repo_digests": [f"registry.example:5443/ragflow@sha256:{'c' * 64}"]}, "image_digest"),
        ({"health": {"status": "error"}}, "api_health"),
        ({"candidate_receipt_status": "metadata_only"}, "receipt_verified"),
        ({"candidate_image_id": None}, "container_image"),
        ({"containers": [{"service": "ragflow-cpu", "image": "sha256:other", "status": "running", "health": "healthy"}]}, "container_image"),
    ],
)
def test_mismatched_revision_digest_and_health_fail(packet, change, failed_check):
    receipt_path, deployment_path, workbench_path, business_path, deployment = packet
    changed = deepcopy(deployment)
    changed.update(change)
    _write(deployment_path, changed)
    result = verify_release(receipt_path, deployment_path, workbench_path, business_path)
    assert result["status"] == "fail"
    assert result["checks"][failed_check] is False


def test_unavailable_backup_or_quality_report_is_incomplete(packet):
    receipt_path, deployment_path, workbench_path, business_path, deployment = packet
    changed = deepcopy(deployment)
    changed["backup"]["path"] = str(deployment_path.parent / "missing.dump")
    _write(deployment_path, changed)
    result = verify_release(receipt_path, deployment_path, workbench_path, business_path)
    assert result["status"] == "incomplete"
    assert result["checks"]["backup"] is False
    _write(deployment_path, deployment)
    business_path.unlink()
    assert verify_release(receipt_path, deployment_path, workbench_path, business_path)["status"] == "incomplete"


def test_dirty_quality_baseline_cannot_prove_clean_candidate(packet):
    receipt_path, deployment_path, workbench_path, business_path, _ = packet
    _write(
        workbench_path,
        {
            "source_revision": REVISION,
            "source_dirty": True,
            "status": "pass",
            "metrics": {},
            "cases": [{"id": "query-1"}],
            "index": {"state": "ready"},
            "model": {"embedding": "model", "weights": {"digest": "sha256:weights"}},
            "corpus_sha256": "a" * 64,
        },
    )
    result = verify_release(receipt_path, deployment_path, workbench_path, business_path)
    assert result["status"] == "incomplete"
    assert "source_workbench" not in result["quality_baseline"]


def test_ci_job_failure_or_foreign_digest_cannot_form_receipt():
    failed = {**JOBS, "ragflow_tests_elasticsearch": "failure"}
    with pytest.raises(ValueError, match="required CI jobs"):
        make_receipt(REVISION, "meownm/ragflow", "123", IMAGE, DIGEST, failed)
    with pytest.raises(ValueError, match="digest"):
        make_receipt(REVISION, "meownm/ragflow", "123", IMAGE, f"other/ragflow@sha256:{'b' * 64}", JOBS)


def test_explicit_engine_skip_preserves_release_identity_verification(packet):
    receipt_path, deployment_path, workbench_path, business_path, deployment = packet
    jobs = {**JOBS, "ragflow_tests_infinity": "skipped", "ragflow_tests_elasticsearch": "skipped"}
    receipt = make_receipt(REVISION, "meownm/ragflow", "123", IMAGE, DIGEST, jobs, engine_tests_required=False, engine_test_reason="no-engine-changes")
    _write(receipt_path, receipt)
    deployment["candidate_receipt_sha256"] = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
    _write(deployment_path, deployment)
    assert verify_release(receipt_path, deployment_path, workbench_path, business_path)["status"] == "pass"
    assert receipt["ci"]["jobs"]["ragflow_tests_infinity"] == "skipped"


@pytest.mark.parametrize("result", ["failure", "cancelled", "success"])
def test_skip_policy_never_hides_a_failed_or_inconsistent_engine_run(result):
    jobs = {**JOBS, "ragflow_tests_infinity": result, "ragflow_tests_elasticsearch": "skipped"}
    with pytest.raises(ValueError, match="required CI jobs"):
        make_receipt(REVISION, "meownm/ragflow", "123", IMAGE, DIGEST, jobs, engine_tests_required=False, engine_test_reason="no-engine-changes")


def test_old_receipts_require_both_engine_successes():
    receipt = make_receipt(REVISION, "meownm/ragflow", "123", IMAGE, DIGEST, dict(JOBS))
    del receipt["ci"]["engine_tests_required"]
    del receipt["ci"]["engine_test_reason"]
    validate_receipt(receipt)
    receipt["ci"]["jobs"]["ragflow_tests_infinity"] = "skipped"
    with pytest.raises(ValueError, match="required CI jobs"):
        validate_receipt(receipt)


@pytest.mark.parametrize("required,reason", [(False, ""), (False, "baseline-unavailable"), ("false", "no-engine-changes"), (None, "no-engine-changes")])
def test_receipt_rejects_unproven_skip_selection(required, reason):
    with pytest.raises(ValueError, match="engine selection"):
        make_receipt(REVISION, "meownm/ragflow", "123", IMAGE, DIGEST, JOBS, engine_tests_required=required, engine_test_reason=reason)


def test_incomplete_quality_report_cannot_pass_release(packet):
    receipt_path, deployment_path, workbench_path, business_path, _ = packet
    _write(workbench_path, {"status": "incomplete", "failure": "index unavailable"})
    result = verify_release(receipt_path, deployment_path, workbench_path, business_path)
    assert result["status"] == "incomplete"
    assert result["quality_status"] == "incomplete"
    assert "Source Workbench quality run is incomplete" in result["missing"]


def test_missing_model_evidence_is_incomplete_even_with_failed_threshold(packet):
    receipt_path, deployment_path, workbench_path, business_path, _ = packet
    workbench = json.loads(workbench_path.read_text(encoding="utf-8"))
    workbench["model"]["weights"] = None
    _write(workbench_path, workbench)
    business = json.loads(business_path.read_text(encoding="utf-8"))
    business["ai"] = None
    _write(business_path, business)
    result = verify_release(receipt_path, deployment_path, workbench_path, business_path)
    assert result["status"] == "incomplete"
    assert "Source Workbench embedding model evidence is unavailable" in result["missing"]
    assert "Business Documents live model evidence is unavailable" in result["missing"]
