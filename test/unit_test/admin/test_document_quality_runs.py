import sys
import json
import subprocess
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from peewee import SqliteDatabase


ADMIN_SERVER = Path(__file__).parents[3] / "admin" / "server"
if str(ADMIN_SERVER) not in sys.path:
    sys.path.insert(0, str(ADMIN_SERVER))

import document_quality_runs as runs
from api.db.db_models import BusinessDocumentQualityCampaign, BusinessDocumentQualityRun


@pytest.fixture(autouse=True)
def private_evidence(monkeypatch, tmp_path):
    monkeypatch.setenv("BUSINESS_DOCUMENT_QUALITY_EVIDENCE_DIR", str(tmp_path / "evidence"))


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
            assert BusinessDocumentQualityRun.select().count() == 4
            assert {row.document_type for row in BusinessDocumentQualityRun.select()} == {"business_requirements", "technical_specification"}
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


@pytest.mark.parametrize("product", ["business_requirements", "technical_specification"])
@pytest.mark.parametrize("runner_error", [False, True])
def test_run_source_version_is_persisted_and_survives_execution(monkeypatch, product, runner_error):
    database = SqliteDatabase(":memory:")
    monkeypatch.setattr(runs, "_configured_tenant", lambda: "qa")
    monkeypatch.setattr(runs, "_source_revision", lambda: "a" * 40)
    monkeypatch.setattr(runs, "get_ragflow_version", lambda: "1.54.0")

    def execute(_row):
        if runner_error:
            raise TimeoutError("runner stopped")
        return "PASS", {"status": "PASS", "executed_cases": 1}, None

    monkeypatch.setattr(runs, "_execute", execute)
    with database.bind_ctx([BusinessDocumentQualityCampaign, BusinessDocumentQualityRun], bind_refs=False, bind_backrefs=False):
        database.create_tables([BusinessDocumentQualityCampaign, BusinessDocumentQualityRun])
        try:
            queued = runs.enqueue_quality_run("MONTHLY", document_type=product)
            assert queued["source_version"] == "1.54.0"
            assert queued["source_build"] == "a" * 40
            monkeypatch.setattr(runs, "get_ragflow_version", lambda: "9.99.0")
            assert runs.process_next_run() is True
            stored = runs.get_quality_run(queued["id"], product)
            assert stored["status"] == ("INCOMPLETE" if runner_error else "PASS")
            assert stored["source_version"] == "1.54.0"
            assert stored["source_build"] == "a" * 40
            assert BusinessDocumentQualityRun.get_by_id(queued["id"]).report["source_version"] == "1.54.0"
        finally:
            database.drop_tables([BusinessDocumentQualityCampaign, BusinessDocumentQualityRun])
            database.close()


@pytest.mark.parametrize("product", ["business_requirements", "technical_specification"])
def test_nightly_build_identity_survives_failed_baseline(monkeypatch, product):
    database = SqliteDatabase(":memory:")
    monkeypatch.setattr(runs, "_configured_tenant", lambda: "qa")
    monkeypatch.setattr(runs, "_source_revision", lambda: "unverified")
    monkeypatch.setattr(runs, "_source_fingerprint", lambda: "b" * 64)
    monkeypatch.setattr(runs, "get_ragflow_version", lambda: "1.54.0-dev.123456789abc")
    monkeypatch.setattr(runs, "quality_model_catalog", lambda _: {"models": [{"name": "qwen", "digest": "a" * 64}], "errors": []})
    monkeypatch.setattr(runs, "_run_campaign_baseline", lambda _: ("INCOMPLETE", "BASELINE_FAILED"))
    with database.bind_ctx([BusinessDocumentQualityCampaign, BusinessDocumentQualityRun], bind_refs=False, bind_backrefs=False):
        database.create_tables([BusinessDocumentQualityCampaign, BusinessDocumentQualityRun])
        try:
            queued = runs.enqueue_nightly_campaign("2026-10-22", product)
            monkeypatch.setattr(runs, "get_ragflow_version", lambda: "9.99.0")
            assert runs.process_next_run() is True
            series = runs.get_quality_campaign(queued["id"], product)
            for identity in (series, *series["runs"]):
                assert identity["source_version"] == "1.54.0-dev.123456789abc"
                assert identity["source_build"] == "b" * 64
            assert series["runs"][0]["status"] == "INCOMPLETE"
        finally:
            database.drop_tables([BusinessDocumentQualityCampaign, BusinessDocumentQualityRun])
            database.close()


def test_legacy_source_identity_does_not_use_current_runtime_version():
    assert runs._public_source_identity("unverified", None) == {"source_version": None, "source_build": None}
    assert runs._public_source_identity("a" * 40, {"source_version": "private text", "source_fingerprint": "invalid"}) == {
        "source_version": None,
        "source_build": "a" * 40,
    }


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
    summary = runs._report_summary(
        {
            "status": "FAIL",
            "rubric_version": "1.0.1",
            "golden_suite": {"expected_case_ids": ["M04"], "executed_case_ids": ["M04"]},
            "case_results": [
                {
                    "case_id": "M04",
                    "priority": "P0",
                    "status": "FAIL",
                    "failures": ["missing_fact_ids=('scenario_rule',)", "Secret: private document"],
                    "metrics": {"missing_fact_ids": ["scenario_rule"], "semantic_coverage": 0.8},
                }
            ],
            "metrics": {"p0_case_pass_rate": 0.0, "all_case_pass_rate": 0.0},
        }
    )
    assert summary["cases"][0]["diagnostics"] == [{"code": "MISSING_FACT", "fact_id": "scenario_rule"}]
    assert {check["metric"] for check in summary["gate_checks"] if not check["passed"]} == {"p0_case_pass_rate", "all_case_pass_rate"}
    assert "Secret" not in str(summary)


def test_partial_diagnostic_does_not_store_full_suite_score():
    summary = runs._report_summary(
        {
            "status": "INCOMPLETE",
            "golden_suite": {"expected_case_ids": ["M01", "M04"], "executed_case_ids": ["M04"]},
            "metrics": {"weighted_score": 3.9, "p0_case_pass_rate": 1.0, "all_case_pass_rate": 1.0},
            "case_results": [{"case_id": "M04", "status": "FAIL", "failures": ["missing_fact_ids=('scenario_rule',)"], "metrics": {"missing_fact_ids": ["scenario_rule"]}}],
        }
    )
    assert summary["executed_cases"] == 1 and summary["expected_cases"] == 2
    assert summary["weighted_score"] is None
    assert summary["p0_case_pass_rate"] is None
    assert summary["gate_checks"] == []
    assert summary["cases"][0]["diagnostics"] == [{"code": "MISSING_FACT", "fact_id": "scenario_rule"}]


def test_execution_error_gets_safe_diagnostic_code():
    summary = runs._report_summary(
        {
            "status": "INCOMPLETE",
            "golden_suite": {"expected_case_ids": ["M04"], "executed_case_ids": ["M04"]},
            "case_results": [{"case_id": "M04", "status": "INCOMPLETE", "failures": ["private database error"]}],
        }
    )
    assert summary["cases"][0]["diagnostics"] == [{"code": "EXECUTION_ERROR"}]
    assert "private database error" not in str(summary)


def test_diagnostic_requires_completed_selected_case(monkeypatch):
    monkeypatch.setattr(runs, "_configured_tenant", lambda: "qa")
    monkeypatch.setattr(runs, "_source_revision", lambda: "revision")

    def write_report(_path, env, _temp, **_kwargs):
        result_status = env["TEST_CASE_STATUS"]
        Path(env["BUSINESS_DOCUMENT_QUALITY_REPORT"]).write_text(
            json.dumps(
                {
                    "status": "INCOMPLETE",
                    "diagnostic_case_id": "M04",
                    "active_case_id": None,
                    "golden_suite": {"expected_case_ids": ["M04"], "executed_case_ids": ["M04"]},
                    "case_results": [{"case_id": "M04", "status": result_status, "failures": []}],
                }
            ),
            encoding="utf-8",
        )
        return 1

    monkeypatch.setattr(runs, "_run_suite", write_report)
    row = SimpleNamespace(id="execution", source_revision="revision", campaign_id=None, model_name=None, model_digest=None, scope="CASE", case_id="M04")
    monkeypatch.setenv("TEST_CASE_STATUS", "INCOMPLETE")
    status, _, _ = runs._execute(row)
    assert status == "INCOMPLETE"
    row.id = "second-execution"
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
    assert snapshot
    path.write_text("second", encoding="utf-8")
    assert runs._source_fingerprint() != snapshot


@pytest.mark.parametrize("revision", ["new-revision", "unknown", "unverified"])
def test_pending_run_from_previous_image_still_requires_qa_tenant(monkeypatch, revision):
    monkeypatch.setattr(runs, "_source_revision", lambda: revision)
    monkeypatch.setattr(runs, "_configured_tenant", lambda: None)
    status, report, reason = runs._execute(type("QueuedRun", (), {"source_revision": "old-revision"})())
    assert status == "INCOMPLETE"
    assert report is None
    assert reason == "Dedicated quality tenant is not configured"


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
    status, _, _ = runs._execute(SimpleNamespace(id="execution", source_revision="revision"))
    assert status == "INCOMPLETE"
    assert observed == ["true"]


def test_nightly_catalog_deduplicates_digest_and_recovers_after_baseline(monkeypatch):
    database = SqliteDatabase(":memory:")
    monkeypatch.setattr(runs, "_configured_tenant", lambda: "qa")
    monkeypatch.setattr(runs, "_source_revision", lambda: "revision")
    monkeypatch.setattr(
        runs,
        "quality_model_catalog",
        lambda _: {"models": [{"name": "qwen", "digest": "a" * 64, "aliases": ["qwen", "qwen:alias"]}, {"name": "mistral", "digest": "b" * 64, "aliases": ["mistral"]}], "errors": []},
    )
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
    monkeypatch.setattr(
        runs, "quality_model_catalog", lambda _: {"models": [{"name": "first", "digest": "a" * 64, "aliases": ["first"]}, {"name": "second", "digest": "b" * 64, "aliases": ["second"]}], "errors": []}
    )
    monkeypatch.setattr(runs, "_run_campaign_baseline", lambda _: ("PASS", None))
    monkeypatch.setattr(runs, "_execute", lambda row: ("INCOMPLETE", None, "Live model suite timed out") if row.model_name == "first" else ("PASS", {"status": "PASS"}, None))
    with database.bind_ctx([BusinessDocumentQualityCampaign, BusinessDocumentQualityRun], bind_refs=False, bind_backrefs=False):
        database.connect()
        database.create_tables([BusinessDocumentQualityCampaign, BusinessDocumentQualityRun])
        try:
            runs.enqueue_nightly_campaign("2026-09-27")
            assert runs.process_next_run() is True
            assert runs.process_next_run() is True
            assert runs.process_next_run() is True
            assert {row.model_name: row.status for row in BusinessDocumentQualityRun.select()} == {"first": "INCOMPLETE", "second": "PASS"}
            assert BusinessDocumentQualityCampaign.get().status == "PARTIAL"
        finally:
            database.drop_tables([BusinessDocumentQualityRun, BusinessDocumentQualityCampaign])
            database.close()


def test_model_digest_mismatch_does_not_run_suite(monkeypatch):
    monkeypatch.setattr(runs, "_configured_tenant", lambda: "qa")
    monkeypatch.setattr(runs, "_source_revision", lambda: "revision")
    monkeypatch.setattr(runs, "quality_model_digest", lambda *_: "different")
    status, report, reason = runs._execute(SimpleNamespace(id="execution", source_revision="revision", campaign_id=None, model_name="qwen", model_digest="a" * 64, scope="CASE", case_id="M04"))
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

    def timeout(_path, _env, directory, **_kwargs):
        (directory / "report.calls.jsonl").write_text("partial model answer", encoding="utf-8")
        raise subprocess.TimeoutExpired("pytest", 1200)

    monkeypatch.setattr(runs, "_run_suite", timeout)
    status, report, reason = runs._execute(
        SimpleNamespace(
            id="execution",
            source_revision="revision",
            campaign_id=None,
            model_name=None,
            model_digest=None,
            scope="CASE",
            case_id="M04",
        )
    )
    assert status == "INCOMPLETE" and report is None
    assert reason == "Live model suite timed out"
    import document_quality_evidence as evidence
    from zipfile import ZipFile

    with evidence.evidence_archive("run", "execution") as stream, ZipFile(stream) as archive:
        assert archive.read("report.calls.jsonl") == b"partial model answer"


def test_pytest_stages_append_logs_and_keep_timeout_details(monkeypatch, tmp_path):
    def execute(*_args, **kwargs):
        kwargs["stdout"].write("full failure detail\n")
        raise subprocess.TimeoutExpired("pytest", 1)

    monkeypatch.setattr(runs.subprocess, "run", execute)
    for stage in ("golden.py", "live.py"):
        with pytest.raises(subprocess.TimeoutExpired):
            runs._run_suite(stage, {}, tmp_path)
    log = (tmp_path / "pytest.log").read_text(encoding="utf-8")
    assert "golden.py" in log and "live.py" in log
    assert log.count("full failure detail") == 2
    assert log.count("Quality suite exceeded") == 2


def test_changed_digest_after_execution_cannot_pass(monkeypatch):
    monkeypatch.setattr(runs, "_source_revision", lambda: "revision")
    monkeypatch.setattr(runs, "_configured_tenant", lambda: "qa")
    digests = iter(["a" * 64, "b" * 64])
    monkeypatch.setattr(runs, "quality_model_digest", lambda *_: next(digests))
    monkeypatch.setattr(runs, "quality_model_configuration", lambda *_: "configuration")

    def write_report(_path, env, _temp, **_kwargs):
        Path(env["BUSINESS_DOCUMENT_QUALITY_REPORT"]).write_text(
            json.dumps(
                {
                    "status": "FAIL",
                    "golden_suite": {"expected_case_ids": ["M04"], "executed_case_ids": ["M04"]},
                    "case_results": [{"case_id": "M04", "status": "FAIL", "failures": ["private answer"]}],
                }
            ),
            encoding="utf-8",
        )
        return 0

    monkeypatch.setattr(runs, "_run_suite", write_report)
    status, report, reason = runs._execute(
        SimpleNamespace(
            id="execution",
            source_revision="revision",
            campaign_id=None,
            model_name="qwen",
            model_digest="a" * 64,
            scope="CASE",
            case_id="M04",
        )
    )
    assert status == "INCOMPLETE" and reason == "Model digest changed during execution"
    assert "private answer" not in str(report)


@pytest.mark.parametrize("revision", ["new-image", "unknown", "unverified"])
def test_changed_source_does_not_discard_completed_daily_result(monkeypatch, revision):
    monkeypatch.setattr(runs, "_source_revision", lambda: revision)
    monkeypatch.setattr(runs, "_source_fingerprint", lambda: "before")
    monkeypatch.setattr(runs, "_configured_tenant", lambda: "qa")

    def write_report(_path, env, _temp, **_kwargs):
        assert env["GITHUB_SHA"] == revision
        monkeypatch.setattr(runs, "_source_revision", lambda: "another-image")
        monkeypatch.setattr(runs, "_source_fingerprint", lambda: "after")
        Path(env["BUSINESS_DOCUMENT_QUALITY_REPORT"]).write_text(
            json.dumps(
                {
                    "status": "PASS",
                    "diagnostic_case_id": "M04",
                    "golden_suite": {"expected_case_ids": ["M04"], "executed_case_ids": ["M04"]},
                    "case_results": [{"case_id": "M04", "status": "PASS", "failures": []}],
                }
            ),
            encoding="utf-8",
        )
        return 0

    monkeypatch.setattr(runs, "_run_suite", write_report)
    status, _, reason = runs._execute(
        SimpleNamespace(
            id="execution",
            source_revision="revision",
            campaign_id=None,
            model_name=None,
            model_digest=None,
            scope="CASE",
            case_id="M04",
        )
    )
    assert status == "DIAGNOSTIC" and reason is None


def test_nightly_comparison_keeps_shared_conditions_and_allows_model_parameters():
    reference = {
        "source_revision": "rev",
        "suite_sha256": "sha256:" + "a" * 64,
        "rubric_version": "1.1.0",
        "template_version": "v1",
        "prompt_hashes": {"draft.txt": "sha256:" + "b" * 64},
        "parameter_profiles": [{"temperature": 0, "top_p": 0.1}],
    }
    signature = runs._comparison_signature(reference)
    for changed in (
        {"rubric_version": "1.2.0"},
        {"prompt_hashes": {"draft.txt": "sha256:" + "c" * 64}},
        {"suite_sha256": "sha256:" + "d" * 64},
    ):
        assert runs._comparison_signature({**reference, **changed}) != signature
    assert runs._comparison_signature({**reference, "parameter_profiles": [{"extra_body": {"num_ctx": 262144}, "max_completion_tokens": 8192}]}) == signature
    assert runs._comparison_signature({**reference, "source_revision": "another-image", "source_dirty": True, "source_fingerprint": "changed"}) == signature


@pytest.mark.parametrize("product", ["business_requirements", "technical_specification"])
def test_daily_baseline_runs_across_application_changes(monkeypatch, product):
    monkeypatch.setattr(runs, "_source_revision", lambda: "new-image")
    monkeypatch.setattr(runs, "_source_fingerprint", lambda: "new-files")
    calls = []

    def baseline(path, _env, _temp):
        calls.append(path)
        monkeypatch.setattr(runs, "_source_revision", lambda: "another-image")
        monkeypatch.setattr(runs, "_source_fingerprint", lambda: "another-files")
        return 0

    monkeypatch.setattr(runs, "_run_suite", baseline)
    campaign = SimpleNamespace(id="daily-baseline", document_type=product, source_revision="old-image", models={"source_fingerprint": "old-files"})
    assert runs._run_campaign_baseline(campaign) == ("PASS", None)
    assert len(calls) == 1
    assert all(Path(path).is_file() for path in calls[0])
    assert {Path(path).name for path in calls[0]} == (
        {"test_alignment.py", "test_alignment_citations.py", "test_alignment_metrics.py", "test_alignment_evaluator.py"}
        if product == "technical_specification"
        else {"test_golden_dialogue_harness.py"}
    )


@pytest.mark.parametrize("exit_code,status", [(1, "FAIL"), (2, "INCOMPLETE"), (5, "INCOMPLETE")])
def test_baseline_distinguishes_failed_assertions_from_an_unexecuted_suite(monkeypatch, exit_code, status):
    monkeypatch.setattr(runs, "_run_suite", lambda *_args: exit_code)
    campaign = SimpleNamespace(id="baseline-exit", document_type="technical_specification", source_revision="source")
    assert runs._run_campaign_baseline(campaign) == (status, "BASELINE_FAILED")


def test_baseline_paths_share_one_pytest_process(monkeypatch, tmp_path):
    commands = []

    def execute(command, **_kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(runs.subprocess, "run", execute)
    assert runs._run_suite(("validator.py", "scorer.py"), {}, tmp_path) == 0
    assert len(commands) == 1
    assert commands[0][commands[0].index("no:cacheprovider") + 1 :][:2] == ["validator.py", "scorer.py"]


def test_daily_full_result_survives_different_campaign_and_runtime_builds(monkeypatch):
    database = SqliteDatabase(":memory:")
    monkeypatch.setattr(runs, "_configured_tenant", lambda: "qa")
    monkeypatch.setattr(runs, "_source_revision", lambda: "current-image")
    monkeypatch.setattr(runs, "_source_fingerprint", lambda: "current-files")
    raw = {"status": "PASS", "golden_suite": {"expected_case_ids": ["M04"], "executed_case_ids": ["M04"]}, "case_results": [{"case_id": "M04", "status": "PASS", "failures": []}]}

    def execute(_path, env, _temp, **_kwargs):
        Path(env["BUSINESS_DOCUMENT_QUALITY_REPORT"]).write_text(json.dumps(raw), encoding="utf-8")
        monkeypatch.setattr(runs, "_source_revision", lambda: "changed-image")
        monkeypatch.setattr(runs, "_source_fingerprint", lambda: "changed-files")
        return 0

    verified = []
    monkeypatch.setattr(runs, "_run_suite", execute)
    monkeypatch.setattr(runs, "verify_report", lambda path, root: verified.append(json.loads(path.read_text(encoding="utf-8"))))
    with database.bind_ctx([BusinessDocumentQualityCampaign, BusinessDocumentQualityRun], bind_refs=False, bind_backrefs=False):
        database.create_tables([BusinessDocumentQualityCampaign, BusinessDocumentQualityRun])
        BusinessDocumentQualityCampaign.create(id="daily", schedule_key="nightly:day", source_revision="old-image", models={"source_fingerprint": "old-files"}, baseline_status="PASS")
        BusinessDocumentQualityRun.create(
            id="previous", campaign_id="daily", trigger="NIGHTLY", status="PASS", report={**runs._report_summary(raw), "source_revision": "previous-image", "source_dirty": True}
        )
        row = SimpleNamespace(id="daily-model", source_revision="queued-image", campaign_id="daily", model_name=None, model_digest=None, scope="FULL")
        status, summary, reason = runs._execute(row)
        assert status == "PASS" and reason is None
        assert summary["executed_cases"] == 1
        assert verified == [raw]
        database.close()


def test_missing_qa_tenant_is_recorded_and_same_day_recovers(monkeypatch):
    database = SqliteDatabase(":memory:")
    monkeypatch.setattr(runs, "_configured_tenant", lambda: "qa")
    monkeypatch.setattr(runs, "_source_revision", lambda: "revision")
    monkeypatch.setattr(runs, "_source_fingerprint", lambda: None)

    def unavailable(_tenant):
        raise LookupError("Tenant not found")

    monkeypatch.setattr(runs, "quality_model_catalog", unavailable)
    with database.bind_ctx([BusinessDocumentQualityCampaign, BusinessDocumentQualityRun], bind_refs=False, bind_backrefs=False):
        database.connect()
        database.create_tables([BusinessDocumentQualityCampaign, BusinessDocumentQualityRun])
        first = runs.enqueue_nightly_campaign("2026-10-19")
        assert first["status"] == "INCOMPLETE" and first["reason_code"] == "QA_TENANT_MISSING"
        assert runs.enqueue_nightly_campaign("2026-10-19")["id"] == first["id"]
        monkeypatch.setattr(runs, "quality_model_catalog", lambda _: {"models": [{"name": "qwen", "digest": "a" * 64, "aliases": ["qwen"]}], "errors": []})
        recovered = runs.enqueue_nightly_campaign("2026-10-19")
        assert recovered["id"] == first["id"] and recovered["status"] == "PENDING"
        assert recovered["reason_code"] is None
        runs.enqueue_nightly_campaign("2026-10-19")
        assert BusinessDocumentQualityCampaign.select().count() == 1
        assert BusinessDocumentQualityRun.select().count() == 1
        database.close()


def test_scheduler_failure_does_not_starve_pending_runs(monkeypatch):
    def fail():
        raise LookupError("missing prerequisite")

    observed = []
    monkeypatch.setattr(runs, "schedule_due", fail)
    monkeypatch.setattr(runs, "process_next_run", lambda: observed.append("processed") or True)
    assert runs.process_quality_cycle() is True
    assert observed == ["processed"]


@pytest.mark.parametrize("change,expected", [("parameters", "Evaluation conditions changed during model execution"), ("stable", "Live model qualification did not pass")])
def test_cross_model_parameters_do_not_mask_case_failure_but_temporal_changes_do(monkeypatch, change, expected):
    database = SqliteDatabase(":memory:")
    monkeypatch.setattr(runs, "_source_revision", lambda: "revision")
    monkeypatch.setattr(runs, "_source_fingerprint", lambda: None)
    monkeypatch.setattr(runs, "_configured_tenant", lambda: "qa")
    monkeypatch.setattr(runs, "quality_model_digest", lambda *_: "a" * 64)
    configs = iter(["start", "changed" if change == "parameters" else "start"])
    monkeypatch.setattr(runs, "quality_model_configuration", lambda *_: next(configs))
    raw = {
        "source_revision": "revision",
        "status": "FAIL",
        "golden_suite": {"expected_case_ids": ["M06"], "executed_case_ids": ["M06"]},
        "case_results": [{"case_id": "M06", "status": "FAIL", "failures": ["job:REQUIREMENT_COVERAGE_FAILED"]}],
        "ai": {"parameter_profiles": [{"extra_body": {"num_ctx": 262144}}]},
    }

    def report(_path, env, _temp, **_kwargs):
        Path(env["BUSINESS_DOCUMENT_QUALITY_REPORT"]).write_text(json.dumps(raw), encoding="utf-8")
        return 1

    monkeypatch.setattr(runs, "_run_suite", report)
    with database.bind_ctx([BusinessDocumentQualityCampaign, BusinessDocumentQualityRun], bind_refs=False, bind_backrefs=False):
        database.connect()
        database.create_tables([BusinessDocumentQualityCampaign, BusinessDocumentQualityRun])
        BusinessDocumentQualityCampaign.create(id="campaign", schedule_key="nightly:day", source_revision="revision", models={}, baseline_status="PASS")
        reference = {**runs._report_summary(raw), "parameter_profiles": [{"extra_body": {"num_ctx": 393216}}]}
        BusinessDocumentQualityRun.create(id="reference", campaign_id="campaign", trigger="NIGHTLY", status="FAIL", report=reference)
        row = SimpleNamespace(id="execution", source_revision="revision", campaign_id="campaign", model_name="qwen", model_digest="a" * 64, scope="FULL")
        status, summary, reason = runs._execute(row)
        assert reason == expected
        assert status == ("INCOMPLETE" if change == "parameters" else "FAIL")
        assert summary["cases"][0]["diagnostics"] == [{"code": "JOB_FAILED", "job_code": "REQUIREMENT_COVERAGE_FAILED"}]
        database.close()


def test_m06_diagnostics_keep_job_and_oracle_codes_without_generated_text():
    item = {"status": "FAIL", "failures": ["job:REQUIREMENT_COVERAGE_FAILED", "requirement:latency:threshold", "unrelated_section_changed:4.1", "private document text"]}
    result = runs._safe_case_diagnostics(item)
    assert result == [
        {"code": "JOB_FAILED", "job_code": "REQUIREMENT_COVERAGE_FAILED"},
        {"code": "REQUIREMENT", "requirement_id": "latency", "check_id": "threshold"},
        {"code": "UNRELATED_SECTION_CHANGED", "section_id": "4.1"},
    ]
    assert runs._public_report({"cases": [{"case_id": "M06", "diagnostics": result}]}, detail=True)["cases"][0]["diagnostics"] == result


@pytest.mark.parametrize("job_code", ["MODEL_CONTEXT_TOO_LARGE", "WORKER_FAILURE"])
def test_execution_diagnostic_keeps_safe_class_and_job_code_only(job_code):
    result = runs._safe_case_diagnostics(
        {
            "status": "INCOMPLETE",
            "execution_error": {"error_type": "AssertionError", "job_code": job_code, "check_id": "job_completed", "message": "private exception", "code": "PRIVATE"},
            "failures": ["AssertionError: private exception"],
        }
    )
    assert result == [{"code": "EXECUTION_ERROR", "error_type": "AssertionError", "job_code": job_code, "check_id": "job_completed"}]


def test_admin_run_detail_never_returns_legacy_raw_content_or_exception():
    database = SqliteDatabase(":memory:")
    with database.bind_ctx([BusinessDocumentQualityRun], bind_refs=False, bind_backrefs=False):
        database.connect()
        database.create_tables([BusinessDocumentQualityRun])
        try:
            BusinessDocumentQualityRun.create(
                id="legacy-run",
                trigger="MANUAL",
                status="FAIL",
                source_revision="revision",
                reason="private exception text",
                report={
                    "status": "FAIL",
                    "raw_response": "private model answer",
                    "cases": [{"case_id": "M04", "status": "FAIL", "failure": "private document text", "diagnostics": [{"code": "PRIVATE_DOCUMENT_TEXT"}]}],
                },
            )
            detail = runs.get_quality_run("legacy-run")
            assert detail["report"]["cases"][0]["diagnostics"] == []
            assert "private" not in str(detail).lower()
        finally:
            database.drop_tables([BusinessDocumentQualityRun])
            database.close()
