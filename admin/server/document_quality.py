"""Administrative read model for every durable Business Documents job."""

from collections import Counter
from math import ceil, isfinite
import re
from typing import Any

from peewee import fn

from api.db.db_models import BusinessDocumentJob
from common.time_utils import current_timestamp


MAX_JOBS = 5000
MILLISECONDS_PER_DAY = 24 * 60 * 60 * 1000
TERMINAL = ("COMPLETED", "DEAD")
ACTIVE = ("PENDING", "RETRY", "RUNNING")
SAFE_ERROR_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
DOCUMENT_AI = frozenset(("ASSESS_INTAKE", "GENERATE_DRAFT", "ASSESS_REVIEW", "PLAN_CHANGES", "GENERATE_EVA_CHANGE"))


def _category(job_type: str) -> str:
    if job_type in DOCUMENT_AI:
        return "DOCUMENT_AI"
    if job_type.startswith("SQL_AGENT_"):
        return "SQL_AGENT"
    if job_type == "GENERATE_EXPORT":
        return "EXPORT"
    return "OTHER"


def _error_code(error: Any) -> str:
    code = error.get("code") if isinstance(error, dict) else None
    return code if isinstance(code, str) and SAFE_ERROR_CODE.fullmatch(code) else "UNKNOWN"


def _percentile95(values: list[float]) -> float | None:
    if not values:
        return None
    values.sort()
    return values[ceil(len(values) * 0.95) - 1]


def summarize_jobs(
    rows: list[dict[str, Any]],
    *,
    days: int,
    truncated: bool,
    counts: dict[str, int] | None = None,
    tasks: list[dict] | None = None,
    failed_documents: int | None = None,
    terminal_documents: int | None = None,
    affected_tenants: int | None = None,
) -> dict[str, Any]:
    """Summarize a bounded detail sample, keeping exact database counts separate."""
    if counts is None:
        counts = dict(Counter(row["status"] for row in rows))
    if tasks is None:
        by_task: dict[str, Counter[str]] = {}
        for row in rows:
            by_task.setdefault(row["job_type"], Counter())[row["status"]] += 1
        tasks = [{"task_type": name, "category": _category(name), **{key.lower(): values[key] for key in (*TERMINAL, *ACTIVE)}} for name, values in sorted(by_task.items())]
    terminal = counts.get("COMPLETED", 0) + counts.get("DEAD", 0)
    durations: list[float] = []
    model_durations: list[float] = []
    models: Counter[tuple[str, str]] = Counter()
    errors: Counter[tuple[str, str]] = Counter()
    total_tokens = measured_tokens = 0
    for row in rows:
        if row["status"] == "DEAD":
            errors[(row["job_type"], _error_code(row.get("error")))] += 1
        if row["status"] not in TERMINAL:
            continue
        started, finished = row.get("create_time"), row.get("update_time")
        if isinstance(started, (int, float)) and isinstance(finished, (int, float)) and not isinstance(started, bool) and not isinstance(finished, bool) and 0 <= started <= finished:
            durations.append(float(finished - started))
        if row["status"] != "COMPLETED":
            continue
        result = row.get("result")
        execution = result.get("execution") if isinstance(result, dict) else None
        ai = execution.get("ai") if isinstance(execution, dict) else None
        if not isinstance(ai, dict):
            continue
        duration = ai.get("duration_ms")
        if isinstance(duration, (int, float)) and not isinstance(duration, bool) and isfinite(duration) and duration >= 0:
            model_durations.append(float(duration))
        provider, model = ai.get("provider"), ai.get("model")
        if isinstance(provider, str) and isinstance(model, str) and provider and model:
            models[(provider, model)] += 1
        usage = ai.get("token_usage")
        tokens = usage.get("total_tokens") if isinstance(usage, dict) else None
        if isinstance(tokens, int) and not isinstance(tokens, bool) and tokens >= 0:
            total_tokens += tokens
            measured_tokens += 1
    failed_count = counts.get("DEAD", 0)
    return {
        "days": days,
        "sampled_jobs": len(rows),
        "sampled_completed_jobs": sum(row["status"] == "COMPLETED" for row in rows),
        "truncated": truncated,
        "terminal_jobs": terminal,
        "completed": counts.get("COMPLETED", 0),
        "failed": failed_count,
        "pending": counts.get("PENDING", 0),
        "retrying": counts.get("RETRY", 0),
        "running": counts.get("RUNNING", 0),
        "failure_rate": failed_count / terminal if terminal else None,
        "failed_documents": failed_documents if failed_documents is not None else len({row["document_id"] for row in rows if row["status"] == "DEAD"}),
        "terminal_documents": terminal_documents if terminal_documents is not None else len({row["document_id"] for row in rows if row["status"] in TERMINAL}),
        "affected_tenants": affected_tenants if affected_tenants is not None else len({row.get("tenant_id") for row in rows if row["status"] == "DEAD"}),
        "latency_p95_ms": _percentile95(durations),
        "measured_latency_jobs": len(durations),
        "model_latency_p95_ms": _percentile95(model_durations),
        "measured_model_latency_jobs": len(model_durations),
        "total_tokens": total_tokens,
        "measured_token_jobs": measured_tokens,
        "tasks": tasks,
        "models": [{"provider": provider, "model": model, "count": count} for (provider, model), count in sorted(models.items(), key=lambda item: (-item[1], item[0]))],
        "errors": [{"task_type": task, "error_code": code, "count": count} for (task, code), count in sorted(errors.items(), key=lambda item: (-item[1], item[0]))],
    }


def document_quality_dashboard(*, days: int = 7) -> dict[str, Any]:
    if days not in (1, 7, 30):
        raise ValueError("days must be 1, 7, or 30")
    now = current_timestamp()
    since = now - days * MILLISECONDS_PER_DAY
    window = (BusinessDocumentJob.update_time >= since) & (BusinessDocumentJob.update_time <= now)
    aggregate = list(
        BusinessDocumentJob.select(BusinessDocumentJob.job_type, BusinessDocumentJob.status, fn.COUNT(BusinessDocumentJob.id).alias("count"))
        .where(window)
        .group_by(BusinessDocumentJob.job_type, BusinessDocumentJob.status)
        .dicts()
    )
    counts: Counter[str] = Counter()
    by_task: dict[str, Counter[str]] = {}
    for item in aggregate:
        counts[item["status"]] += item["count"]
        by_task.setdefault(item["job_type"], Counter())[item["status"]] = item["count"]
    tasks = [{"task_type": name, "category": _category(name), **{key.lower(): values[key] for key in (*TERMINAL, *ACTIVE)}} for name, values in sorted(by_task.items())]
    rows = list(
        BusinessDocumentJob.select(
            BusinessDocumentJob.id,
            BusinessDocumentJob.document_id,
            BusinessDocumentJob.tenant_id,
            BusinessDocumentJob.job_type,
            BusinessDocumentJob.status,
            BusinessDocumentJob.result,
            BusinessDocumentJob.error,
            BusinessDocumentJob.attempt,
            BusinessDocumentJob.max_attempts,
            BusinessDocumentJob.create_time,
            BusinessDocumentJob.update_time,
        )
        .where(window & BusinessDocumentJob.status.in_(TERMINAL))
        .order_by(BusinessDocumentJob.update_time.desc(), BusinessDocumentJob.id.desc())
        .limit(MAX_JOBS + 1)
        .dicts()
    )
    terminal_docs = BusinessDocumentJob.select(fn.COUNT(fn.DISTINCT(BusinessDocumentJob.document_id))).where(window & BusinessDocumentJob.status.in_(TERMINAL)).scalar() or 0
    failed = (
        BusinessDocumentJob.select(
            fn.COUNT(fn.DISTINCT(BusinessDocumentJob.document_id)).alias("documents"),
            fn.COUNT(fn.DISTINCT(BusinessDocumentJob.tenant_id)).alias("tenants"),
        )
        .where(window & (BusinessDocumentJob.status == "DEAD"))
        .dicts()
        .first()
        or {}
    )
    return {
        "updated_at": now,
        **summarize_jobs(
            rows[:MAX_JOBS],
            days=days,
            truncated=len(rows) > MAX_JOBS,
            counts=counts,
            tasks=tasks,
            terminal_documents=terminal_docs,
            failed_documents=failed.get("documents") or 0,
            affected_tenants=failed.get("tenants") or 0,
        ),
    }


def failed_jobs_page(*, days: int = 7, category: str = "", task_type: str = "", error_code: str = "", offset: int = 0, limit: int = 50) -> dict[str, Any]:
    if days not in (1, 7, 30) or category not in ("", "DOCUMENT_AI", "SQL_AGENT", "EXPORT", "OTHER"):
        raise ValueError("Invalid job filter")
    if task_type and (len(task_type) > 64 or not SAFE_ERROR_CODE.fullmatch(task_type)):
        raise ValueError("Invalid job type")
    if error_code and not SAFE_ERROR_CODE.fullmatch(error_code):
        raise ValueError("Invalid error code")
    if offset < 0 or limit != 50:
        raise ValueError("Invalid job page")
    now = current_timestamp()
    query = BusinessDocumentJob.select(
        BusinessDocumentJob.id,
        BusinessDocumentJob.document_id,
        BusinessDocumentJob.job_type,
        BusinessDocumentJob.error,
        BusinessDocumentJob.attempt,
        BusinessDocumentJob.max_attempts,
        BusinessDocumentJob.update_time,
    ).where((BusinessDocumentJob.status == "DEAD") & (BusinessDocumentJob.update_time >= now - days * MILLISECONDS_PER_DAY) & (BusinessDocumentJob.update_time <= now))
    if task_type:
        query = query.where(BusinessDocumentJob.job_type == task_type)
    elif category == "DOCUMENT_AI":
        query = query.where(BusinessDocumentJob.job_type.in_(DOCUMENT_AI))
    elif category == "SQL_AGENT":
        query = query.where(BusinessDocumentJob.job_type.startswith("SQL_AGENT_"))
    elif category == "EXPORT":
        query = query.where(BusinessDocumentJob.job_type == "GENERATE_EXPORT")
    elif category == "OTHER":
        query = query.where(~(BusinessDocumentJob.job_type.in_(DOCUMENT_AI) | BusinessDocumentJob.job_type.startswith("SQL_AGENT_") | (BusinessDocumentJob.job_type == "GENERATE_EXPORT")))
    count = 0
    codes = set()
    jobs = []
    for row in query.order_by(BusinessDocumentJob.update_time.desc(), BusinessDocumentJob.id.desc()).dicts().iterator():
        if category and _category(row["job_type"]) != category:
            continue
        code = _error_code(row["error"])
        codes.add(code)
        if error_code and code != error_code:
            continue
        count += 1
        if offset < count <= offset + limit:
            jobs.append(
                {
                    "id": row["id"],
                    "document_id": row["document_id"],
                    "task_type": row["job_type"],
                    "category": _category(row["job_type"]),
                    "status": "DEAD",
                    "finished_at": row["update_time"],
                    "attempt": row["attempt"],
                    "max_attempts": row["max_attempts"],
                    "error_code": code,
                }
            )
    return {"days": days, "updated_at": now, "total": count, "offset": offset, "limit": limit, "error_codes": sorted(codes), "jobs": jobs}
