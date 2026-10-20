"""Value-only BT/TZ evidence, complete inventories and bounded verdict validation."""

import json
import re
from datetime import UTC, datetime
from hashlib import sha256

from .domain import fail, sections

MAX_REQUIREMENTS = 128
MAX_REPORT_BYTES = 1_000_000
CHECK_LEASE_SECONDS = 360
COVERAGE = {"IMPLEMENTED", "PARTIAL", "MISSING", "UNDETERMINED"}
CONSISTENCY = {"CONSISTENT", "CONTRADICTED", "UNDETERMINED"}


def fingerprint(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def strict_json(raw):
    """Reject ambiguous objects before a normal JSON decoder can discard keys."""
    try:
        size = len(raw.encode()) if isinstance(raw, str) else MAX_REPORT_BYTES + 1
    except UnicodeError:
        fail("ALIGNMENT_REPORT_INVALID", "Некорректная кодировка отчёта", 502)
    if size > MAX_REPORT_BYTES:
        fail("ALIGNMENT_REPORT_INVALID", "Отчёт отсутствует или превышает допустимый размер", 502)

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result

    def constant(_value):
        raise ValueError("Non-finite JSON number")

    try:
        value = json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, RecursionError):
        fail("ALIGNMENT_REPORT_INVALID", "Помощник вернул неоднозначный или некорректный JSON", 502)
    if not isinstance(value, dict):
        fail("ALIGNMENT_REPORT_INVALID", "Нужен объект отчёта", 502)
    return value


def source_snapshot(rows):
    """Inventory every paragraph, including headings; the model cannot choose inputs.

    These are source fragments, not a claim that extraction has proved atomic
    requirement semantics. The verifier must check every clause in each fragment.
    """
    if not isinstance(rows, list) or not rows or len(rows) > 16:
        fail("ALIGNMENT_SOURCE_MISSING", "Выберите от одного до шестнадцати согласованных БТ", 409)
    if any(not isinstance(row, dict) or not isinstance(row.get("document_id"), str) or not row["document_id"] for row in rows):
        fail("ALIGNMENT_SOURCE_INVALID", "Неполный снимок согласованных БТ", 409)
    sources, inventory = [], []
    for row in sorted(rows, key=lambda item: item["document_id"]):
        required = ("document_id", "revision_id", "agreement_event_id", "content_hash", "body_markdown")
        if any(not isinstance(row.get(key), str) or not row[key] for key in required):
            fail("ALIGNMENT_SOURCE_INVALID", "Неполный снимок согласованных БТ", 409)
        if any(source["document_id"] == row["document_id"] for source in sources):
            fail("ALIGNMENT_SOURCE_INVALID", "Повторное основание", 409)
        body = row["body_markdown"]
        if len(body) > 100_000 or sum(len(item["body_markdown"]) for item in sources) + len(body) > 200_000:
            fail("ALIGNMENT_SOURCE_TOO_LARGE", "Основания превышают лимит; усечение запрещено", 413)
        source = {key: row[key] for key in required}
        source["body_sha256"] = sha256(body.encode()).hexdigest()
        sources.append(source)
        # Even headings can contain normative clauses; preserve them. Fragments
        # reference exact source spans and the full body is also sent for context.
        for match in re.finditer(r"\S[\s\S]*?(?=\n\s*\n|\Z)", body):
            quote = match.group().rstrip()
            if not quote:
                continue
            inventory.append({"id": f"R{len(inventory) + 1:03}", "source_id": row["document_id"], "start": match.start(), "end": match.start() + len(quote), "quote": quote})
    if not inventory or len(inventory) > MAX_REQUIREMENTS:
        fail("ALIGNMENT_INVENTORY_INVALID", "Пустой или слишком большой перечень оснований; усечение запрещено", 413)
    value = {"sources": sources, "inventory": inventory}
    return {**value, "sha256": fingerprint(value)}


def candidate_identity(document):
    return fingerprint({"id": document["id"], "revision": document["revision"], "sections": sections(document["sections"])})


def report_input(document, basis):
    return {"candidate_sha256": candidate_identity(document), "basis_sha256": basis["sha256"], "sections": sections(document["sections"]), "basis": basis}


def _invalid(message="Неполный или недоказанный отчёт соответствия"):
    fail("ALIGNMENT_REPORT_INVALID", message, 502)


def _reason(value):
    if not isinstance(value, str) or _size(value) > 512:
        _invalid()


def _size(value):
    try:
        return len(value.encode())
    except UnicodeError:
        _invalid("Некорректная кодировка отчёта")


def _references(refs, document, *, required=False):
    if not isinstance(refs, list) or len(refs) > 9 or (required and not refs):
        _invalid()
    for ref in refs:
        if not isinstance(ref, dict) or set(ref) != {"section", "quote"}:
            _invalid()
        section, quote = ref["section"], ref["quote"]
        if not isinstance(section, str) or section not in document["sections"] or not isinstance(quote, str) or not quote or _size(quote) > 512 or quote not in document["sections"][section]:
            _invalid("Цитата отчёта не принадлежит проверяемому ТЗ")


def validate_report(value, document, basis):
    expected = {"candidate_sha256", "basis_sha256", "requirements", "additional_rules", "joint"}
    if not isinstance(value, dict) or set(value) != expected:
        _invalid()
    try:
        size = _size(json.dumps(value, ensure_ascii=False))
    except (TypeError, ValueError, RecursionError):
        _invalid()
    if size > MAX_REPORT_BYTES:
        _invalid()
    if value["candidate_sha256"] != candidate_identity(document) or value["basis_sha256"] != basis["sha256"]:
        _invalid("Отчёт относится к другому ТЗ или основанию")
    rows = value["requirements"]
    identifiers = [item["id"] for item in basis["inventory"]]
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows) or [row.get("id") for row in rows] != identifiers:
        _invalid("Перечень результатов не совпадает с полным перечнем оснований")
    for row in rows:
        if set(row) != {"id", "coverage", "consistency", "references", "reason"}:
            _invalid()
        if not isinstance(row["coverage"], str) or row["coverage"] not in COVERAGE or not isinstance(row["consistency"], str) or row["consistency"] not in CONSISTENCY:
            _invalid()
        _reason(row["reason"])
        _references(row["references"], document, required=row["coverage"] == "IMPLEMENTED" or row["consistency"] == "CONTRADICTED")
        if (row["coverage"] != "IMPLEMENTED" or row["consistency"] != "CONSISTENT") and not row["reason"].strip():
            _invalid()
    additions = value["additional_rules"]
    if not isinstance(additions, list) or len(additions) > MAX_REQUIREMENTS:
        _invalid()
    for row in additions:
        if (
            not isinstance(row, dict)
            or set(row) != {"kind", "references", "reason"}
            or not isinstance(row["kind"], str)
            or row["kind"] not in ("TECHNICAL_DETAIL", "UNSUPPORTED_BUSINESS_RULE", "UNDETERMINED")
        ):
            _invalid()
        _references(row["references"], document, required=True)
        _reason(row["reason"])
        if not row["reason"].strip():
            _invalid()
    joint = value["joint"]
    if (
        not isinstance(joint, dict)
        or set(joint) != {"checked_ids", "consistency", "reason"}
        or joint["checked_ids"] != identifiers
        or not isinstance(joint["consistency"], str)
        or joint["consistency"] not in ("CONSISTENT", "CONTRADICTED", "UNDETERMINED")
    ):
        _invalid()
    _reason(joint["reason"])
    if joint["consistency"] != "CONSISTENT" and not joint["reason"].strip():
        _invalid()
    failures = sum(row["coverage"] != "IMPLEMENTED" or row["consistency"] != "CONSISTENT" for row in rows)
    failures += sum(row["kind"] != "TECHNICAL_DETAIL" for row in additions) + int(joint["consistency"] != "CONSISTENT")
    return {
        "execution_status": "COMPLETE",
        "report_status": "COMPLETE",
        "quality_status": "FAIL" if failures else "PASS",
        "qualifies": not failures,
        "metrics": {
            "expected_fragments": len(identifiers),
            "returned_fragments": len(rows),
            "implemented": sum(row["coverage"] == "IMPLEMENTED" for row in rows),
            "contradictions": sum(row["consistency"] == "CONTRADICTED" for row in rows),
            "unsupported_rules": sum(row["kind"] == "UNSUPPORTED_BUSINESS_RULE" for row in additions),
            "joint_consistency": joint["consistency"],
        },
        "report": value,
    }


def failure_result(code, *, completed=False):
    return {"execution_status": "COMPLETE" if completed else "INCOMPLETE", "report_status": "INCOMPLETE", "quality_status": "FAIL" if completed else "UNKNOWN", "qualifies": False, "reason_code": code}


def expire_checks(checks, now):
    """End abandoned attempts without retrying paid inference or erasing its debt."""
    changed = False
    for check in checks:
        if check.get("status") != "PROCESSING":
            continue
        try:
            created = datetime.fromisoformat(check["created_at"])
            if created.tzinfo is None:
                raise ValueError("Missing timezone")
            expired = (now - created).total_seconds() >= CHECK_LEASE_SECONDS
        except (ValueError, TypeError, KeyError):
            expired = True
        if expired:
            check.update(
                status="INCOMPLETE", reason_code="EXECUTION_LEASE_EXPIRED", finished_at=now.astimezone(UTC).isoformat(), backend_usage_known=False, result=failure_result("EXECUTION_LEASE_EXPIRED")
            )
            changed = True
    return changed
