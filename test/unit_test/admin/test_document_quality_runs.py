import sys
import json
import subprocess
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from peewee import SqliteDatabase


ADMIN_SERVER = Path(__file__).parents[3] / "admin" / "server"
if str(ADMIN_SERVER) not in sys.path:
    sys.path.insert(0, str(ADMIN_SERVER))

import document_quality_runs as runs
from api.db.db_models import BusinessDocumentQualityCampaign, BusinessDocumentQualityRun


def test_schedule_is_idempotent_and_manual_run_is_persisted(monkeypatch):
    database = SqliteDatabase(":memory:")
    monkeypatch.setattr(runs, "_configured_tenant", lambda: "dedicated-qa")
    with database.bind_ctx([BusinessDocumentQualityCampaign, BusinessDocumentQualityRun], bind_refs=False, bind_backrefs=False):
        database.connect()
        database.create_tables([BusinessDocumentQualityCampaign, BusinessDocumentQualityRun])
        try:
            monkeypatch.setattr(runs, "quality_model_catalog", lambda _: {"models": [{"name": "qwen", "digest": "a" * 64, "aliases": ["qwen"]}], "errors": []})
            monkeypatch.setattr(runs, "_run_campaign_baseline", lambda _: ("PASS", None))
            when = datetime(2026, 10, 1, 3, 15, tzinfo=ZoneInfo("Europe/Moscow"))
            runs.schedule_due(when)
            runs.schedule_due(when)
            assert BusinessDocumentQualityRun.select().count() == 2
            assert {row.trigger for row in BusinessDocumentQualityRun.select()} == {"NIGHTLY", "MONTHLY"}

            monkeypatch.setattr(runs, "_execute", lambda _row: ("PASS", {"weighted_score": 3.5}, None))
            assert runs.process_next_run() is True
            assert runs.process_next_run() is True
            assert BusinessDocumentQualityRun.select().where(BusinessDocumentQualityRun.status == "PASS").count() == 1
            assert runs.list_quality_runs()["runs"][0]["trigger"] in {"NIGHTLY", "MONTHLY"}
        finally:
            database.drop_tables([BusinessDocumentQualityCampaign, BusinessDocumentQualityRun])
            database.close()


def test_manual_run_requires_dedicated_tenant(monkeypatch):
    monkeypatch.setattr(runs, "_configured_tenant", lambda: "")
    try:
        runs.enqueue_quality_run("MANUAL")
    except ValueError as error:
        assert "dedicated" in str(error).lower()
    else:
        raise AssertionError("Manual quality run was accepted without a QA tenant")


def test_persisted_report_omits_generated_content_and_failure_text():
    summary = runs._report_summary(
        {
            "status": "FAIL",
            "golden_suite": {"expected_case_ids": ["case-1"], "executed_case_ids": ["case-1"]},
            "case_results": [{"case_id": "case-1", "status": "FAIL", "failures": ["private document text"]}],
            "metrics": {"weighted_score": 2.5, "criterion_scores": {"source_grounding": 2.0}},
        }
    )
    assert summary["cases"][0]["diagnostics"] == [{"code": "UNCLASSIFIED"}]
    assert summary["cases"][0]["failure_count"] == 1
    assert "private document text" not in str(summary)


def test_report_keeps_safe_fact_ids_and_gate_thresholds():
    summary = runs._report_summary({
        "status": "FAIL", "rubric_version": "1.0.1",
        "golden_suite": {"expected_case_ids": ["M04"], "executed_case_ids": ["M04"]},
        "case_results": [{"case_id": "M04", "priority": "P0", "status": "FAIL",
                          "failures": ["missing_fact_ids=('scenario_rule',)", "Secret: private document"],
                          "metrics": {"missing_fact_ids": ["scenario_rule"], "semantic_coverage": 0.8}}],
        "metrics": {"p0_case_pass_rate": 0.0, "all_case_pass_rate": 0.0},
    })
    assert summary["cases"][0]["diagnostics"] == [{"code": "MISSING_FACT", "fact_id": "scenario_rule"}]
    assert {check["metric"] for check in summary["gate_checks"] if not check["passed"]} == {"p0_case_pass_rate", "all_case_pass_rate"}
    assert "Secret" not in str(summary)


def test_partial_diagnostic_does_not_store_full_suite_score():
    summary = runs._report_summary({
        "status": "INCOMPLETE",
        "golden_suite": {"expected_case_ids": ["M01", "M04"], "executed_case_ids": ["M04"]},
        "metrics": {"weighted_score": 3.9, "p0_case_pass_rate": 1.0, "all_case_pass_rate": 1.0},
        "case_results": [{"case_id": "M04", "status": "FAIL", "failures": ["missing_fact_ids=('scenario_rule',)"],
                          "metrics": {"missing_fact_ids": ["scenario_rule"]}}],
    })
    assert summary["executed_cases"] == 1 and summary["expected_cases"] == 2
    assert summary["weighted_score"] is None
    assert summary["p0_case_pass_rate"] is None
    assert summary["gate_checks"] == []
    assert summary["cases"][0]["diagnostics"] == [{"code": "MISSING_FACT", "fact_id": "scenario_rule"}]


def test_execution_error_gets_safe_diagnostic_code():
    summary = runs._report_summary({
        "status": "INCOMPLETE", "golden_suite": {"expected_case_ids": ["M04"], "executed_case_ids": ["M04"]},
        "case_results": [{"case_id": "M04", "status": "INCOMPLETE", "failures": ["private database error"]}],
    })
    assert summary["cases"][0]["diagnostics"] == [{"code": "EXECUTION_ERROR"}]
    assert "private database error" not in str(summary)


def test_diagnostic_requires_completed_selected_case(monkeypatch):
    monkeypatch.setattr(runs, "_configured_tenant", lambda: "qa")
    monkeypatch.setattr(runs, "_source_revision", lambda: "revision")

    def write_report(_path, env, _temp, **_kwargs):
        result_status = env["TEST_CASE_STATUS"]
        Path(env["BUSINESS_DOCUMENT_QUALITY_REPORT"]).write_text(json.dumps({
            "status": "INCOMPLETE", "diagnostic_case_id": "M04", "active_case_id": None,
            "golden_suite": {"expected_case_ids": ["M04"], "executed_case_ids": ["M04"]},
            "case_results": [{"case_id": "M04", "status": result_status, "failures": []}],
        }), encoding="utf-8")
        return 1

    monkeypatch.setattr(runs, "_run_suite", write_report)
    row = SimpleNamespace(source_revision="revision", campaign_id=None, model_name=None,
                          model_digest=None, scope="CASE", case_id="M04")
    monkeypatch.setenv("TEST_CASE_STATUS", "INCOMPLETE")
    status, _, _ = runs._execute(row)
    assert status == "INCOMPLETE"
    monkeypatch.setenv("TEST_CASE_STATUS", "FAIL")
    status, _, _ = runs._execute(row)
    assert status == "DIAGNOSTIC"


def test_live_suite_uses_remaining_full_run_budget(monkeypatch, tmp_path):
    observed = []
    monkeypatch.setattr(runs.time, "monotonic", lambda: 19.5)
    monkeypatch.setattr(runs.subprocess, "run", lambda *args, **kwargs: observed.append(kwargs["timeout"]) or SimpleNamespace(returncode=0))
    assert runs._run_suite("test.py", {}, tmp_path, deadline=20) == 0
    assert observed == [0.5]


def test_unverified_source_fingerprint_detects_mounted_code_change(monkeypatch, tmp_path):
    path = tmp_path / "admin" / "server" / "quality.py"
    path.parent.mkdir(parents=True)
    path.write_text("first", encoding="utf-8")
    monkeypatch.setattr(runs, "ROOT", tmp_path)
    monkeypatch.setattr(runs, "_source_revision", lambda: "unverified")
    snapshot = runs._source_fingerprint()
    campaign = SimpleNamespace(source_revision="unverified", models={"source_fingerprint": snapshot})
    assert runs._campaign_source_matches(campaign)
    path.write_text("second", encoding="utf-8")
    assert not runs._campaign_source_matches(campaign)


def test_pending_run_from_previous_image_is_not_executed(monkeypatch):
    monkeypatch.setattr(runs, "_source_revision", lambda: "new-revision")
    status, report, reason = runs._execute(type("QueuedRun", (), {"source_revision": "old-revision"})())
    assert status == "INCOMPLETE"
    assert report is None
    assert "revision" in reason


def test_monthly_history_survives_thirty_daily_runs():
    database = SqliteDatabase(":memory:")
    with database.bind_ctx([BusinessDocumentQualityRun], bind_refs=False, bind_backrefs=False):
        database.connect()
        database.create_tables([BusinessDocumentQualityRun])
        try:
            for index, trigger in enumerate(["MONTHLY", *(["NIGHTLY"] * 35)]):
                run = BusinessDocumentQualityRun.create(id=f"run-{index}", trigger=trigger, status="PASS", source_revision="rev")
                database.execute_sql("UPDATE business_document_quality_run SET create_time = ? WHERE id = ?", (index + 1, run.id))
            rows = runs.list_quality_runs()["runs"]
            assert len(rows) == 36
            assert rows[-1]["id"] == "run-0"
        finally:
            database.drop_tables([BusinessDocumentQualityRun])
            database.close()


def test_golden_summary_does_not_persist_failure_text():
    summary = runs._golden_summary(
        {
            "suite_id": "suite",
            "expected_cases": 1,
            "executed_cases": 1,
            "cases": [{"case_id": "G01", "status": "FAIL", "failure_count": 1, "failure": "private document text"}],
        }
    )
    assert summary["cases"][0]["diagnostics"] == [{"code": "ASSERTION_FAILED"}]
    assert "private document text" not in str(summary)


def test_pytest_failure_names_omit_assertion_text(tmp_path):
    (tmp_path / "pytest.log").write_text(
        "FAILED test/evals/business_documents/test_golden_dialogue_harness.py::test_release_gate - AssertionError: private document text\n",
        encoding="utf-8",
    )
    assert runs._failed_test_names(tmp_path) == "test_release_gate"


def test_scheduled_golden_suite_enables_controlled_retrieval(monkeypatch):
    monkeypatch.setattr(runs, "_source_revision", lambda: "revision")
    monkeypatch.setattr(runs, "_configured_tenant", lambda: "qa")
    observed = []

    def fail_golden(_test_path, env, temp, **_kwargs):
        observed.append(env["BUSINESS_DOCUMENT_RELATED_FILE_SEARCH_ENABLED"])
        (temp / "pytest.log").write_text("", encoding="utf-8")
        return 1

    monkeypatch.setattr(runs, "_run_suite", fail_golden)
    status, _, _ = runs._execute(SimpleNamespace(source_revision="revision"))
    assert status == "INCOMPLETE"
    assert observed == ["true"]


def test_nightly_catalog_deduplicates_digest_and_recovers_after_baseline(monkeypatch):
    database = SqliteDatabase(":memory:")
    monkeypatch.setattr(runs, "_configured_tenant", lambda: "qa")
    monkeypatch.setattr(runs, "_source_revision", lambda: "revision")
    monkeypatch.setattr(runs, "quality_model_catalog", lambda _: {"models": [
        {"name": "qwen", "digest": "a" * 64, "aliases": ["qwen", "qwen:alias"]},
        {"name": "mistral", "digest": "b" * 64, "aliases": ["mistral"]}], "errors": []})
    monkeypatch.setattr(runs, "_run_campaign_baseline", lambda _: ("PASS", None))
    monkeypatch.setattr(runs, "_execute", lambda _: ("PASS", {"status": "PASS"}, None))
    with database.bind_ctx([BusinessDocumentQualityCampaign, BusinessDocumentQualityRun], bind_refs=False, bind_backrefs=False):
        database.connect()
        database.create_tables([BusinessDocumentQualityCampaign, BusinessDocumentQualityRun])
        try:
            runs.enqueue_nightly_campaign("2026-09-27")
            runs.enqueue_nightly_campaign("2026-09-27")
            assert BusinessDocumentQualityCampaign.select().count() == 1
            assert BusinessDocumentQualityRun.select().count() == 2
            assert runs.process_next_run() is True
            assert runs.process_next_run() is True
            assert runs.process_next_run() is True
            assert runs.list_quality_campaigns()["campaigns"][0]["status"] == "COMPLETE"
            assert runs.process_next_run() is False
        finally:
            database.drop_tables([BusinessDocumentQualityRun, BusinessDocumentQualityCampaign])
            database.close()


def test_nightly_baseline_failure_marks_models_incomplete(monkeypatch):
    database = SqliteDatabase(":memory:")
    monkeypatch.setattr(runs, "_configured_tenant", lambda: "qa")
    monkeypatch.setattr(runs, "quality_model_catalog", lambda _: {"models": [{"name": "qwen", "digest": "a" * 64, "aliases": ["qwen"]}], "errors": []})
    monkeypatch.setattr(runs, "_run_campaign_baseline", lambda _: ("FAIL", "BASELINE_FAILED"))
    with database.bind_ctx([BusinessDocumentQualityCampaign, BusinessDocumentQualityRun], bind_refs=False, bind_backrefs=False):
        database.connect()
        database.create_tables([BusinessDocumentQualityCampaign, BusinessDocumentQualityRun])
        try:
            runs.enqueue_nightly_campaign("2026-09-27")
            assert runs.process_next_run() is True
            assert BusinessDocumentQualityRun.get().status == "INCOMPLETE"
            assert BusinessDocumentQualityRun.get().reason_code == "BASELINE_FAILED"
            assert BusinessDocumentQualityCampaign.get().status == "PARTIAL"
        finally:
            database.drop_tables([BusinessDocumentQualityRun, BusinessDocumentQualityCampaign])
            database.close()


def test_one_incomplete_model_does_not_stop_remaining_models(monkeypatch):
    database = SqliteDatabase(":memory:")
    monkeypatch.setattr(runs, "_configured_tenant", lambda: "qa")
    monkeypatch.setattr(runs, "quality_model_catalog", lambda _: {"models": [
        {"name": "first", "digest": "a" * 64, "aliases": ["first"]},
        {"name": "second", "digest": "b" * 64, "aliases": ["second"]}], "errors": []})
    monkeypatch.setattr(runs, "_run_campaign_baseline", lambda _: ("PASS", None))
    monkeypatch.setattr(runs, "_execute", lambda row: ("INCOMPLETE", None, "Live model suite timed out")
                        if row.model_name == "first" else ("PASS", {"status": "PASS"}, None))
    with database.bind_ctx([BusinessDocumentQualityCampaign, BusinessDocumentQualityRun], bind_refs=False, bind_backrefs=False):
        database.connect()
        database.create_tables([BusinessDocumentQualityCampaign, BusinessDocumentQualityRun])
        try:
            runs.enqueue_nightly_campaign("2026-09-27")
            assert runs.process_next_run() is True
            assert runs.process_next_run() is True
            assert runs.process_next_run() is True
            assert {row.model_name: row.status for row in BusinessDocumentQualityRun.select()} == {
                "first": "INCOMPLETE", "second": "PASS"}
            assert BusinessDocumentQualityCampaign.get().status == "PARTIAL"
        finally:
            database.drop_tables([BusinessDocumentQualityRun, BusinessDocumentQualityCampaign])
            database.close()


def test_model_digest_mismatch_does_not_run_suite(monkeypatch):
    monkeypatch.setattr(runs, "_configured_tenant", lambda: "qa")
    monkeypatch.setattr(runs, "_source_revision", lambda: "revision")
    monkeypatch.setattr(runs, "quality_model_digest", lambda *_: "different")
    status, report, reason = runs._execute(SimpleNamespace(source_revision="revision", campaign_id=None,
                                                              model_name="qwen", model_digest="a" * 64, scope="CASE", case_id="M04"))
    assert status == "INCOMPLETE" and report is None
    assert "digest" in reason


def test_repeated_manual_request_reuses_active_run_then_allows_a_new_run(monkeypatch):
    database = SqliteDatabase(":memory:")
    monkeypatch.setattr(runs, "_configured_tenant", lambda: "qa")
    monkeypatch.setattr(runs, "_known_case_ids", lambda: {"M04"})
    monkeypatch.setattr(runs, "quality_model_catalog", lambda _: {"models": [{"name": "qwen", "digest": "a" * 64, "aliases": ["qwen", "qwen:alias"]}], "errors": []})
    monkeypatch.setattr(runs, "_execute", lambda _: ("DIAGNOSTIC", {"status": "INCOMPLETE"}, None))
    with database.bind_ctx([BusinessDocumentQualityCampaign, BusinessDocumentQualityRun], bind_refs=False, bind_backrefs=False):
        database.connect()
        database.create_tables([BusinessDocumentQualityCampaign, BusinessDocumentQualityRun])
        try:
            first = runs.enqueue_quality_run("MANUAL", model="qwen", scope="CASE", case_id="M04")
            repeat = runs.enqueue_quality_run("MANUAL", model="qwen:alias", scope="CASE", case_id="M04")
            assert first["id"] == repeat["id"]
            assert BusinessDocumentQualityRun.select().count() == 1
            assert runs.process_next_run() is True
            assert BusinessDocumentQualityRun.get().schedule_key is None
            second = runs.enqueue_quality_run("MANUAL", model="qwen", scope="CASE", case_id="M04")
            assert second["id"] != first["id"]
        finally:
            database.drop_tables([BusinessDocumentQualityRun, BusinessDocumentQualityCampaign])
            database.close()


def test_one_model_timeout_is_incomplete(monkeypatch):
    monkeypatch.setattr(runs, "_source_revision", lambda: "revision")
    monkeypatch.setattr(runs, "_configured_tenant", lambda: "qa")

    def timeout(*_, **_kwargs):
        raise subprocess.TimeoutExpired("pytest", 1200)

    monkeypatch.setattr(runs, "_run_suite", timeout)
    status, report, reason = runs._execute(SimpleNamespace(
        source_revision="revision", campaign_id=None, model_name=None,
        model_digest=None, scope="CASE", case_id="M04",
    ))
    assert status == "INCOMPLETE" and report is None
    assert reason == "Live model suite timed out"


def test_changed_digest_after_execution_cannot_pass(monkeypatch):
    monkeypatch.setattr(runs, "_source_revision", lambda: "revision")
    monkeypatch.setattr(runs, "_configured_tenant", lambda: "qa")
    digests = iter(["a" * 64, "b" * 64])
    monkeypatch.setattr(runs, "quality_model_digest", lambda *_: next(digests))

    def write_report(_path, env, _temp, **_kwargs):
        Path(env["BUSINESS_DOCUMENT_QUALITY_REPORT"]).write_text(json.dumps({
            "status": "FAIL", "golden_suite": {"expected_case_ids": ["M04"], "executed_case_ids": ["M04"]},
            "case_results": [{"case_id": "M04", "status": "FAIL", "failures": ["private answer"]}],
        }), encoding="utf-8")
        return 0

    monkeypatch.setattr(runs, "_run_suite", write_report)
    status, report, reason = runs._execute(SimpleNamespace(
        source_revision="revision", campaign_id=None, model_name="qwen",
        model_digest="a" * 64, scope="CASE", case_id="M04",
    ))
    assert status == "INCOMPLETE" and reason == "Model digest changed during execution"
    assert "private answer" not in str(report)


def test_changed_source_after_execution_cannot_pass(monkeypatch):
    revisions = iter(["revision", "new-image"])
    monkeypatch.setattr(runs, "_source_revision", lambda: next(revisions))
    monkeypatch.setattr(runs, "_source_fingerprint", lambda: None)
    monkeypatch.setattr(runs, "_configured_tenant", lambda: "qa")

    def write_report(_path, env, _temp, **_kwargs):
        Path(env["BUSINESS_DOCUMENT_QUALITY_REPORT"]).write_text(json.dumps({
            "status": "PASS", "golden_suite": {"expected_case_ids": ["M04"], "executed_case_ids": ["M04"]},
            "case_results": [{"case_id": "M04", "status": "PASS", "failures": []}],
        }), encoding="utf-8")
        return 0

    monkeypatch.setattr(runs, "_run_suite", write_report)
    status, _, reason = runs._execute(SimpleNamespace(
        source_revision="revision", campaign_id=None, model_name=None,
        model_digest=None, scope="CASE", case_id="M04",
    ))
    assert status == "INCOMPLETE" and "source revision" in reason


def test_nightly_comparison_signature_marks_rubric_prompt_and_parameter_changes():
    reference = {"source_revision": "rev", "suite_sha256": "sha256:" + "a" * 64,
                 "rubric_version": "1.1.0", "template_version": "v1",
                 "prompt_hashes": {"draft.txt": "sha256:" + "b" * 64},
                 "parameter_profiles": [{"temperature": 0, "top_p": 0.1}]}
    signature = runs._comparison_signature(reference)
    for changed in ({"rubric_version": "1.2.0"}, {"prompt_hashes": {"draft.txt": "sha256:" + "c" * 64}},
                    {"parameter_profiles": [{"temperature": 0.2, "top_p": 0.1}]},
                    {"suite_sha256": "sha256:" + "d" * 64}):
        assert runs._comparison_signature({**reference, **changed}) != signature


def test_admin_run_detail_never_returns_legacy_raw_content_or_exception():
    database = SqliteDatabase(":memory:")
    with database.bind_ctx([BusinessDocumentQualityRun], bind_refs=False, bind_backrefs=False):
        database.connect()
        database.create_tables([BusinessDocumentQualityRun])
        try:
            BusinessDocumentQualityRun.create(
                id="legacy-run", trigger="MANUAL", status="FAIL", source_revision="revision",
                reason="private exception text", report={
                    "status": "FAIL", "raw_response": "private model answer",
                    "cases": [{"case_id": "M04", "status": "FAIL", "failure": "private document text",
                               "diagnostics": [{"code": "PRIVATE_DOCUMENT_TEXT"}]}],
                },
            )
            detail = runs.get_quality_run("legacy-run")
            assert detail["report"]["cases"][0]["diagnostics"] == []
            assert "private" not in str(detail).lower()
        finally:
            database.drop_tables([BusinessDocumentQualityRun])
            database.close()
