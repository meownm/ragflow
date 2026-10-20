"""Exact SQL aggregates over compact, anonymous alignment observations."""

from collections import Counter
from contextlib import nullcontext

from peewee import Case, SqliteDatabase, fn

from api.db.db_models import TechnicalSpecificationAlignmentObservation as Observation
from api.technical_specifications.alignment import CHECK_LEASE_SECONDS
from api.technical_specifications.telemetry import LIMIT_CODES, error_code
from common.time_utils import current_timestamp

DAY_MS = 86_400_000


def alignment_operations_dashboard(*, days=7):
    if type(days) is not int or days not in (1, 7, 30):
        raise ValueError("Invalid alignment observation window")
    now = current_timestamp()
    database = Observation._meta.database
    connection = database.connection_context() if database.is_closed() else nullcontext()
    # Keep counts and percentiles in the same committed snapshot.
    with connection, database.atomic():
        if not isinstance(database, SqliteDatabase):
            # The runtime PostgreSQL retrying adapter exposes begin() without
            # isolation arguments; set the level before the first data read.
            database.execute_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        window = (Observation.accepted_at_ms >= now - days * DAY_MS) & (Observation.accepted_at_ms <= now)
        predicates = {
            "processing": Observation.state == "PROCESSING",
            "finished": Observation.state == "FINISHED",
            "overdue_processing": (Observation.state == "PROCESSING") & (Observation.accepted_at_ms <= now - CHECK_LEASE_SECONDS * 1000),
            "incomplete": Observation.state == "INCOMPLETE",
            "execution_complete": Observation.execution_status == "COMPLETE",
            "reports_complete": Observation.report_complete.in_((True,)),
            "reports_incomplete": Observation.report_complete.in_((False,)),
            "limit_failures": Observation.error_code.in_(LIMIT_CODES),
            "cancelled": Observation.error_code == "EXECUTION_CANCELLED",
            "interruptions": Observation.error_code.in_(("EXECUTION_INTERRUPTED", "EXECUTION_LEASE_EXPIRED")),
            "expired": Observation.error_code == "EXECUTION_LEASE_EXPIRED",
            "usage_unknown": Observation.usage_known.in_((False,)),
            "reservation_unknown": Observation.reserved_tokens.is_null(True),
        }
        row = (
            Observation.select(
                fn.COUNT(Observation.id).alias("total"),
                *(fn.SUM(Case(None, [(predicate, 1)], 0)).alias(key) for key, predicate in predicates.items()),
                fn.SUM(Observation.spent_tokens).alias("spent"),
                fn.SUM(Observation.reserved_tokens).alias("reserved"),
                fn.COUNT(Observation.duration_ms).alias("duration_count"),
                fn.AVG(Observation.duration_ms).alias("duration_mean"),
                fn.COUNT(Observation.model_duration_ms).alias("model_count"),
                fn.AVG(Observation.model_duration_ms).alias("model_mean"),
            )
            .where(window)
            .dicts()
            .get()
        )
        counts = {key: int(row[key] or 0) for key in ("total", *predicates)}
        durations = {}
        for name, field, prefix in (("check", Observation.duration_ms, "duration"), ("model", Observation.model_duration_ms, "model")):
            count = int(row[prefix + "_count"])
            rank = (95 * count + 99) // 100
            percentile = Observation.select(field).where(window & field.is_null(False)).order_by(field).offset(rank - 1).limit(1).scalar() if count else None
            durations[name] = {"measured": count, "mean_ms": float(row[prefix + "_mean"]) if count else None, "p95_ms": percentile}
        codes = Counter()
        for item in Observation.select(Observation.error_code, fn.COUNT(Observation.id).alias("count")).where(window & Observation.error_code.is_null(False)).group_by(Observation.error_code).dicts():
            codes[error_code(item["error_code"]) or "UNKNOWN"] += item["count"]
        earliest = Observation.select(fn.MIN(Observation.observed_at_ms)).scalar()
    terminal = counts["finished"] + counts["incomplete"]
    return {
        "document_type": "technical_specification",
        "scope": "RECORDED_ALIGNMENT_ATTEMPTS",
        "days": days,
        "updated_at": now,
        "earliest_observation_at": earliest,
        "counts": counts,
        "terminal_attempts": terminal,
        "execution_completion_rate": counts["execution_complete"] / terminal if terminal else None,
        "report_completion_rate": counts["reports_complete"] / terminal if terminal else None,
        "total_tokens": int(row["spent"] or 0) if counts["total"] and not counts["usage_unknown"] else None,
        "known_spent_tokens": int(row["spent"] or 0),
        "reserved_tokens": int(row["reserved"] or 0) if counts["total"] and not counts["reservation_unknown"] else None,
        "known_reserved_tokens": int(row["reserved"] or 0),
        "durations": durations,
        "reasons": [{"code": code, "count": count} for code, count in sorted(codes.items(), key=lambda pair: (-pair[1], pair[0]))],
    }
