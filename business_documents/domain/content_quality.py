"""Pure domain checks for generated business-document structure."""

from __future__ import annotations

import re
from typing import Iterable, Sequence


_WORD = re.compile(r"[A-Za-zА-Яа-яЁё0-9_]+")
_SEMANTIC_STOP_WORDS = frozenset(
    {
        "а",
        "без",
        "в",
        "для",
        "и",
        "из",
        "к",
        "как",
        "на",
        "не",
        "по",
        "после",
        "при",
        "раздел",
        "с",
        "содержание",
        "также",
        "то",
        "что",
        "paragraph",
    }
)
_SEMANTIC_ROOTS = {
    "actor_client": ("клиент", "пользоват", "заказчик"),
    "actor_system": ("систем", "сервис", "прилож"),
    "booking": ("запис", "брониров", "бронь"),
    "choose": ("выбир", "выбра", "выбор"),
    "confirm": ("подтверж",),
    "failure": ("ошиб", "недоступ", "отказ", "таймаут"),
    "retry": ("повтор",),
    "slot": ("слот", "интервал"),
}


def semantic_duplicate_section_pairs(
    sections: Sequence[object],
    *,
    section_pairs: Iterable[tuple[str, str]] | None = None,
) -> tuple[tuple[str, str], ...]:
    """Return cross-section pairs whose text blocks express nearly the same content."""

    configured_pairs = tuple(section_pairs) if section_pairs is not None else None
    allowed = {frozenset(pair): pair for pair in configured_pairs or ()}
    meaningful_blocks: list[tuple[str, frozenset[str]]] = []
    for section in sections:
        if not isinstance(section, dict) or not isinstance(section.get("id"), str):
            continue
        blocks = section.get("blocks", [])
        if not isinstance(blocks, list):
            continue
        for block in blocks:
            signature = _semantic_signature(" ".join(_semantic_content_strings(block)))
            if len(signature) >= 5:
                meaningful_blocks.append((section["id"], signature))

    duplicates: list[tuple[str, str]] = []
    for index, (section_id, signature) in enumerate(meaningful_blocks):
        for prior_section_id, prior_signature in meaningful_blocks[:index]:
            if section_id == prior_section_id:
                continue
            pair_key = frozenset((prior_section_id, section_id))
            if configured_pairs is not None and pair_key not in allowed:
                continue
            overlap = len(signature & prior_signature)
            if overlap < 5 or overlap / min(len(signature), len(prior_signature)) < 0.75:
                continue
            pair = allowed.get(pair_key, (prior_section_id, section_id))
            if pair not in duplicates:
                duplicates.append(pair)
            break
    return tuple(duplicates)


def meaningful_text_block_count(sections: Sequence[object]) -> int:
    """Count text blocks large enough to participate in semantic comparison."""

    count = 0
    for section in sections:
        if not isinstance(section, dict):
            continue
        blocks = section.get("blocks", [])
        if not isinstance(blocks, list):
            continue
        count += sum(len(_semantic_signature(" ".join(_semantic_content_strings(block)))) >= 5 for block in blocks)
    return count


def parent_child_section_pairs(sections: Sequence[object]) -> tuple[tuple[str, str], ...]:
    """Derive structural parent-child pairs from numbered section identifiers."""

    section_ids = {section.get("id") for section in sections if isinstance(section, dict) and isinstance(section.get("id"), str)}
    return tuple((section_id.rsplit(".", 1)[0], section_id) for section_id in sorted(section_ids) if "." in section_id and section_id.rsplit(".", 1)[0] in section_ids)


def _semantic_signature(value: str) -> frozenset[str]:
    tokens: set[str] = set()
    for raw_token in _WORD.findall(value.casefold()):
        if raw_token in _SEMANTIC_STOP_WORDS or len(raw_token) < 3:
            continue
        canonical = next(
            (name for name, roots in _SEMANTIC_ROOTS.items() if any(raw_token.startswith(root) for root in roots)),
            raw_token[:7] if len(raw_token) > 8 else raw_token,
        )
        tokens.add(canonical)
    return frozenset(tokens)


def _semantic_content_strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        return [text for item in value for text in _semantic_content_strings(item)]
    if isinstance(value, dict):
        return [
            text
            for key, item in value.items()
            if key not in {"evidence_refs", "source", "type", "url"}
            for text in _semantic_content_strings(item)
        ]
    return []
