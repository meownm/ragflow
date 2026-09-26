"""Exact browser UTF-16 selections against an immutable rendered section."""

from business_documents.domain.errors import RuleViolation


def _slice(encoded: bytes, start: int, end: int) -> str:
    try:
        return encoded[start * 2 : end * 2].decode("utf-16-le")
    except UnicodeDecodeError as error:
        raise RuleViolation("INVALID_COMMENT_ANCHOR", "Anchor offsets split a Unicode character") from error


def _context(encoded: bytes, start: int, end: int, *, trim_start: bool) -> str:
    try:
        return _slice(encoded, start, end)
    except RuleViolation:
        # Only the outer context edge may shrink across a surrogate pair.
        return _slice(encoded, start + 1, end) if trim_start else _slice(encoded, start, end - 1)


def validate_comment_anchor(anchor: object, revision_id: str, section_id: str | None, section_text: str) -> None:
    if anchor is not None and not isinstance(anchor, dict):
        raise RuleViolation("INVALID_COMMENT_ANCHOR", "anchor must be a JSON object")
    if not anchor:
        return
    if anchor.get("revision_id") != revision_id:
        raise RuleViolation("INVALID_COMMENT_ANCHOR", "Anchor revision_id must match the target revision")
    if anchor.get("section_id") != section_id or section_id is None:
        raise RuleViolation("INVALID_COMMENT_ANCHOR", "Anchor section_id must match the comment section")
    selected_text = anchor.get("selected_text")
    if not isinstance(selected_text, str) or not selected_text:
        raise RuleViolation("INVALID_COMMENT_ANCHOR", "selected_text must be non-empty")
    start, end = anchor.get("start_offset"), anchor.get("end_offset")
    encoded = section_text.encode("utf-16-le")
    length = len(encoded) // 2
    if (
        isinstance(start, bool)
        or isinstance(end, bool)
        or not isinstance(start, int)
        or not isinstance(end, int)
        or start < 0
        or end <= start
        or end > length
        or _slice(encoded, start, end) != selected_text
    ):
        raise RuleViolation("INVALID_COMMENT_ANCHOR", "Anchor offsets must exactly select text inside the target section")
    prefix = _context(encoded, max(0, start - 64), start, trim_start=True)
    suffix = _context(encoded, end, min(length, end + 64), trim_start=False)
    if anchor.get("prefix") != prefix or anchor.get("suffix") != suffix:
        raise RuleViolation("INVALID_COMMENT_ANCHOR", "Anchor context does not match the target section")
