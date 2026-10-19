"""Value-only rules for document revisions, dependencies and proposed changes."""

from __future__ import annotations

from hashlib import sha256
from collections import Counter
import json
import re
from uuid import uuid4

from .errors import SourceWorkspaceError


DOCUMENT_TYPES = {"business_requirements", "technical_specification", "architecture", "database", "api", "instruction", "tests", "regulation", "reference", "other"}
CHANGE_POLICIES = {"working", "approval", "locked"}
RELATIONS = {"requires", "constrains", "details", "reflects", "verifies", "reference"}
OUTCOMES = {"changes_required", "no_impact", "conflict", "insufficient_data"}
PLAN_POLICY = "linked-documents-v1"
MAX_DEPENDENCIES = 100
WORKSPACE_CAPABILITIES = {"read": {"owner", "editor", "reviewer", "reader"}, "edit": {"owner", "editor"}, "approve": {"owner", "reviewer"}, "manage": {"owner"}}
_HEADING = re.compile(r"^ {0,3}(#{1,6})\s+(.+?)\s*#*\s*$")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")


def fail(code: str, message: str, status: int = 422):
    raise SourceWorkspaceError(code, message, status)


def identifier() -> str:
    return uuid4().hex


def text(value, name: str, limit: int = 100_000, *, empty: bool = False) -> str:
    if not isinstance(value, str) or len(value) > limit or (not empty and not value.strip()):
        fail("INVALID_INPUT", f"Некорректное поле «{name}»")
    return value


def choice(value, allowed: set[str], name: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        fail("INVALID_INPUT", f"Некорректное поле «{name}»")
    return value


def expected(value) -> int:
    if type(value) is not int or value < 1:
        fail("VERSION_CONFLICT", "Укажите текущую версию документа", 409)
    return value


def digest(value) -> str:
    serialized = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(serialized.encode()).hexdigest()


def render(sections: list[dict]) -> str:
    return "".join(section["text"] for section in sections)


def heading(section: dict) -> str:
    first = section["text"].splitlines()
    match = _HEADING.match(first[0]) if first else None
    return match.group(2) if match else "Введение"


def _section_keys(sections):
    parents = []
    keys = []
    for section in sections:
        lines = section["text"].splitlines()
        match = _HEADING.match(lines[0]) if lines else None
        if match:
            level = len(match.group(1))
            while parents and parents[-1][0] >= level:
                parents.pop()
            parents.append((level, match.group(2)))
            keys.append(tuple(parents))
        else:
            keys.append(())
    return keys


def split_sections(markdown: str, previous: list[dict] | None = None, *, maximum: int = 100_000) -> list[dict]:
    """Keep every character; ambiguous repeated headings receive new identities."""
    text(markdown, "Содержание", maximum, empty=True)
    parts: list[str] = []
    current: list[str] = []
    fence = ""
    for line in markdown.splitlines(keepends=True):
        marker = _FENCE.match(line)
        if marker:
            token = marker.group(1)
            if not fence:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence) and not line[marker.end() :].strip():
                fence = ""
        if not fence and _HEADING.match(line) and current:
            parts.append("".join(current))
            current = []
        current.append(line)
    if current or not parts:
        parts.append("".join(current))
    old_by_key = {}
    old_by_text = {}
    for section, key in zip(previous or [], _section_keys(previous or []), strict=True):
        old_by_key.setdefault(key, []).append(section)
        old_by_text.setdefault((key, section["text"]), []).append(section)
    keys = _section_keys([{"text": part} for part in parts])
    counts, text_counts = Counter(keys), Counter(zip(keys, parts, strict=True))
    result = []
    for part, key in zip(parts, keys, strict=True):
        matches = old_by_key.get(key, [])
        exact = old_by_text.get((key, part), [])
        old = matches[0] if len(matches) == 1 and counts[key] == 1 else exact[0] if len(exact) == 1 and text_counts[(key, part)] == 1 else None
        result.append({"id": old["id"] if old else identifier(), "text": part})
    return result


def validate_sections(value) -> list[dict]:
    if not isinstance(value, list) or not value or len(value) > 500:
        fail("INVALID_SECTIONS", "Документ должен содержать от 1 до 500 разделов")
    seen = set()
    for section in value:
        if not isinstance(section, dict) or set(section) != {"id", "text"}:
            fail("INVALID_SECTIONS", "Некорректный раздел документа")
        key = text(section["id"], "Идентификатор раздела", 32)
        if key in seen:
            fail("INVALID_SECTIONS", "Идентификаторы разделов должны быть уникальны")
        seen.add(key)
        text(section["text"], "Текст раздела", empty=True)
    text(render(value), "Содержание", empty=True)
    return value


def validate_scope(value, sections: list[dict]) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value) or len(set(value)) != len(value):
        fail("INVALID_SCOPE", "Область связи должна содержать уникальные разделы")
    if set(value) - {section["id"] for section in sections}:
        fail("STALE_SCOPE", "Один из разделов связи больше не существует", 409)
    return value


def input_manifest(inputs: list[dict]) -> list[dict]:
    """Revision IDs identify immutable snapshots; proposals need no second text copy."""
    return [{key: value for key, value in item.items() if key not in {"sections", "previous_sections"}} for item in inputs]


def assert_version(document: dict, version: int, *, code="VERSION_CONFLICT"):
    if document["version"] != version:
        fail(code, "Документ или его настройки изменились; обновите данные", 409)


def assert_editable(document: dict):
    if document["source_kind"] != "local" or document["change_policy"] == "locked":
        fail("DOCUMENT_LOCKED", "Документ зафиксирован или редактируется у владельца источника", 409)


def assert_approver(actor: str, document: dict):
    if actor != (document.get("approver_id") or document["owner_id"]):
        fail("PERMISSION_DENIED", "Решение принимает назначенный согласующий", 403)


def manual_plan(before: list[dict], after: list[dict]) -> dict:
    previous = {section["id"]: section for section in before}
    current = {section["id"]: section for section in after}
    operations = [{"op": "delete", "section_id": section["id"], "source_ids": []} for section in before if section["id"] not in current]
    anchor = None
    for section in after:
        old = previous.get(section["id"])
        if old is None or old["text"] != section["text"]:
            operations.append({"op": "replace" if old else "add", "section_id": section["id"], "after_id": anchor, "text": section["text"], "source_ids": []})
        anchor = section["id"]
    return {
        "outcome": "changes_required",
        "summary": "Ручная правка ожидает решения назначенного согласующего.",
        "operations": operations,
        "source_results": [],
        "candidate_hash": digest(render(after)),
    }


def manual_candidate(change: dict) -> list[dict]:
    sections = validate_sections(change["candidate_sections"])
    if digest(render(sections)) != change["plan"].get("candidate_hash") or change["verification"].get("verdict") != "HUMAN_REQUIRED":
        fail("INVALID_MANUAL_CHANGE", "Сохранённый текст ручной правки не соответствует предложению", 409)
    return sections


def source_state(source, dependency):
    if not source:
        return None, ""
    if dependency["refresh_on"] == "implemented":
        return source.get("implemented_revision_id"), source.get("implementation_event_id") or ""
    revision = source.get("approved_revision_id") if dependency["refresh_on"] == "approved" and source.get("source_kind") != "business" else source.get("current_revision_id")
    reference = source.get("source_ref") or {}
    return revision, reference.get("agreement_event_id", "") if source.get("source_kind") == "business" else ""


def input_reviewed(dependency, revision_id, target_id, token=""):
    return dependency["reviewed_revision_id"] == revision_id and dependency["reviewed_target_revision_id"] == target_id and (dependency.get("reviewed_source_state_token") or "") == token


def assert_current_inputs(inputs: list[dict], sources: dict[str, dict], dependencies: dict[str, dict]):
    for item in inputs:
        source, dependency = sources.get(item["source_id"]), dependencies.get(item["dependency_id"])
        revision_id, token = source_state(source, item["policy"])
        if (
            not dependency
            or dependency["version"] != item["policy"]["version"]
            or dependency["source_id"] != item["source_id"]
            or revision_id != item["source_revision_id"]
            or token != item.get("source_state_token", "")
        ):
            fail("SOURCE_CHANGED", "Основания изменились; подготовьте правки заново", 409)


def confirmation_inputs(actor: str, document: dict, change: dict, sources: dict[str, dict], dependencies: dict[str, dict], action: str) -> list[dict]:
    assert_approver(actor, document)
    if change["status"] != "ready":
        fail("CHANGE_NOT_READY", "Предложение уже обработано или не прошло проверку", 409)
    assert_version(document, change["document_version"], code="STALE_CHANGE")
    if document["current_revision_id"] != change["base_revision_id"] or change["policy_version"] != PLAN_POLICY:
        fail("STALE_CHANGE", "Базовая редакция или правила подготовки изменились", 409)
    assert_editable(document)
    if action == "reject":
        return []
    if change.get("origin") == "manual":
        manual_candidate(change)
        assert_current_inputs(change["inputs"], sources, dependencies)
        # A human text decision does not claim that source requirements were verified.
        return []
    if change["plan"]["outcome"] not in {"changes_required", "no_impact"} or change["verification"].get("verdict") != "PASS":
        fail("UNVERIFIED_CHANGE", "Предложение не прошло проверку")
    assert_current_inputs(change["inputs"], sources, dependencies)
    resolved = {item["dependency_id"] for item in change["plan"]["source_results"] if item["disposition"] != "deferred"}
    return [item for item in change["inputs"] if item["dependency_id"] in resolved]


def assert_approval(actor: str, document: dict, version: int, inputs: list[dict], sources: dict[str, dict], dependencies: dict[str, dict]):
    assert_version(document, version)
    assert_approver(actor, document)
    assert_current_inputs(inputs, sources, dependencies)
    if document["provenance_state"] == "unresolved":
        fail("BASELINE_REQUIRED", "Исторические основания документа не восстановлены", 409)
    for item in inputs:
        dependency = dependencies[item["dependency_id"]]
        if dependency["blocks_approval"] and not input_reviewed(dependency, item["source_revision_id"], document["current_revision_id"], item.get("source_state_token", "")):
            fail("UNRESOLVED_DEPENDENCY", "Перед утверждением разберите влияние обязательных оснований", 409)


def select_plan(plan: dict, selected: list[str], parent_id: str) -> dict:
    if not isinstance(selected, list) or not selected or any(not isinstance(item, str) for item in selected) or len(set(selected)) != len(selected):
        fail("INVALID_SELECTION", "Выберите непустой набор правок")
    operations = plan["operations"]
    selected_ids = set(selected)
    if selected_ids - {operation["section_id"] for operation in operations}:
        fail("INVALID_SELECTION", "Выбрана неизвестная правка")
    chosen = [dict(operation) for operation in operations if operation["section_id"] in selected_ids]
    chosen_sources = {source for operation in chosen for source in operation["source_ids"]}
    deferred_sources = {source for operation in operations if operation["section_id"] not in selected_ids for source in operation["source_ids"]}
    if chosen_sources & deferred_sources:
        fail("DEPENDENT_OPERATIONS", "Правки по одному основанию нужно применять вместе")
    results = [
        {**result, "disposition": "deferred", "explanation": "Отложено пользователем; влияние остаётся неразобранным."} if result["dependency_id"] in deferred_sources else dict(result)
        for result in plan["source_results"]
    ]
    return {**plan, "operations": chosen, "source_results": results, "selection_of": parent_id}


def apply_plan(base: list[dict], plan: dict, inputs: list[dict], *, allow_deferred: bool = False) -> list[dict]:
    """Validate authorization and section operations before semantic review or write."""
    if not isinstance(plan, dict):
        fail("INVALID_PLAN", "Модель не вернула план изменений")
    outcome = choice(plan.get("outcome"), OUTCOMES, "Результат анализа")
    text(plan.get("summary"), "Объяснение", 12_000)
    operations, results = plan.get("operations"), plan.get("source_results")
    if not isinstance(operations, list) or len(operations) > 500 or not isinstance(results, list):
        fail("INVALID_PLAN", "Некорректные операции или основания")
    by_id = {item["dependency_id"]: item for item in inputs}
    accounted = set()
    for result in results:
        if not isinstance(result, dict):
            fail("INVALID_PLAN", "Некорректный результат проверки основания")
        key = result.get("dependency_id")
        if not isinstance(key, str) or key not in by_id or key in accounted:
            fail("INVALID_PLAN", "План содержит неизвестное или повторное основание")
        accounted.add(key)
        choice(result.get("disposition"), {"changed", "unchanged", "conflict", "insufficient_data"} | ({"deferred"} if allow_deferred else set()), "Влияние источника")
        text(result.get("explanation"), "Объяснение влияния", 6000)
        if result["disposition"] in {"conflict", "insufficient_data"} and outcome not in {"conflict", "insufficient_data"}:
            fail("UNRESOLVED_SOURCE", "В плане остался неразрешённый источник")
    if accounted != set(by_id):
        fail("SOURCE_OMITTED", "Модель не проверила все подключённые основания")
    if outcome != "changes_required" and operations:
        fail("INVALID_PLAN", "Операции не соответствуют результату анализа")
    if outcome == "changes_required" and not operations:
        fail("INVALID_PLAN", "Для изменения документа не указаны операции")
    sections = [dict(section) for section in base]
    original = {section["id"]: section for section in base}
    changed = set()
    used_sources = set()
    insertion_tails = {}
    for operation in operations:
        if not isinstance(operation, dict):
            fail("INVALID_PLAN", "Некорректная операция")
        kind = choice(operation.get("op"), {"replace", "delete", "add"}, "Операция")
        section_id = operation.get("section_id")
        sources = operation.get("source_ids")
        if not isinstance(sources, list) or not sources or any(not isinstance(key, str) or key not in by_id for key in sources):
            fail("INVALID_PLAN", "Операция должна ссылаться на подключённые основания")
        used_sources.update(sources)
        anchor = operation.get("after_id") if kind == "add" else section_id
        for source in sources:
            scope = by_id[source]["policy"]["target_scope"]
            if scope and anchor not in scope:
                fail("SCOPE_VIOLATION", "Операция выходит за согласованную область связи")
        if kind == "add":
            if anchor is not None and anchor not in original:
                fail("INVALID_PLAN", "Не найден раздел для вставки")
            position = insertion_tails.get(anchor, anchor)
            if position is not None and not any(item["id"] == position for item in sections):
                fail("INVALID_PLAN", "Раздел для вставки уже удалён предыдущей операцией")
            section_id = operation.setdefault("section_id", identifier())
            if not isinstance(section_id, str) or len(section_id) != 32 or section_id in original or section_id in changed:
                fail("INVALID_PLAN", "Некорректный идентификатор нового раздела")
            index = next((index + 1 for index, item in enumerate(sections) if item["id"] == position), 0)
            sections.insert(index, {"id": section_id, "text": text(operation.get("text"), "Новый раздел")})
            insertion_tails[anchor] = section_id
        else:
            if not isinstance(section_id, str) or section_id not in original or section_id in changed:
                fail("INVALID_PLAN", "Неизвестный или повторно изменённый раздел")
            index = next(index for index, item in enumerate(sections) if item["id"] == section_id)
            if kind == "replace":
                sections[index] = {"id": section_id, "text": text(operation.get("text"), "Новый текст", empty=True)}
            else:
                sections.pop(index)
        changed.add(section_id)
    dispositions = {result["dependency_id"]: result["disposition"] for result in results}
    if any(dispositions[key] != "changed" for key in used_sources) or any(value == "changed" and key not in used_sources for key, value in dispositions.items()):
        fail("INVALID_PLAN", "Результаты проверки оснований не соответствуют операциям")
    if not sections:
        fail("INVALID_PLAN", "Нельзя удалить все разделы документа")
    # Model text may omit its final newline; keep adjacent Markdown sections separate.
    if operations:
        for section, following in zip(sections, sections[1:]):
            if section["text"] and not section["text"].endswith(("\n", "\r")) and not following["text"].startswith(("\n", "\r")):
                section["text"] += "\n"
    return validate_sections(sections)
