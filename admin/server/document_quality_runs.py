"""Durable scheduling and execution of Business Documents quality checks."""

import json
import hashlib
import importlib.util
import logging
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from math import isfinite
import re
from zoneinfo import ZoneInfo

from api.db.db_models import BusinessDocumentQualityCampaign, BusinessDocumentQualityRun
from common.misc_utils import get_uuid
from common.time_utils import current_timestamp
from tools.quality.verify_business_document_quality_report import QualityGateFailed, verify_report
from document_quality_models import quality_model_catalog, quality_model_digest


ROOT = Path(__file__).resolve().parents[2]
NIGHTLY_HOUR = 2
MONTHLY_HOUR = 3
MOSCOW = ZoneInfo("Europe/Moscow")
RUN_TIMEOUT_SECONDS = 20 * 60
_SAFE_ID = re.compile(r"^[A-Za-z0-9_:-]{1,80}$")
_SAFE_LABEL = re.compile(r"^[A-Za-z0-9._:/-]{1,256}$")
_SAFE_HASH = re.compile(r"^sha256:[a-f0-9]{64}$")
_FAILURE_CODES = {
    "protocol_not_separated",
    "invalid_question_bounds",
    "template_section_order_mismatch",
    "conceptual_plantuml_missing",
    "scenario_text_missing",
    "scenario_plantuml_missing",
    "unexpected_revision_created",
}
_DIAGNOSTIC_CODES = (
    {"MISSING_FACT", "UNCLASSIFIED", "ASSERTION_FAILED", "EXECUTION_ERROR"}
    | {code.upper() for code in _FAILURE_CODES}
    | {
        "QUESTIONS_COUNT",
        "QUESTION_TARGETS",
        "GROUNDED_CLAIM_COUNT",
        "GROUNDED_REFERENCE_PRECISION",
        "MISSING_FACT_IDS",
        "DUPLICATE_CONTENT",
        "MISPLACED_FACT_COUNT",
        "CONTRADICTIONS",
        "WEIGHTED_SCORE",
        "REQUIRED_FRAGMENT_MISSING",
        "FORBIDDEN_FRAGMENT_PRESENT",
        "HARD_FAILURES",
    }
)


def _configured_tenant() -> str:
    return os.environ.get("BUSINESS_DOCUMENT_QUALITY_TENANT_ID", "").strip()


def _source_revision() -> str:
    source = ROOT / "SOURCE_REVISION"
    return source.read_text(encoding="utf-8").strip()[:64] if source.is_file() else "unknown"


def _source_fingerprint() -> str | None:
    if _source_revision() != "unverified" and os.environ.get("RAGFLOW_QA_SOURCE_DIRTY") != "1":
        return None
    digest = hashlib.sha256()
    for directory in ("admin/server", "api", "agent", "business_documents", "common", "rag", "test/evals/business_documents", "tools/quality"):
        for path in sorted((ROOT / directory).rglob("*")):
            if not path.is_file() or path.suffix not in {".py", ".json", ".md"}:
                continue
            digest.update(path.relative_to(ROOT).as_posix().encode("utf-8"))
            with path.open("rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
    return digest.hexdigest()


def _campaign_source_matches(campaign: BusinessDocumentQualityCampaign) -> bool:
    if campaign.source_revision != _source_revision() or campaign.source_revision == "unknown":
        return False
    fingerprint = _source_fingerprint()
    return fingerprint is None or fingerprint == (campaign.models or {}).get("source_fingerprint")


def _safe_number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value) else None


def _public_checks(value) -> list[dict]:
    checks = []
    for item in value[:30] if isinstance(value, list) else []:
        if not isinstance(item, dict) or not isinstance(item.get("metric"), str) or not _SAFE_ID.fullmatch(item["metric"]):
            continue
        actual, threshold = _safe_number(item.get("actual")), _safe_number(item.get("threshold"))
        if actual is not None and threshold is not None and isinstance(item.get("passed"), bool):
            checks.append({"metric": item["metric"], "actual": actual, "threshold": threshold, "passed": item["passed"]})
    return checks


def _public_report(value, *, detail: bool) -> dict | None:
    if not isinstance(value, dict):
        return None
    report = {}
    for key in ("status", "suite_id", "suite_version", "rubric_version", "template_version", "provider", "model"):
        item = value.get(key)
        if isinstance(item, str) and _SAFE_LABEL.fullmatch(item):
            report[key] = item
    for key in ("suite_sha256", "model_digest"):
        item = value.get(key)
        if isinstance(item, str) and (_SAFE_HASH.fullmatch(item) or re.fullmatch(r"[a-f0-9]{64}", item)):
            report[key] = item
    for key in ("expected_cases", "executed_cases", "duration_ms", "total_tokens", "weighted_score", "grounded_reference_precision", "p0_case_pass_rate", "all_case_pass_rate"):
        item = _safe_number(value.get(key))
        if item is not None:
            report[key] = item
    if isinstance(value.get("source_dirty"), bool):
        report["source_dirty"] = value["source_dirty"]
    for key in ("criterion_scores", "metrics"):
        source = value.get(key)
        if isinstance(source, dict):
            report[key] = {name: numeric for name, raw in source.items() if isinstance(name, str) and _SAFE_ID.fullmatch(name) if (numeric := _safe_number(raw)) is not None}
    hashes = value.get("prompt_hashes")
    if isinstance(hashes, dict):
        report["prompt_hashes"] = {
            name: digest for name, digest in hashes.items() if isinstance(name, str) and _SAFE_LABEL.fullmatch(name) and isinstance(digest, str) and _SAFE_HASH.fullmatch(digest)
        }
    profiles = value.get("parameter_profiles")
    if isinstance(profiles, list):
        report["parameter_profiles"] = [
            {key: numeric for key, raw in profile.items() if key in {"temperature", "top_p", "max_completion_tokens"} if (numeric := _safe_number(raw)) is not None}
            for profile in profiles[:20]
            if isinstance(profile, dict)
        ]
    report["gate_checks"] = _public_checks(value.get("gate_checks"))
    if detail:
        cases = []
        for item in value.get("cases", [])[:100] if isinstance(value.get("cases"), list) else []:
            if not isinstance(item, dict) or not isinstance(item.get("case_id"), str) or not _SAFE_ID.fullmatch(item["case_id"]):
                continue
            case = {"case_id": item["case_id"], "gate_checks": _public_checks(item.get("gate_checks"))}
            if item.get("status") in {"PASS", "FAIL", "INCOMPLETE"}:
                case["status"] = item["status"]
            if item.get("priority") in {"P0", "P1", "P2"}:
                case["priority"] = item["priority"]
            count = _safe_number(item.get("failure_count"))
            case["failure_count"] = count if count is not None and count >= 0 else 0
            metrics = item.get("metrics")
            if isinstance(metrics, dict):
                case["metrics"] = {name: numeric for name, raw in metrics.items() if isinstance(name, str) and _SAFE_ID.fullmatch(name) if (numeric := _safe_number(raw)) is not None}
            diagnostics = item.get("diagnostics")
            case["diagnostics"] = (
                [
                    {"code": entry["code"], **({"fact_id": entry["fact_id"]} if isinstance(entry.get("fact_id"), str) and _SAFE_ID.fullmatch(entry["fact_id"]) else {})}
                    for entry in diagnostics[:20]
                    if isinstance(entry, dict) and entry.get("code") in _DIAGNOSTIC_CODES
                ]
                if isinstance(diagnostics, list)
                else []
            )
            cases.append(case)
        report["cases"] = cases
    return report


def _public_run(row: BusinessDocumentQualityRun, *, detail: bool = False) -> dict:
    report = _public_report(row.report, detail=detail)
    return {
        "id": row.id,
        "trigger": row.trigger,
        "status": row.status,
        "requested_at": row.create_time,
        "started_at": row.started_at,
        "finished_at": row.finished_at,
        "source_revision": row.source_revision,
        "campaign_id": row.campaign_id,
        "model_name": row.model_name,
        "model_digest": row.model_digest,
        "scope": row.scope,
        "case_id": row.case_id,
        "reason_code": row.reason_code,
        "reason": None,
        "report": report,
    }


def list_quality_runs() -> dict:
    recent = list(BusinessDocumentQualityRun.select().order_by(BusinessDocumentQualityRun.create_time.desc()).limit(500))
    monthly = BusinessDocumentQualityRun.select().where(BusinessDocumentQualityRun.trigger == "MONTHLY").order_by(BusinessDocumentQualityRun.create_time.desc()).limit(12)
    rows = {row.id: row for row in (*recent, *monthly)}
    ordered = sorted(rows.values(), key=lambda row: (row.create_time, row.id), reverse=True)
    total = BusinessDocumentQualityRun.select().count()
    return {"configured": bool(_configured_tenant()), "source_revision": _source_revision(), "total": total, "truncated": total > len(ordered), "runs": [_public_run(row) for row in ordered]}


def get_quality_run(run_id: str) -> dict | None:
    row = BusinessDocumentQualityRun.get_or_none(BusinessDocumentQualityRun.id == run_id)
    return _public_run(row, detail=True) if row else None


def _public_campaign(row: BusinessDocumentQualityCampaign, *, detail: bool = False) -> dict:
    runs = list(BusinessDocumentQualityRun.select().where(BusinessDocumentQualityRun.campaign_id == row.id).order_by(BusinessDocumentQualityRun.create_time, BusinessDocumentQualityRun.id))
    return {
        "id": row.id,
        "status": row.status,
        "source_revision": row.source_revision,
        "requested_at": row.create_time,
        "started_at": row.started_at,
        "finished_at": row.finished_at,
        "baseline_status": row.baseline_status,
        "baseline_report": row.baseline_report if detail else None,
        "reason_code": row.reason_code,
        "models": row.models if detail else None,
        "runs": [_public_run(item) for item in runs],
    }


def list_quality_campaigns() -> dict:
    rows = BusinessDocumentQualityCampaign.select().order_by(BusinessDocumentQualityCampaign.create_time.desc()).limit(30)
    return {"campaigns": [_public_campaign(row) for row in rows]}


def get_quality_campaign(campaign_id: str) -> dict | None:
    row = BusinessDocumentQualityCampaign.get_or_none(BusinessDocumentQualityCampaign.id == campaign_id)
    return _public_campaign(row, detail=True) if row else None


def list_quality_models() -> dict:
    tenant = _configured_tenant()
    if not tenant:
        return {"configured": False, "models": [], "errors": [], "case_ids": []}
    return {"configured": True, "case_ids": sorted(_known_case_ids()), **quality_model_catalog(tenant)}


def enqueue_quality_run(trigger: str, *, requested_by: str | None = None, schedule_key: str | None = None, model: str | None = None, scope: str = "FULL", case_id: str | None = None) -> dict:
    if not _configured_tenant():
        raise ValueError("Dedicated Business Documents quality tenant is not configured")
    if trigger not in {"MANUAL", "NIGHTLY", "MONTHLY"}:
        raise ValueError("Unknown quality run trigger")
    if scope not in {"FULL", "CASE"} or (scope == "CASE") != bool(case_id):
        raise ValueError("Choose a full run or one case")
    if case_id and (not isinstance(case_id, str) or not _SAFE_ID.fullmatch(case_id) or case_id not in _known_case_ids()):
        raise ValueError("Unknown quality case")
    if model is not None and (not isinstance(model, str) or not model or len(model) > 256):
        raise ValueError("Unknown quality model")
    selected = None
    if model:
        selected = next((entry for entry in quality_model_catalog(_configured_tenant())["models"] if model in entry["aliases"]), None)
        if selected is None:
            raise ValueError("Selected model is not in the QA Ollama catalog")
    if trigger == "MANUAL":
        active = (
            BusinessDocumentQualityRun.select()
            .where(
                (BusinessDocumentQualityRun.trigger == "MANUAL")
                & (BusinessDocumentQualityRun.status.in_(("PENDING", "RUNNING")))
                & (BusinessDocumentQualityRun.model_name == (selected["name"] if selected else None))
                & (BusinessDocumentQualityRun.scope == scope)
                & (BusinessDocumentQualityRun.case_id == case_id)
            )
            .order_by(BusinessDocumentQualityRun.create_time)
            .first()
        )
        if active is not None:
            return _public_run(active)
        identity = json.dumps([selected["digest"] if selected else "default", scope, case_id], separators=(",", ":"))
        schedule_key = "manual:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:56]
    now = current_timestamp()
    values = {
        "id": get_uuid(),
        "schedule_key": schedule_key,
        "trigger": trigger,
        "status": "PENDING",
        "requested_by": requested_by,
        "model_name": selected["name"] if selected else None,
        "model_digest": selected["digest"] if selected else None,
        "scope": scope,
        "case_id": case_id,
        "source_revision": _source_revision(),
        "create_time": now,
        "update_time": now,
    }
    if schedule_key:
        row, _ = BusinessDocumentQualityRun.get_or_create(schedule_key=schedule_key, defaults=values)
    else:
        row = BusinessDocumentQualityRun.create(**values)
    return _public_run(row)


def _known_case_ids() -> set[str]:
    suite = ROOT / "agent/business_requirements/golden_model_quality/v1.json"
    value = json.loads(suite.read_text(encoding="utf-8"))
    return {item["id"] for item in value.get("cases", []) if isinstance(item, dict) and isinstance(item.get("id"), str)}


def enqueue_nightly_campaign(day: str) -> dict:
    key = f"nightly:{day}"
    existing = BusinessDocumentQualityCampaign.get_or_none(BusinessDocumentQualityCampaign.schedule_key == key)
    if existing is not None:
        return _public_campaign(existing)
    tenant = _configured_tenant()
    if not tenant:
        raise ValueError("Dedicated Business Documents quality tenant is not configured")
    try:
        catalog = quality_model_catalog(tenant)
        reason_code = "CATALOG_PARTIAL" if catalog["errors"] else "NO_COMPLETION_MODELS" if not catalog["models"] else None
    except (OSError, ValueError, TimeoutError):
        logging.exception("Business Documents quality model catalog is unavailable")
        catalog = {"models": [], "errors": ["CATALOG_UNAVAILABLE"]}
        reason_code = "CATALOG_UNAVAILABLE"
    fingerprint = _source_fingerprint()
    if fingerprint is not None:
        catalog["source_fingerprint"] = fingerprint
    now = current_timestamp()
    with BusinessDocumentQualityCampaign._meta.database.atomic():
        campaign, created = BusinessDocumentQualityCampaign.get_or_create(
            schedule_key=key,
            defaults={
                "id": get_uuid(),
                "source_revision": _source_revision(),
                "models": catalog,
                "reason_code": reason_code,
                "status": "PENDING" if catalog["models"] else "INCOMPLETE",
                "create_time": now,
                "update_time": now,
            },
        )
        if created:
            for item in catalog["models"]:
                BusinessDocumentQualityRun.create(
                    id=get_uuid(),
                    trigger="NIGHTLY",
                    status="PENDING",
                    campaign_id=campaign.id,
                    model_name=item["name"],
                    model_digest=item["digest"],
                    scope="FULL",
                    source_revision=campaign.source_revision,
                    create_time=now,
                    update_time=now,
                )
    return _public_campaign(campaign)


def schedule_due(now: datetime | None = None) -> None:
    if not _configured_tenant():
        return
    local = (now or datetime.now(MOSCOW)).astimezone(MOSCOW)
    if local.hour >= NIGHTLY_HOUR:
        enqueue_nightly_campaign(local.date().isoformat())
    if local.day > 1 or local.hour >= MONTHLY_HOUR:
        enqueue_quality_run("MONTHLY", schedule_key=f"monthly:{local.year:04d}-{local.month:02d}")


def _run_suite(test_path: str, env: dict[str, str], temp: Path, *, deadline: float | None = None) -> int:
    timeout = RUN_TIMEOUT_SECONDS if deadline is None else min(RUN_TIMEOUT_SECONDS, deadline - time.monotonic())
    if timeout <= 0:
        raise subprocess.TimeoutExpired(test_path, RUN_TIMEOUT_SECONDS)
    with (temp / "pytest.log").open("w", encoding="utf-8") as log:
        completed = subprocess.run(
            [sys.executable, "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider", test_path, "--basetemp", str(temp / "pytest")],
            cwd=ROOT,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
    if completed.returncode:
        logging.warning("Business Documents quality suite failed: %s (pytest exit %d)", test_path, completed.returncode)
    return completed.returncode


def _safe_case_diagnostics(item: dict) -> list[dict]:
    diagnostics: list[dict] = []
    if item.get("status") == "INCOMPLETE":
        return [{"code": "EXECUTION_ERROR"}]
    metrics = item.get("metrics") if isinstance(item.get("metrics"), dict) else {}
    for fact_id in metrics.get("missing_fact_ids") or []:
        if isinstance(fact_id, str) and _SAFE_ID.fullmatch(fact_id):
            diagnostics.append({"code": "MISSING_FACT", "fact_id": fact_id})
    for failure in item.get("failures") or []:
        if not isinstance(failure, str):
            continue
        code = failure.split("=", 1)[0].split(":", 1)[0]
        if code == "missing_fact_ids" and diagnostics:
            continue
        if code in _FAILURE_CODES or code in {
            "questions_count",
            "question_targets",
            "grounded_claim_count",
            "grounded_reference_precision",
            "missing_fact_ids",
            "duplicate_content",
            "misplaced_fact_count",
            "contradictions",
            "weighted_score",
            "required_fragment_missing",
            "forbidden_fragment_present",
            "hard_failures",
        }:
            diagnostics.append({"code": code.upper()})
    if item.get("status") == "FAIL" and not diagnostics:
        diagnostics.append({"code": "UNCLASSIFIED"})
    return diagnostics[:20]


def _gate_checks(metrics: dict, rubric: dict) -> list[dict]:
    gate = rubric.get("live_suite_gate") or {}
    checks = []
    for metric, threshold, operator in (
        ("weighted_score", rubric.get("pass_threshold"), "min"),
        ("p0_case_pass_rate", gate.get("p0_case_pass_rate"), "min"),
        ("all_case_pass_rate", gate.get("all_case_pass_rate"), "min"),
        ("grounded_reference_precision", gate.get("minimum_grounded_fact_precision"), "min"),
        ("semantic_coverage", gate.get("minimum_semantic_coverage"), "min"),
        ("duplication_rate", gate.get("maximum_duplication_rate"), "max"),
        ("misplacement_rate", gate.get("maximum_misplacement_rate"), "max"),
        ("contradiction_rate", gate.get("maximum_contradiction_rate"), "max"),
        ("hard_failure_count", gate.get("hard_failure_count"), "max"),
    ):
        actual = len(metrics.get("hard_failures") or []) if metric == "hard_failure_count" else metrics.get(metric)
        if isinstance(actual, (int, float)) and not isinstance(actual, bool) and isinstance(threshold, (int, float)):
            checks.append({"metric": metric, "actual": actual, "threshold": threshold, "passed": actual >= threshold if operator == "min" else actual <= threshold})
    return checks


def _report_summary(report: dict) -> dict:
    ai = report.get("ai") or {}
    metrics = report.get("metrics") or {}
    suite = report.get("golden_suite") or {}
    rubric = json.loads((ROOT / "agent/business_requirements/evals/rubric.v1.json").read_text(encoding="utf-8"))
    expected_cases = len(suite.get("expected_case_ids") or [])
    executed_cases = len(suite.get("executed_case_ids") or [])
    complete = expected_cases > 0 and expected_cases == executed_cases and report.get("status") in {"PASS", "FAIL"}
    return {
        "status": report.get("status"),
        "generated_at": report.get("generated_at"),
        "source_revision": report.get("source_revision"),
        "source_dirty": report.get("source_dirty"),
        "rubric_version": report.get("rubric_version"),
        "template_version": report.get("template_version"),
        "model_digest": report.get("model_weights_digest") or None,
        "suite_sha256": suite.get("sha256"),
        "suite_id": suite.get("suite_id"),
        "suite_version": suite.get("suite_version"),
        "prompt_hashes": {key: value for key, value in (report.get("prompts") or {}).items() if isinstance(key, str) and isinstance(value, str) and value.startswith("sha256:")},
        "parameter_profiles": ai.get("parameter_profiles") or [],
        "expected_cases": expected_cases,
        "executed_cases": executed_cases,
        "provider": ai.get("provider"),
        "model": ai.get("model"),
        "duration_ms": ai.get("duration_ms"),
        "total_tokens": (ai.get("token_usage") or {}).get("total_tokens"),
        "weighted_score": metrics.get("weighted_score") if complete else None,
        "grounded_reference_precision": metrics.get("grounded_reference_precision"),
        "p0_case_pass_rate": metrics.get("p0_case_pass_rate") if complete else None,
        "all_case_pass_rate": metrics.get("all_case_pass_rate") if complete else None,
        "criterion_scores": metrics.get("criterion_scores") or {},
        "metrics": {
            key: value
            for key, value in metrics.items()
            if key in {"semantic_coverage", "duplication_rate", "misplacement_rate", "contradiction_rate", "hard_failure_count"} and isinstance(value, (int, float)) and not isinstance(value, bool)
        },
        "gate_checks": _gate_checks(metrics, rubric) if complete else [],
        "cases": [
            {
                "case_id": item.get("case_id"),
                "priority": item.get("priority"),
                "status": item.get("status"),
                "failure_count": len(item.get("failures") or []),
                "diagnostics": _safe_case_diagnostics(item),
                "metrics": {
                    key: value
                    for key, value in (item.get("metrics") or {}).items()
                    if key in {"semantic_coverage", "grounded_reference_precision", "duplication_rate", "misplacement_rate", "contradiction_rate"}
                    and isinstance(value, (int, float))
                    and not isinstance(value, bool)
                },
                "gate_checks": _gate_checks(item.get("metrics") or {}, rubric),
            }
            for item in report.get("case_results") or []
            if isinstance(item, dict)
        ],
    }


def _golden_summary(report: dict) -> dict:
    cases = report.get("cases") or []
    return {
        "status": "FAIL",
        "suite_id": report.get("suite_id"),
        "suite_version": report.get("suite_version"),
        "expected_cases": report.get("expected_cases"),
        "executed_cases": report.get("executed_cases"),
        "criterion_scores": {},
        "cases": [
            {
                "case_id": item.get("case_id"),
                "status": item.get("status"),
                "failure_count": item.get("failure_count"),
                "diagnostics": [{"code": "ASSERTION_FAILED"}] if item.get("status") == "FAIL" else [],
            }
            for item in cases
            if isinstance(item, dict)
        ][:50],
    }


def _failed_test_names(temp: Path) -> str:
    lines = (temp / "pytest.log").read_text(encoding="utf-8", errors="replace").splitlines()
    names = []
    for line in lines:
        if not line.startswith("FAILED "):
            continue
        candidate = line.split(" ", 2)[1]
        if candidate and all(character.isalnum() or character in "_./:-[]" for character in candidate):
            names.append(candidate.rsplit("::", 1)[-1][:80])
    return ", ".join(names[:3])[:120]


def _comparison_signature(summary: dict) -> tuple:
    return (
        summary.get("source_revision"),
        summary.get("suite_sha256"),
        summary.get("rubric_version"),
        summary.get("template_version"),
        json.dumps(summary.get("prompt_hashes"), sort_keys=True),
        json.dumps(summary.get("parameter_profiles"), sort_keys=True),
        summary.get("source_dirty"),
    )


def _execute(row: BusinessDocumentQualityRun) -> tuple[str, dict | None, str | None]:
    current_revision = _source_revision()
    initial_fingerprint = _source_fingerprint()
    if not row.source_revision or current_revision == "unknown" or row.source_revision != current_revision:
        return "INCOMPLETE", None, "Queued source revision differs from the running image or is unknown"
    tenant = _configured_tenant()
    if not tenant:
        return "INCOMPLETE", None, "Dedicated quality tenant is not configured"
    live_test = ROOT / "test/evals/business_documents/test_live_model_quality.py"
    golden_test = ROOT / "test/evals/business_documents/test_golden_dialogue_harness.py"
    if not (live_test.is_file() and golden_test.is_file()):
        return "INCOMPLETE", None, "Quality test assets are missing from the runtime"
    if importlib.util.find_spec("pytest") is None:
        return "INCOMPLETE", None, "Quality test dependencies are missing from the runtime"
    if getattr(row, "campaign_id", None):
        campaign = BusinessDocumentQualityCampaign.get_or_none(BusinessDocumentQualityCampaign.id == row.campaign_id)
        if campaign is None or campaign.baseline_status != "PASS":
            return "INCOMPLETE", None, "Nightly baseline did not pass"
        if not _campaign_source_matches(campaign):
            return "INCOMPLETE", None, "Evaluation source changed during the nightly series"
    model_name = getattr(row, "model_name", None)
    model_digest = getattr(row, "model_digest", None)
    if model_name and model_digest and quality_model_digest(tenant, model_name) != model_digest:
        return "INCOMPLETE", None, "Model digest changed before execution"
    with tempfile.TemporaryDirectory(prefix="business-document-quality-") as directory:
        temp = Path(directory)
        report_path = temp / "report.json"
        golden_report_path = temp / "golden.json"
        env = os.environ.copy()
        env.update(
            {
                "BUSINESS_DOCUMENT_LIVE_LLM": "1",
                "BUSINESS_DOCUMENT_LIVE_TENANT_ID": tenant,
                "BUSINESS_DOCUMENT_RELATED_FILE_SEARCH_ENABLED": "true",
                "BUSINESS_DOCUMENT_QUALITY_REPORT": str(report_path),
                "BUSINESS_DOCUMENT_GOLDEN_REPORT": str(golden_report_path),
                "GITHUB_SHA": row.source_revision or "unknown",
                "PYTHONDONTWRITEBYTECODE": "1",
            }
        )
        if model_name:
            env["BUSINESS_DOCUMENT_QUALITY_MODEL"] = model_name
        if model_digest:
            env["BUSINESS_DOCUMENT_MODEL_DIGEST"] = model_digest
        if getattr(row, "case_id", None):
            env["BUSINESS_DOCUMENT_QUALITY_CASE_ID"] = row.case_id
        run_golden = not getattr(row, "campaign_id", None) and getattr(row, "scope", "FULL") == "FULL"
        deadline = time.monotonic() + RUN_TIMEOUT_SECONDS
        try:
            golden_exit = _run_suite(str(golden_test), env, temp, deadline=deadline) if run_golden else 0
        except subprocess.TimeoutExpired:
            return "INCOMPLETE", None, "Deterministic golden suite timed out"
        if golden_exit:
            try:
                summary = _golden_summary(json.loads(golden_report_path.read_text(encoding="utf-8"))) if golden_report_path.is_file() else None
            except (ValueError, OSError):
                summary = None
            failed_tests = _failed_test_names(temp)
            detail = f": {failed_tests}" if failed_tests else ""
            return "INCOMPLETE", summary, f"Deterministic golden suite failed (pytest exit {golden_exit}){detail}"
        try:
            live_exit = _run_suite(str(live_test), env, temp, deadline=deadline)
        except subprocess.TimeoutExpired:
            if report_path.is_file():
                try:
                    summary = _report_summary(json.loads(report_path.read_text(encoding="utf-8")))
                except (OSError, ValueError):
                    summary = None
            else:
                summary = None
            return "INCOMPLETE", summary, "Live model suite timed out"
        if not report_path.is_file():
            failed_tests = _failed_test_names(temp)
            detail = f": {failed_tests}" if failed_tests else ""
            return "INCOMPLETE", None, f"Live model suite produced no report (pytest exit {live_exit}){detail}"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        summary = _report_summary(report)
        if _source_revision() != row.source_revision:
            return "INCOMPLETE", summary, "Queued source revision differs from the running image or is unknown"
        if initial_fingerprint != _source_fingerprint():
            return "INCOMPLETE", summary, "Evaluation source changed during execution"
        if model_name and model_digest and quality_model_digest(tenant, model_name) != model_digest:
            return "INCOMPLETE", summary, "Model digest changed during execution"
        if getattr(row, "campaign_id", None):
            comparison = (
                BusinessDocumentQualityRun.select(BusinessDocumentQualityRun.report)
                .where((BusinessDocumentQualityRun.campaign_id == row.campaign_id) & (BusinessDocumentQualityRun.status.in_(("PASS", "FAIL"))))
                .first()
            )
            if comparison is not None and _comparison_signature(summary) != _comparison_signature(comparison.report or {}):
                return "INCOMPLETE", summary, "Evaluation conditions changed during the nightly series"
        if getattr(row, "scope", "FULL") == "CASE":
            completed_case = (
                report.get("active_case_id") is None
                and report.get("diagnostic_case_id") == row.case_id
                and len(report.get("case_results") or []) == 1
                and report["case_results"][0].get("case_id") == row.case_id
                and report["case_results"][0].get("status") in {"PASS", "FAIL"}
            )
            return ("DIAGNOSTIC", summary, None) if completed_case else ("INCOMPLETE", summary, "Diagnostic case did not complete")
        if live_exit or report.get("status") != "PASS":
            return "FAIL" if report.get("status") == "FAIL" else "INCOMPLETE", summary, "Live model qualification did not pass"
        try:
            verify_report(report_path, ROOT, expected_revision=row.source_revision if row.source_revision != "unknown" else None)
        except QualityGateFailed:
            return "FAIL", summary, "Live model report failed the quality gate"
        return "PASS", summary, None


def _run_campaign_baseline(campaign: BusinessDocumentQualityCampaign) -> tuple[str, str | None]:
    if not _campaign_source_matches(campaign):
        return "INCOMPLETE", "SOURCE_CHANGED"
    golden_test = ROOT / "test/evals/business_documents/test_golden_dialogue_harness.py"
    if not golden_test.is_file() or importlib.util.find_spec("pytest") is None:
        return "INCOMPLETE", "TEST_ASSETS_MISSING"
    with tempfile.TemporaryDirectory(prefix="business-document-baseline-") as directory:
        temp = Path(directory)
        env = {**os.environ, "BUSINESS_DOCUMENT_RELATED_FILE_SEARCH_ENABLED": "true", "BUSINESS_DOCUMENT_GOLDEN_REPORT": str(temp / "golden.json"), "PYTHONDONTWRITEBYTECODE": "1"}
        try:
            exit_code = _run_suite(str(golden_test), env, temp)
        except subprocess.TimeoutExpired:
            return "INCOMPLETE", "BASELINE_TIMEOUT"
        report_path = temp / "golden.json"
        if exit_code and report_path.is_file():
            try:
                summary = _golden_summary(json.loads(report_path.read_text(encoding="utf-8")))
                BusinessDocumentQualityCampaign.update(baseline_report=summary).where(BusinessDocumentQualityCampaign.id == campaign.id).execute()
            except (ValueError, OSError):
                pass
        if not _campaign_source_matches(campaign):
            return "INCOMPLETE", "SOURCE_CHANGED"
        return ("PASS", None) if exit_code == 0 else ("FAIL", "BASELINE_FAILED")


def _refresh_campaign(campaign_id: str) -> None:
    campaign = BusinessDocumentQualityCampaign.get_or_none(BusinessDocumentQualityCampaign.id == campaign_id)
    if campaign is None:
        return
    statuses = [item.status for item in BusinessDocumentQualityRun.select(BusinessDocumentQualityRun.status).where(BusinessDocumentQualityRun.campaign_id == campaign_id)]
    if any(status in {"PENDING", "RUNNING"} for status in statuses):
        status = "RUNNING"
        finished_at = None
    else:
        status = "COMPLETE" if statuses and all(item in {"PASS", "FAIL"} for item in statuses) and not campaign.reason_code else "PARTIAL"
        finished_at = current_timestamp()
    BusinessDocumentQualityCampaign.update(status=status, finished_at=finished_at).where(BusinessDocumentQualityCampaign.id == campaign_id).execute()


def _reason_code(status: str, reason: str | None) -> str | None:
    if status in {"PASS", "DIAGNOSTIC"}:
        return None
    if reason is None:
        return "QUALITY_GATE" if status == "FAIL" else "RUN_INCOMPLETE"
    for prefix, code in (
        ("Queued source revision", "SOURCE_CHANGED"),
        ("Dedicated quality tenant", "QA_TENANT_MISSING"),
        ("Quality test assets", "TEST_ASSETS_MISSING"),
        ("Quality test dependencies", "TEST_ASSETS_MISSING"),
        ("Nightly baseline", "BASELINE_FAILED"),
        ("Model digest changed", "MODEL_CHANGED"),
        ("Live model suite timed out", "MODEL_TIMEOUT"),
        ("Live model suite produced no report", "REPORT_MISSING"),
        ("Evaluation conditions changed", "CONDITIONS_CHANGED"),
        ("Evaluation source changed", "SOURCE_CHANGED"),
        ("Diagnostic case did not complete", "RUN_INCOMPLETE"),
        ("Deterministic golden suite failed", "BASELINE_FAILED"),
        ("Deterministic golden suite timed out", "BASELINE_TIMEOUT"),
    ):
        if reason.startswith(prefix):
            return code
    return "QUALITY_GATE" if status == "FAIL" else "RUN_INCOMPLETE"


def process_next_run() -> bool:
    stale_before = current_timestamp() - (2 * RUN_TIMEOUT_SECONDS + 5 * 60) * 1000
    stale = list(
        BusinessDocumentQualityRun.select(BusinessDocumentQualityRun.id, BusinessDocumentQualityRun.campaign_id).where(
            (BusinessDocumentQualityRun.status == "RUNNING") & (BusinessDocumentQualityRun.started_at < stale_before)
        )
    )
    if stale:
        BusinessDocumentQualityRun.update(
            status="INCOMPLETE", schedule_key=None, finished_at=current_timestamp(), reason="Quality worker stopped before completion", reason_code="WORKER_STOPPED"
        ).where(BusinessDocumentQualityRun.id.in_([item.id for item in stale])).execute()
        for campaign_id in {item.campaign_id for item in stale if item.campaign_id}:
            _refresh_campaign(campaign_id)
    stale_campaigns = list(
        BusinessDocumentQualityCampaign.select().where(
            (BusinessDocumentQualityCampaign.status == "RUNNING") & (BusinessDocumentQualityCampaign.baseline_status.is_null(True)) & (BusinessDocumentQualityCampaign.started_at < stale_before)
        )
    )
    for campaign in stale_campaigns:
        BusinessDocumentQualityCampaign.update(baseline_status="INCOMPLETE", reason_code="WORKER_STOPPED").where(BusinessDocumentQualityCampaign.id == campaign.id).execute()
        BusinessDocumentQualityRun.update(status="INCOMPLETE", reason_code="BASELINE_FAILED", reason="Nightly baseline did not pass", finished_at=current_timestamp()).where(
            (BusinessDocumentQualityRun.campaign_id == campaign.id) & (BusinessDocumentQualityRun.status == "PENDING")
        ).execute()
        _refresh_campaign(campaign.id)
    campaign = BusinessDocumentQualityCampaign.select().where(BusinessDocumentQualityCampaign.status == "PENDING").order_by(BusinessDocumentQualityCampaign.create_time).first()
    row = BusinessDocumentQualityRun.select().where(BusinessDocumentQualityRun.status == "PENDING").order_by(BusinessDocumentQualityRun.create_time, BusinessDocumentQualityRun.id).first()
    if campaign is not None and (row is None or campaign.create_time <= row.create_time):
        started = current_timestamp()
        claimed = (
            BusinessDocumentQualityCampaign.update(status="RUNNING", started_at=started)
            .where((BusinessDocumentQualityCampaign.id == campaign.id) & (BusinessDocumentQualityCampaign.status == "PENDING"))
            .execute()
        )
        if claimed != 1:
            return False
        try:
            baseline_status, code = _run_campaign_baseline(campaign)
        except Exception:
            logging.exception("Business Documents nightly baseline failed to run")
            baseline_status, code = "INCOMPLETE", "BASELINE_ERROR"
        BusinessDocumentQualityCampaign.update(baseline_status=baseline_status, reason_code=code or campaign.reason_code).where(BusinessDocumentQualityCampaign.id == campaign.id).execute()
        if baseline_status != "PASS":
            BusinessDocumentQualityRun.update(status="INCOMPLETE", reason_code="BASELINE_FAILED", reason="Nightly baseline did not pass", finished_at=current_timestamp()).where(
                (BusinessDocumentQualityRun.campaign_id == campaign.id) & (BusinessDocumentQualityRun.status == "PENDING")
            ).execute()
        _refresh_campaign(campaign.id)
        return True
    if row is None:
        return False
    if row.campaign_id:
        parent = BusinessDocumentQualityCampaign.get_or_none(BusinessDocumentQualityCampaign.id == row.campaign_id)
        if parent is None or parent.baseline_status != "PASS":
            return False
    started = current_timestamp()
    claimed = BusinessDocumentQualityRun.update(status="RUNNING", started_at=started).where((BusinessDocumentQualityRun.id == row.id) & (BusinessDocumentQualityRun.status == "PENDING")).execute()
    if claimed != 1:
        return False
    try:
        status, report, reason = _execute(row)
    except Exception:
        logging.exception("Business Documents quality run could not complete")
        status, report, reason = "INCOMPLETE", None, "Quality runner failed or timed out"
    BusinessDocumentQualityRun.update(
        status=status, schedule_key=None if row.trigger == "MANUAL" else row.schedule_key, report=report, reason=reason, reason_code=_reason_code(status, reason), finished_at=current_timestamp()
    ).where(BusinessDocumentQualityRun.id == row.id).execute()
    if row.campaign_id:
        _refresh_campaign(row.campaign_id)
    return True
