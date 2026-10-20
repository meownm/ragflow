"""Product isolation, stale workers and sanitized TZ quality projections."""

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from peewee import SqliteDatabase

ADMIN = Path(__file__).resolve().parents[3] / "admin/server"
if str(ADMIN) not in sys.path:
    sys.path.insert(0, str(ADMIN))

import document_quality_runs as runs
from document_quality_health import schedule_health
from technical_specification_quality import public_report

from api.db.db_models import BusinessDocumentQualityCampaign, BusinessDocumentQualityRun


@pytest.fixture
def queue(monkeypatch, tmp_path):
    database = SqliteDatabase(":memory:")
    monkeypatch.setenv("BUSINESS_DOCUMENT_QUALITY_EVIDENCE_DIR", str(tmp_path / "evidence"))
    monkeypatch.setattr(runs, "_configured_tenant", lambda: "qa")
    monkeypatch.setattr(runs, "_source_fingerprint", lambda: "source-hash")
    monkeypatch.setattr(runs, "quality_model_catalog", lambda _: {"models": [{"name": "qwen", "digest": "a" * 64, "aliases": ["qwen"]}], "errors": []})
    with database.bind_ctx([BusinessDocumentQualityCampaign, BusinessDocumentQualityRun], bind_refs=False, bind_backrefs=False):
        database.create_tables([BusinessDocumentQualityCampaign, BusinessDocumentQualityRun])
        yield database
    database.close()


def test_nightly_slots_are_separate_and_idempotent(queue):
    when = datetime(2026, 10, 20, 2, 5, tzinfo=ZoneInfo("Europe/Moscow"))
    runs.schedule_due(when)
    first = [(row.id, row.schedule_key, row.document_type) for row in BusinessDocumentQualityCampaign.select()]
    runs.schedule_due(when)
    assert first == [(row.id, row.schedule_key, row.document_type) for row in BusinessDocumentQualityCampaign.select()]
    assert len(first) == 2
    assert len(runs.list_quality_runs()["runs"]) == 2  # nightly + monthly catch-up
    assert len(runs.list_quality_runs("technical_specification")["runs"]) == 2  # nightly + monthly catch-up
    assert len(runs.list_quality_campaigns("technical_specification")["campaigns"]) == 1


def test_manual_dedup_and_details_never_cross_products(queue):
    bt = runs.enqueue_quality_run("MANUAL")
    tz = runs.enqueue_quality_run("MANUAL", document_type="technical_specification")
    assert bt["id"] != tz["id"]
    assert runs.enqueue_quality_run("MANUAL", document_type="technical_specification")["id"] == tz["id"]
    assert runs.get_quality_run(tz["id"]) is None
    assert runs.get_quality_run(bt["id"], "technical_specification") is None
    assert runs.get_quality_run(tz["id"], "technical_specification")["document_type"] == "technical_specification"


def test_default_model_history_exposes_observed_identity_without_qualifying_the_run(queue):
    row = BusinessDocumentQualityRun.create(
        id="observed-model-history",
        document_type="technical_specification",
        trigger="MONTHLY",
        status="FAIL",
        report={"model": "observed", "model_digest": "a" * 64, "evidence_status": "INCOMPLETE", "executed_cases": 0, "expected_cases": 56},
    )
    public = runs.get_quality_run(row.id, "technical_specification")
    assert public["model_name"] == "observed" and public["model_digest"] == "a" * 64
    assert public["status"] == "FAIL" and public["report"]["evidence_status"] == "INCOMPLETE"
    assert public["report"]["executed_cases"] == 0
    unchanged = BusinessDocumentQualityRun.get_by_id(row.id)
    assert unchanged.model_name is None and unchanged.model_digest is None


def test_completed_campaign_can_have_failed_quality(queue):
    campaign = runs.enqueue_nightly_campaign("2026-10-20", "technical_specification")
    BusinessDocumentQualityRun.update(status="FAIL").where(BusinessDocumentQualityRun.campaign_id == campaign["id"]).execute()
    runs._refresh_campaign(campaign["id"])
    result = runs.get_quality_campaign(campaign["id"], "technical_specification")
    assert result["status"] == "COMPLETE" and result["quality_status"] == "FAIL"


def test_late_executor_cannot_overwrite_reclassified_run(queue, monkeypatch):
    created = runs.enqueue_quality_run("MANUAL", document_type="technical_specification")

    def stopped(row):
        BusinessDocumentQualityRun.update(status="INCOMPLETE", reason_code="WORKER_STOPPED").where(BusinessDocumentQualityRun.id == row.id).execute()
        return "PASS", {"status": "PASS"}, None

    monkeypatch.setattr(runs, "_execute", stopped)
    assert runs.process_next_run()
    result = runs.get_quality_run(created["id"], "technical_specification")
    assert result["status"] == "INCOMPLETE" and result["reason_code"] == "WORKER_STOPPED"


def test_campaign_waiting_for_baseline_does_not_starve_other_product(queue, monkeypatch):
    campaign = runs.enqueue_nightly_campaign("2026-10-20")
    BusinessDocumentQualityCampaign.update(status="RUNNING").where(BusinessDocumentQualityCampaign.id == campaign["id"]).execute()
    manual = runs.enqueue_quality_run("MANUAL", document_type="technical_specification")
    monkeypatch.setattr(runs, "_execute", lambda row: ("FAIL", {}, "Synthetic model mismatch"))
    assert runs.process_next_run()
    assert runs.get_quality_run(manual["id"], "technical_specification")["status"] == "FAIL"


def test_late_baseline_cannot_overwrite_reclassified_campaign(queue, monkeypatch):
    campaign = runs.enqueue_nightly_campaign("2026-10-20", "technical_specification")

    def expired(row):
        BusinessDocumentQualityCampaign.update(status="PARTIAL", baseline_status="INCOMPLETE", reason_code="WORKER_STOPPED").where(BusinessDocumentQualityCampaign.id == row.id).execute()
        return "PASS", None

    monkeypatch.setattr(runs, "_run_campaign_baseline", expired)
    assert runs.process_next_run()
    current = runs.get_quality_campaign(campaign["id"], "technical_specification")
    assert current["baseline_status"] == "INCOMPLETE" and current["reason_code"] == "WORKER_STOPPED"


def test_independent_watchdog_does_not_create_missing_slot(queue):
    when = datetime(2026, 10, 20, 2, 16, tzinfo=ZoneInfo("Europe/Moscow"))
    health = schedule_health("technical_specification", when)
    assert health["reason_code"] == "NIGHTLY_MISSING"
    assert not BusinessDocumentQualityCampaign.select().exists()
    runs.enqueue_nightly_campaign("2026-10-20")
    assert schedule_health("technical_specification", when)["status"] == "ALERT"
    runs.enqueue_nightly_campaign("2026-10-20", "technical_specification")
    assert schedule_health("technical_specification", when)["status"] == "OK"
    assert schedule_health("technical_specification", when.replace(hour=15))["reason_code"] == "NIGHTLY_OVERDUE"


def test_watchdog_checks_previous_night_during_scheduler_grace(queue):
    when = datetime(2026, 10, 20, 2, 5, tzinfo=ZoneInfo("Europe/Moscow"))
    runs.enqueue_nightly_campaign("2026-10-19", "technical_specification")
    BusinessDocumentQualityCampaign.update(status="COMPLETE").execute()
    assert schedule_health("technical_specification", when)["status"] == "OK"


def test_tz_public_report_never_exposes_source_or_provider_text():
    report = {
        "status": "FAIL",
        "quality_status": "FAIL",
        "evidence_status": "INCOMPLETE",
        "expected_cases": 36,
        "executed_cases": 1,
        "source": "private",
        "cases": [{"case_id": "BTZ-01", "status": "FAIL", "failure_count": 1, "reason": "private", "result": {"report": "private"}}],
    }
    public = public_report(report)
    assert "private" not in str(public)
    assert public["quality_status"] == "FAIL" and public["evidence_status"] == "INCOMPLETE"


def test_source_snapshot_identity_reaches_model_comparison_without_untrusted_text():
    result = public_report({"source_dirty": True, "source_fingerprint": "a" * 64})
    assert result["source_dirty"] is True and result["source_fingerprint"] == "a" * 64
    invalid = public_report({"source_dirty": "private", "source_fingerprint": "private"})
    assert "private" not in str(invalid) and "source_dirty" not in invalid


def test_semantic_metrics_reach_saved_run_projection_without_private_annotations(queue):
    from test.evals.technical_specifications.alignment.evaluate import public_summary
    from test.unit_test.tools.quality.test_release_evidence import _alignment_report

    summary = public_summary(_alignment_report())
    summary["alignment_quality"]["private"] = "private document"
    queued = runs.enqueue_quality_run("MANUAL", document_type="technical_specification")
    BusinessDocumentQualityRun.update(report=summary).where(BusinessDocumentQualityRun.id == queued["id"]).execute()
    report = runs.get_quality_run(queued["id"], "technical_specification")["report"]
    assert report["alignment_quality"]["measures"]["contradiction_recall"]["value"] == 1
    assert "private" not in str(report)


@pytest.mark.parametrize("product", ["unknown", "", None, [], {}])
def test_unknown_products_are_rejected(product):
    with pytest.raises(ValueError):
        runs.list_quality_runs(product)


@pytest.mark.parametrize("defect", [None, "missing-audit", "missing-probes", "exit-2", "unknown-tokens", "forged-aggregate"])
def test_nightly_pass_requires_recomputed_full_execution_evidence(queue, monkeypatch, defect):
    import json
    from types import SimpleNamespace

    import technical_specification_quality as quality

    from test.unit_test.tools.quality.test_release_evidence import _alignment_report

    report = _alignment_report()  # synthetic proof; no model is contacted
    if defect == "missing-audit":
        del report["cases"][0]["result"]["audit"]
    elif defect == "missing-probes":
        del report["real_limit_probes"]
    elif defect == "unknown-tokens":
        report["budget"]["reserved_tokens"] = 1
    elif defect == "forged-aggregate":
        report["quality_status"] = "UNKNOWN"
    row = SimpleNamespace(id="synthetic-nightly", source_revision="a" * 40, campaign_id=None, model_name=None, model_digest=None, case_id=None, scope="FULL")

    def subprocess_run(command, **_options):
        Path(command[command.index("--report") + 1]).write_text(json.dumps(report), encoding="utf-8")
        return SimpleNamespace(returncode=2 if defect == "exit-2" else 0)

    monkeypatch.setattr(quality.subprocess, "run", subprocess_run)
    monkeypatch.setattr(runs, "_source_revision", lambda: row.source_revision)
    status, summary, _reason = quality.execute(row, Path.cwd(), "qa", row.source_revision, "source-hash")
    assert status == ("PASS" if defect is None else "INCOMPLETE")
    assert summary["status"] == status


@pytest.mark.parametrize("mode", ["missing", "complete", "failure", "partial"])
def test_scheduled_series_collects_experiments_and_cannot_pass_unattested_budget(queue, monkeypatch, mode):
    import json
    from types import SimpleNamespace

    import technical_specification_quality as quality

    from test.evals.technical_specifications.alignment.nightly import ORDER, execution_seconds
    from test.unit_test.technical_specifications.test_alignment_nightly import stage
    from test.unit_test.tools.quality.test_release_evidence import _alignment_report

    report = _alignment_report()
    if mode != "missing":
        report["nightly_experiments"] = {kind: stage(kind) for kind in ORDER}
    if mode == "failure":
        report["nightly_experiments"]["REASONING"]["cases"][-1]["status"] = "FAIL"
        report["nightly_experiments"]["REASONING"]["cases"][-1]["result"].update(execution_status="COMPLETE", report_status="INCOMPLETE", quality_status="FAIL")
    if mode == "partial":
        report.update(cases=[], quality_status="UNKNOWN", status="INCOMPLETE", qualifies=False)
        report["nightly_experiments"]["REPEATS"]["cases"][-1]["status"] = "INCOMPLETE"
        report["nightly_experiments"]["REPEATS"]["budget"]["reserved_tokens"] = 100
    row = SimpleNamespace(id="synthetic-nightly", trigger="NIGHTLY", source_revision="a" * 40, campaign_id=None, model_name=None, model_digest=None, case_id=None, scope="FULL")

    def subprocess_run(command, **options):
        assert "--nightly" in command and options["timeout"] == execution_seconds()
        Path(command[command.index("--report") + 1]).write_text(json.dumps(report), encoding="utf-8")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(quality.subprocess, "run", subprocess_run)
    monkeypatch.setattr(runs, "_source_revision", lambda: row.source_revision)
    status, summary, _reason = quality.execute(row, Path.cwd(), "qa", row.source_revision, "source-hash")
    assert status == ("FAIL" if mode == "failure" else "INCOMPLETE")
    assert summary["evidence_status"] == "INCOMPLETE"
    assert [item["experiment"] for item in summary["nightly_experiments"]] == list(ORDER)
    if mode == "partial":
        assert summary["nightly_experiments"][0]["total_tokens"] is None


def test_stale_detection_uses_scheduled_series_deadline(queue, monkeypatch):
    import technical_specification_quality as quality

    from common.time_utils import current_timestamp

    scheduled = runs.enqueue_quality_run("NIGHTLY", document_type="technical_specification")
    manual = runs.enqueue_quality_run("MANUAL", document_type="technical_specification")
    started = current_timestamp() - (quality.execution_timeout() + 600) * 1000
    BusinessDocumentQualityRun.update(status="RUNNING", started_at=started).execute()
    monkeypatch.setattr(runs, "_execute", lambda _: (_ for _ in ()).throw(AssertionError("No pending work")))
    assert not runs.process_next_run()
    assert runs.get_quality_run(scheduled["id"], "technical_specification")["status"] == "RUNNING"
    assert runs.get_quality_run(manual["id"], "technical_specification")["status"] == "INCOMPLETE"
