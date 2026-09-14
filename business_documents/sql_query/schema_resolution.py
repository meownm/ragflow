"""Catalog-backed application scenarios for the SQL document constructor.

OpenMetadata remains authoritative. The LLM may classify terms and recommend
only identifiers present in bounded catalog evidence. Search returns compact
summaries; complete columns are loaded only for selected tables.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping, Protocol, Sequence, TypeVar


MAX_SCHEMA_TERMS = 8
MAX_SCHEMA_TERM_LENGTH = 200
MAX_REQUIREMENTS_LENGTH = 20_000
MAX_CATALOG_CANDIDATES = 5
MAX_CATALOG_COLUMNS = 500
MAX_SEARCH_COLUMNS_PER_CANDIDATE = 12
SCHEMA_RESOLUTION_VERSION = "1"

_REQUEST_FIELDS = {"terms", "requirements", "locale"}
_ENTITY_REQUEST_FIELDS = {"entity_ids", "locale"}
_INTERPRETATION_FIELDS = {
    "term",
    "kind",
    "normalized_term",
    "recommended_entity_id",
    "recommended_column_ids",
    "confidence",
    "reason",
    "clarification_question",
}
_TERM_KINDS = {"entity", "field", "unknown"}
_T = TypeVar("_T")
_R = TypeVar("_R")


class SchemaResolutionValidationError(ValueError):
    """The caller or model produced an invalid closed contract."""


class CatalogAccessDenied(PermissionError):
    """The actor cannot read the catalog backing this scenario."""


class CatalogLookupError(RuntimeError):
    """One bounded catalog lookup failed after access was granted."""


class SchemaInterpretationError(RuntimeError):
    """The optional LLM interpretation could not be produced safely."""


@dataclass(frozen=True)
class PromptProvenance:
    name: str
    version: str
    content_hash: str

    def to_api(self) -> dict[str, str]:
        return {"name": self.name, "version": self.version, "content_hash": self.content_hash}


@dataclass(frozen=True)
class CatalogFreshness:
    snapshot_at: str | None
    latest_entity_updated_at: str | None
    checked_at: str | None
    catalog_checked_at: str | None
    age_hours: float | None
    stale: bool
    threshold_hours: float

    def to_api(self) -> dict[str, Any]:
        return {
            "snapshot_at": self.snapshot_at,
            "latest_entity_updated_at": self.latest_entity_updated_at,
            "checked_at": self.checked_at,
            "catalog_checked_at": self.catalog_checked_at,
            "age_hours": self.age_hours,
            "stale": self.stale,
            "threshold_hours": self.threshold_hours,
        }


@dataclass(frozen=True)
class CatalogSource:
    label: str
    url: str | None = None
    dataset_id: str | None = None

    def to_api(self) -> dict[str, str]:
        result = {"label": self.label}
        if self.url:
            result["url"] = self.url
        if self.dataset_id:
            result["dataset_id"] = self.dataset_id
        return result


@dataclass(frozen=True)
class CatalogColumn:
    id: str
    name: str
    fqn: str
    data_type: str
    description: str
    constraint: str
    glossary_terms: tuple[str, ...]

    def to_api(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "fqn": self.fqn,
            "data_type": self.data_type,
            "description": self.description,
            "constraint": self.constraint,
            "glossary_terms": list(self.glossary_terms),
        }


@dataclass(frozen=True)
class CatalogConstraint:
    constraint_type: str
    columns: tuple[str, ...]
    referred_columns: tuple[str, ...]
    relationship_type: str | None = None

    def to_api(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "constraint_type": self.constraint_type,
            "columns": list(self.columns),
            "referred_columns": list(self.referred_columns),
        }
        if self.relationship_type:
            result["relationship_type"] = self.relationship_type
        return result


@dataclass(frozen=True)
class CatalogCandidate:
    id: str
    name: str
    display_name: str | None
    technical_name: str
    fqn: str
    description: str
    version: float | None
    updated_at: str | None
    service: str
    schema: str
    database: str
    owners: tuple[str, ...]
    domains: tuple[str, ...]
    tags: tuple[str, ...]
    glossary_terms: tuple[str, ...]
    url: str
    matched_by: tuple[str, ...]
    matched_columns: tuple[str, ...]
    columns: tuple[CatalogColumn, ...]
    column_count: int
    table_constraints: tuple[CatalogConstraint, ...]
    schema_loaded: bool

    @property
    def columns_truncated(self) -> bool:
        return self.schema_loaded and self.column_count > len(self.columns)

    @property
    def schema_fingerprint(self) -> str | None:
        if not self.schema_loaded:
            return None
        payload = {
            "id": self.id,
            "fqn": self.fqn,
            "version": self.version,
            "updated_at": self.updated_at,
            "column_count": self.column_count,
            "columns": [column.to_api() for column in self.columns],
            "table_constraints": [constraint.to_api() for constraint in self.table_constraints],
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return f"sha256:{hashlib.sha256(encoded).hexdigest()}"

    def to_api(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": "table",
            "name": self.name,
            "display_name": self.display_name,
            "technical_name": self.technical_name,
            "fqn": self.fqn,
            "description": self.description,
            "version": self.version,
            "updated_at": self.updated_at,
            "service": self.service,
            "schema": self.schema,
            "database": self.database,
            "owners": list(self.owners),
            "domains": list(self.domains),
            "tags": list(self.tags),
            "glossary_terms": list(self.glossary_terms),
            "columns": [column.name for column in self.columns],
            "column_details": [column.to_api() for column in self.columns],
            "table_constraints": [constraint.to_api() for constraint in self.table_constraints],
            "matched_columns": list(self.matched_columns),
            "column_count": self.column_count,
            "described_column_count": sum(bool(column.description) for column in self.columns),
            "url": self.url,
            "matched_by": list(self.matched_by),
            "schema_loaded": self.schema_loaded,
            "schema_fingerprint": self.schema_fingerprint,
        }


@dataclass(frozen=True)
class CatalogResult:
    question: str
    answer: str
    freshness: CatalogFreshness
    retrieval: str
    sources: tuple[CatalogSource, ...]
    warnings: tuple[str, ...]
    candidates: tuple[CatalogCandidate, ...]
    needs_clarification: bool

    def to_api(self) -> dict[str, Any]:
        return {
            "agent": "catalog_copilot",
            "intent": "discovery",
            "question": self.question,
            "answer": self.answer,
            "freshness": self.freshness.to_api(),
            "retrieval": self.retrieval,
            "sources": [source.to_api() for source in self.sources],
            "warnings": list(self.warnings),
            "entities": [candidate.to_api() for candidate in self.candidates],
            "needs_clarification": self.needs_clarification,
        }


@dataclass(frozen=True)
class ResolveSchemaCommand:
    tenant_id: str
    actor_id: str
    terms: tuple[str, ...]
    requirements: str
    locale: str
    is_admin: bool = False


@dataclass(frozen=True)
class LoadSchemaEntitiesCommand:
    actor_id: str
    entity_ids: tuple[str, ...]
    locale: str
    is_admin: bool = False


class CatalogResolver(Protocol):
    async def authorize(self, *, actor_id: str, is_admin: bool) -> None: ...

    async def resolve(self, *, term: str, actor_id: str, locale: str) -> CatalogResult: ...

    async def load_entity(self, *, entity_id: str, actor_id: str, locale: str) -> CatalogResult: ...


class SchemaInterpreter(Protocol):
    def prompt_provenance(self) -> PromptProvenance | None: ...

    async def interpret(
        self,
        *,
        tenant_id: str,
        requirements: str,
        locale: str,
        catalog_answers: Sequence[tuple[str, CatalogResult]],
    ) -> Mapping[str, Any]: ...


def _required_text(value: Any, field: str, *, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SchemaResolutionValidationError(f"{field} must be a non-empty string")
    normalized = value.strip()
    if len(normalized) > maximum:
        raise SchemaResolutionValidationError(f"{field} exceeds {maximum} characters")
    return normalized


def _optional_text(value: Any, field: str, *, maximum: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise SchemaResolutionValidationError(f"{field} must be a string or null")
    normalized = value.strip()
    if len(normalized) > maximum:
        raise SchemaResolutionValidationError(f"{field} exceeds {maximum} characters")
    return normalized or None


def _optional_mapping_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text[:2_000] or None


def _unique_texts(value: Any, *, maximum: int = 100) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    result: list[str] = []
    seen: set[str] = set()
    for raw in value:
        text = str(raw or "").strip()
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            result.append(text)
        if len(result) >= maximum:
            break
    return tuple(result)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _freshness(value: Any) -> CatalogFreshness:
    source = value if isinstance(value, Mapping) else {}
    threshold = _number(source.get("threshold_hours"))
    return CatalogFreshness(
        snapshot_at=_optional_mapping_text(source.get("snapshot_at")),
        latest_entity_updated_at=_optional_mapping_text(source.get("latest_entity_updated_at")),
        checked_at=_optional_mapping_text(source.get("checked_at")),
        catalog_checked_at=_optional_mapping_text(source.get("catalog_checked_at")),
        age_hours=_number(source.get("age_hours")),
        # Unknown freshness must fail closed in the document snapshot.
        stale=bool(source.get("stale")) if isinstance(source.get("stale"), bool) else True,
        threshold_hours=threshold if threshold is not None else 0.0,
    )


def _column(raw: Any, table_fqn: str) -> CatalogColumn | None:
    if not isinstance(raw, Mapping):
        return None
    name = _optional_mapping_text(raw.get("name"))
    if not name:
        return None
    fqn = _optional_mapping_text(raw.get("fqn")) or f"{table_fqn}.{name}"
    return CatalogColumn(
        id=fqn,
        name=name[:500],
        fqn=fqn[:1_000],
        data_type=(_optional_mapping_text(raw.get("data_type")) or "")[:500],
        description=(_optional_mapping_text(raw.get("description")) or "")[:10_000],
        constraint=(_optional_mapping_text(raw.get("constraint")) or "")[:500],
        glossary_terms=_unique_texts(raw.get("glossary_terms")),
    )


def _constraint(raw: Any) -> CatalogConstraint | None:
    if not isinstance(raw, Mapping):
        return None
    constraint_type = _optional_mapping_text(raw.get("constraint_type"))
    columns = _unique_texts(raw.get("columns"))
    if not constraint_type or not columns:
        return None
    return CatalogConstraint(
        constraint_type=constraint_type[:500],
        columns=columns,
        referred_columns=_unique_texts(raw.get("referred_columns")),
        relationship_type=_optional_mapping_text(raw.get("relationship_type")),
    )


def _normalized_identifier(value: Any) -> str:
    return str(value or "").strip().strip("\"'`«»“”").casefold()


def _preview_columns(raw_columns: Any, *, term: str, table_fqn: str) -> tuple[CatalogColumn, ...]:
    if not isinstance(raw_columns, list):
        return ()
    expected = _normalized_identifier(term)
    exact: list[CatalogColumn] = []
    fallback: list[CatalogColumn] = []
    seen: set[str] = set()
    for raw in raw_columns:
        column = _column(raw, table_fqn)
        if column is None or column.id in seen:
            continue
        seen.add(column.id)
        if len(fallback) < 3:
            fallback.append(column)
        if expected and any(expected in _normalized_identifier(value) for value in (column.name, column.fqn)):
            exact.append(column)
            if len(exact) >= MAX_SEARCH_COLUMNS_PER_CANDIDATE:
                break
    return tuple(exact or fallback)


def _all_columns(raw_columns: Any, *, table_fqn: str) -> tuple[CatalogColumn, ...]:
    if not isinstance(raw_columns, list):
        return ()
    columns: list[CatalogColumn] = []
    seen: set[str] = set()
    for raw_column in raw_columns:
        column = _column(raw_column, table_fqn)
        if column is None or column.id in seen:
            continue
        seen.add(column.id)
        columns.append(column)
        if len(columns) >= MAX_CATALOG_COLUMNS:
            break
    return tuple(columns)


def _constraints(value: Any) -> tuple[CatalogConstraint, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(constraint for constraint in (_constraint(raw) for raw in value[:100]) if constraint is not None)


def _column_count(value: Any, raw_columns: Any) -> int:
    declared = value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0
    return max(declared, len(raw_columns)) if isinstance(raw_columns, list) else declared


def _candidate(raw: Any, *, term: str, include_all_columns: bool) -> CatalogCandidate | None:
    if not isinstance(raw, Mapping):
        return None
    entity_id = _optional_mapping_text(raw.get("id"))
    fqn = _optional_mapping_text(raw.get("fqn"))
    technical_name = _optional_mapping_text(raw.get("technical_name"))
    if not entity_id or not fqn or not technical_name:
        return None
    raw_columns = raw.get("column_details")
    normalized_columns = _all_columns(raw_columns, table_fqn=fqn) if include_all_columns else _preview_columns(raw_columns, term=term, table_fqn=fqn)
    return CatalogCandidate(
        id=entity_id[:500],
        name=(_optional_mapping_text(raw.get("name")) or technical_name)[:500],
        display_name=_optional_mapping_text(raw.get("display_name")),
        technical_name=technical_name[:500],
        fqn=fqn[:1_000],
        description=(_optional_mapping_text(raw.get("description")) or "")[:10_000],
        version=_number(raw.get("version")),
        updated_at=_optional_mapping_text(raw.get("updated_at")),
        service=(_optional_mapping_text(raw.get("service")) or "")[:500],
        schema=(_optional_mapping_text(raw.get("schema")) or "")[:500],
        database=(_optional_mapping_text(raw.get("database")) or "")[:500],
        owners=_unique_texts(raw.get("owners")),
        domains=_unique_texts(raw.get("domains")),
        tags=_unique_texts(raw.get("tags")),
        glossary_terms=_unique_texts(raw.get("glossary_terms")),
        url=(_optional_mapping_text(raw.get("url")) or "")[:2_000],
        matched_by=_unique_texts(raw.get("matched_by")),
        matched_columns=_unique_texts(raw.get("matched_columns")),
        columns=normalized_columns,
        column_count=_column_count(raw.get("column_count"), raw_columns),
        table_constraints=_constraints(raw.get("table_constraints")),
        schema_loaded=include_all_columns,
    )


def _source(raw: Any) -> CatalogSource | None:
    if not isinstance(raw, Mapping):
        return None
    label = _optional_mapping_text(raw.get("label"))
    if not label:
        return None
    return CatalogSource(
        label=label[:500],
        url=_optional_mapping_text(raw.get("url")),
        dataset_id=_optional_mapping_text(raw.get("dataset_id")),
    )


def catalog_result_from_mapping(
    term: str,
    answer: Mapping[str, Any],
    *,
    include_all_columns: bool = False,
) -> CatalogResult:
    """Normalize an upstream OpenMetadata map into the owned closed contract."""

    if not isinstance(answer, Mapping):
        raise CatalogLookupError("Catalog returned an invalid response")
    raw_entities = answer.get("entities")
    candidates = tuple(
        candidate
        for candidate in (_candidate(raw, term=term, include_all_columns=include_all_columns) for raw in (raw_entities[:MAX_CATALOG_CANDIDATES] if isinstance(raw_entities, list) else []))
        if candidate is not None
    )
    exact_ids = _exact_candidate_ids(term, candidates)
    needs_clarification = bool(answer.get("needs_clarification")) or not candidates or (len(candidates) > 1 and len(exact_ids) != 1)
    sources = tuple(source for source in (_source(value) for value in answer.get("sources", [])[:100]) if source is not None) if isinstance(answer.get("sources"), list) else ()
    return CatalogResult(
        question=(_optional_mapping_text(answer.get("question")) or term)[:2_000],
        answer=(_optional_mapping_text(answer.get("answer")) or "")[:10_000],
        freshness=_freshness(answer.get("freshness")),
        retrieval=(_optional_mapping_text(answer.get("retrieval")) or "")[:500],
        sources=sources,
        warnings=_unique_texts(answer.get("warnings")),
        candidates=candidates,
        needs_clarification=needs_clarification,
    )


def _parse_identifiers(value: Any, *, field: str, maximum_length: int) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise SchemaResolutionValidationError(f"{field} must be a non-empty array")
    if len(value) > MAX_SCHEMA_TERMS:
        raise SchemaResolutionValidationError(f"{field} must contain no more than {MAX_SCHEMA_TERMS} items")
    result: list[str] = []
    seen: set[str] = set()
    for index, raw in enumerate(value):
        item = _required_text(raw, f"{field}[{index}]", maximum=maximum_length)
        key = item.casefold()
        if key in seen:
            raise SchemaResolutionValidationError(f"{field} must be unique ignoring case")
        seen.add(key)
        result.append(item)
    return tuple(result)


def _locale(value: Any) -> str:
    if value not in {"ru", "en"}:
        raise SchemaResolutionValidationError("locale must be ru or en")
    return str(value)


def parse_resolve_schema_command(
    payload: Any,
    *,
    tenant_id: str,
    actor_id: str,
    is_admin: bool = False,
) -> ResolveSchemaCommand:
    if not isinstance(payload, dict):
        raise SchemaResolutionValidationError("Request body must be a JSON object")
    unknown = set(payload) - _REQUEST_FIELDS
    if unknown:
        raise SchemaResolutionValidationError(f"Unknown request fields: {', '.join(sorted(unknown))}")
    requirements = payload.get("requirements", "")
    if not isinstance(requirements, str):
        raise SchemaResolutionValidationError("requirements must be a string")
    requirements = requirements.strip()
    if len(requirements) > MAX_REQUIREMENTS_LENGTH:
        raise SchemaResolutionValidationError(f"requirements exceeds {MAX_REQUIREMENTS_LENGTH} characters")
    return ResolveSchemaCommand(
        tenant_id=_required_text(tenant_id, "tenant_id", maximum=200),
        actor_id=_required_text(actor_id, "actor_id", maximum=200),
        terms=_parse_identifiers(payload.get("terms"), field="terms", maximum_length=MAX_SCHEMA_TERM_LENGTH),
        requirements=requirements,
        locale=_locale(payload.get("locale", "ru")),
        is_admin=bool(is_admin),
    )


def parse_load_schema_entities_command(
    payload: Any,
    *,
    actor_id: str,
    is_admin: bool = False,
) -> LoadSchemaEntitiesCommand:
    if not isinstance(payload, dict):
        raise SchemaResolutionValidationError("Request body must be a JSON object")
    unknown = set(payload) - _ENTITY_REQUEST_FIELDS
    if unknown:
        raise SchemaResolutionValidationError(f"Unknown request fields: {', '.join(sorted(unknown))}")
    return LoadSchemaEntitiesCommand(
        actor_id=_required_text(actor_id, "actor_id", maximum=200),
        entity_ids=_parse_identifiers(payload.get("entity_ids"), field="entity_ids", maximum_length=500),
        locale=_locale(payload.get("locale", "ru")),
        is_admin=bool(is_admin),
    )


def _candidate_ids(answer: CatalogResult) -> tuple[str, ...]:
    return tuple(candidate.id for candidate in answer.candidates)


def _is_exact_candidate(expected: str, entity: CatalogCandidate) -> bool:
    return any(_normalized_identifier(value) == expected for value in (entity.fqn, entity.technical_name, entity.name, entity.display_name))


def _exact_candidate_ids(term: str, candidates: Sequence[CatalogCandidate]) -> tuple[str, ...]:
    expected = _normalized_identifier(term)
    return tuple(candidate.id for candidate in candidates if _is_exact_candidate(expected, candidate))


def _matching_columns(term: str, answer: CatalogResult) -> tuple[tuple[str, str], ...]:
    expected = _normalized_identifier(term)
    return tuple((candidate.id, column.id) for candidate in answer.candidates for column in candidate.columns if expected in {_normalized_identifier(column.name), _normalized_identifier(column.fqn)})


def _fallback_interpretation(term: str, answer: CatalogResult, locale: str) -> dict[str, Any]:
    candidate_ids = _candidate_ids(answer)
    exact_ids = _exact_candidate_ids(term, answer.candidates)
    column_matches = _matching_columns(term, answer)
    if not candidate_ids:
        reason = "No catalog-backed candidate was found." if locale == "en" else "Каталог не вернул подтверждённых кандидатов."
        question = "Refine the business term, schema, or data source." if locale == "en" else "Уточните бизнес-термин, схему или источник данных."
    elif answer.needs_clarification:
        reason = "Several catalog-backed candidates remain." if locale == "en" else "В каталоге осталось несколько подтверждённых кандидатов."
        question = "Select the physical table that matches the requirement." if locale == "en" else "Выберите физическую таблицу, соответствующую требованию."
    else:
        reason = "The catalog returned one candidate." if locale == "en" else "Каталог вернул одного кандидата."
        question = None
    matching_entity_ids = {entity_id for entity_id, _ in column_matches}
    recommended_entity = (
        candidate_ids[0] if len(candidate_ids) == 1 else exact_ids[0] if len(exact_ids) == 1 and not answer.needs_clarification else column_matches[0][0] if len(matching_entity_ids) == 1 else None
    )
    return {
        "term": term,
        "kind": "unknown" if not candidate_ids else "field" if column_matches else "entity",
        "normalized_term": term,
        "recommended_entity_id": recommended_entity,
        "recommended_column_ids": [column_id for _, column_id in column_matches],
        "confidence": None,
        "reason": reason,
        "clarification_question": question,
    }


def _validated_interpretation_term(
    item: Any,
    index: int,
    expected: Mapping[str, CatalogResult],
    existing: Mapping[str, Any],
) -> tuple[str, Mapping[str, Any]]:
    if not isinstance(item, Mapping) or set(item) != _INTERPRETATION_FIELDS:
        raise SchemaResolutionValidationError(f"LLM resolution {index} must contain the exact contract fields")
    term = _required_text(item.get("term"), f"resolutions[{index}].term", maximum=MAX_SCHEMA_TERM_LENGTH)
    if term not in expected or term in existing:
        raise SchemaResolutionValidationError("LLM response contains an unknown or duplicate term")
    return term, item


def _validated_recommendations(
    item: Mapping[str, Any],
    answer: CatalogResult,
    kind: str,
) -> tuple[str | None, tuple[str, ...]]:
    recommended = _optional_text(item.get("recommended_entity_id"), "recommended_entity_id", maximum=500)
    if recommended is not None and recommended not in _candidate_ids(answer):
        raise SchemaResolutionValidationError("LLM recommended an entity outside the catalog candidates")

    raw_columns = item.get("recommended_column_ids")
    maximum = MAX_SEARCH_COLUMNS_PER_CANDIDATE * MAX_CATALOG_CANDIDATES
    if not isinstance(raw_columns, list) or len(raw_columns) > maximum:
        raise SchemaResolutionValidationError("recommended_column_ids must be a bounded array")
    if any(not isinstance(value, str) or not value.strip() for value in raw_columns):
        raise SchemaResolutionValidationError("recommended_column_ids must contain non-empty strings")
    recommended_columns = _unique_texts(raw_columns, maximum=maximum)
    if len(recommended_columns) != len(raw_columns):
        raise SchemaResolutionValidationError("recommended_column_ids must contain unique non-empty strings")

    allowed_columns = {column.id for candidate in answer.candidates if recommended is None or candidate.id == recommended for column in candidate.columns}
    if any(column_id not in allowed_columns for column_id in recommended_columns):
        raise SchemaResolutionValidationError("LLM recommended a column outside the catalog candidates")
    if kind != "field" and recommended_columns:
        raise SchemaResolutionValidationError("Only field interpretations may recommend columns")
    return recommended, recommended_columns


def _validated_confidence(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
        raise SchemaResolutionValidationError("confidence must be between 0 and 1 or null")
    return float(value)


def _validated_interpretation(
    item: Mapping[str, Any],
    *,
    term: str,
    answer: CatalogResult,
    index: int,
) -> dict[str, Any]:
    kind = item.get("kind")
    if kind not in _TERM_KINDS:
        raise SchemaResolutionValidationError(f"resolutions[{index}].kind is unsupported")
    recommended, recommended_columns = _validated_recommendations(item, answer, str(kind))
    return {
        "term": term,
        "kind": kind,
        "normalized_term": _required_text(
            item.get("normalized_term"),
            "normalized_term",
            maximum=MAX_SCHEMA_TERM_LENGTH,
        ),
        "recommended_entity_id": recommended,
        "recommended_column_ids": list(recommended_columns),
        "confidence": _validated_confidence(item.get("confidence")),
        "reason": _required_text(item.get("reason"), "reason", maximum=2_000),
        "clarification_question": _optional_text(
            item.get("clarification_question"),
            "clarification_question",
            maximum=1_000,
        ),
    }


def _validated_interpretations(
    raw: Mapping[str, Any],
    catalog_answers: Sequence[tuple[str, CatalogResult]],
) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, Mapping) or set(raw) != {"schema_version", "resolutions"}:
        raise SchemaResolutionValidationError("LLM response must contain exactly schema_version and resolutions")
    if raw.get("schema_version") != SCHEMA_RESOLUTION_VERSION:
        raise SchemaResolutionValidationError("LLM response schema_version is unsupported")
    items = raw.get("resolutions")
    if not isinstance(items, list) or len(items) != len(catalog_answers):
        raise SchemaResolutionValidationError("LLM response must contain one resolution per term")

    expected = dict(catalog_answers)
    normalized: dict[str, dict[str, Any]] = {}
    for index, raw_item in enumerate(items):
        term, item = _validated_interpretation_term(raw_item, index, expected, normalized)
        normalized[term] = _validated_interpretation(
            item,
            term=term,
            answer=expected[term],
            index=index,
        )
    return normalized


async def _bounded_batch(
    items: Sequence[_T],
    operation: Callable[[_T], Awaitable[_R]],
    *,
    concurrency: int,
    item_timeout_seconds: float,
    batch_timeout_seconds: float,
) -> list[_R | Exception]:
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def run_one(item: _T) -> _R | Exception:
        try:
            async with semaphore:
                return await asyncio.wait_for(operation(item), timeout=item_timeout_seconds)
        except TimeoutError:
            return CatalogLookupError("Catalog lookup timed out")
        except CatalogLookupError as exc:
            return exc

    tasks = [asyncio.create_task(run_one(item)) for item in items]
    done, pending = await asyncio.wait(tasks, timeout=batch_timeout_seconds)
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    results: list[_R | Exception] = []
    for task in tasks:
        if task not in done:
            results.append(CatalogLookupError("Catalog batch deadline exceeded"))
        else:
            results.append(task.result())
    return results


def _failure_result(term: str, locale: str) -> CatalogResult:
    message = "Catalog lookup failed for this term." if locale == "en" else "Не удалось выполнить поиск по каталогу для этого термина."
    return CatalogResult(
        question=term,
        answer=message,
        freshness=_freshness(None),
        retrieval="",
        sources=(),
        warnings=(message,),
        candidates=(),
        needs_clarification=True,
    )


def _lookup_error(locale: str, *, timeout: bool = False) -> dict[str, Any]:
    if locale == "en":
        message = "The catalog lookup timed out." if timeout else "The catalog lookup failed."
    else:
        message = "Истекло время ожидания каталога." if timeout else "Не удалось выполнить поиск по каталогу."
    return {
        "status": "ERROR",
        "error_code": "CATALOG_TIMEOUT" if timeout else "CATALOG_LOOKUP_FAILED",
        "retryable": True,
        "message": message,
    }


def _lookup_ok() -> dict[str, Any]:
    return {"status": "OK", "error_code": None, "retryable": False, "message": None}


def _is_timeout_error(error: Exception) -> bool:
    message = str(error).casefold()
    return "timed out" in message or "deadline" in message


def _collate_catalog_outcomes(
    terms: Sequence[str],
    outcomes: Sequence[CatalogResult | Exception],
    locale: str,
) -> tuple[
    list[tuple[str, CatalogResult]],
    dict[str, dict[str, Any]],
    list[tuple[str, CatalogResult]],
]:
    catalog_answers: list[tuple[str, CatalogResult]] = []
    lookup_states: dict[str, dict[str, Any]] = {}
    successful_answers: list[tuple[str, CatalogResult]] = []
    for term, outcome in zip(terms, outcomes, strict=True):
        if isinstance(outcome, Exception):
            result = _failure_result(term, locale)
            lookup_states[term] = _lookup_error(locale, timeout=_is_timeout_error(outcome))
        else:
            result = outcome
            lookup_states[term] = _lookup_ok()
            successful_answers.append((term, result))
        catalog_answers.append((term, result))
    return catalog_answers, lookup_states, successful_answers


def _resolution_payload(
    catalog_answers: Sequence[tuple[str, CatalogResult]],
    lookup_states: Mapping[str, dict[str, Any]],
    interpretations: Mapping[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], bool, bool]:
    resolutions: list[dict[str, Any]] = []
    needs_clarification = False
    degraded = False
    for term, answer in catalog_answers:
        lookup = lookup_states[term]
        failed = lookup["status"] == "ERROR"
        unresolved = failed or answer.needs_clarification or not answer.candidates
        degraded = degraded or failed
        needs_clarification = needs_clarification or unresolved
        resolutions.append(
            {
                "term": term,
                "lookup": lookup,
                "catalog_answer": answer.to_api(),
                "interpretation": interpretations[term],
                "needs_clarification": unresolved,
            }
        )
    return resolutions, needs_clarification, degraded


class SchemaResolutionScenario:
    """Resolve compact candidates concurrently, then interpret them once."""

    def __init__(
        self,
        catalog: CatalogResolver,
        interpreter: SchemaInterpreter,
        *,
        max_concurrency: int = 3,
        lookup_timeout_seconds: float = 20.0,
        batch_timeout_seconds: float = 45.0,
        overall_timeout_seconds: float = 120.0,
    ):
        self._catalog = catalog
        self._interpreter = interpreter
        self._max_concurrency = max_concurrency
        self._lookup_timeout_seconds = lookup_timeout_seconds
        self._batch_timeout_seconds = batch_timeout_seconds
        self._overall_timeout_seconds = overall_timeout_seconds

    async def run(self, command: ResolveSchemaCommand) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._overall_timeout_seconds
        try:
            await asyncio.wait_for(
                self._catalog.authorize(actor_id=command.actor_id, is_admin=command.is_admin),
                timeout=max(0.001, min(self._lookup_timeout_seconds, deadline - loop.time())),
            )
        except TimeoutError as exc:
            raise CatalogLookupError("Catalog authorization timed out") from exc

        async def resolve_term(term: str) -> CatalogResult:
            return await self._catalog.resolve(term=term, actor_id=command.actor_id, locale=command.locale)

        outcomes = await _bounded_batch(
            command.terms,
            resolve_term,
            concurrency=self._max_concurrency,
            item_timeout_seconds=self._lookup_timeout_seconds,
            batch_timeout_seconds=max(0.001, min(self._batch_timeout_seconds, deadline - loop.time())),
        )
        catalog_answers, lookup_states, successful_answers = _collate_catalog_outcomes(
            command.terms,
            outcomes,
            command.locale,
        )

        try:
            prompt = self._interpreter.prompt_provenance()
        except SchemaInterpretationError:
            prompt = None
        llm_status = "SKIPPED"
        llm_warning: str | None = None
        interpretations = {term: _fallback_interpretation(term, answer, command.locale) for term, answer in catalog_answers}
        llm_candidates = [(term, answer) for term, answer in successful_answers if answer.candidates]
        if llm_candidates and loop.time() < deadline:
            try:
                raw = await asyncio.wait_for(
                    self._interpreter.interpret(
                        tenant_id=command.tenant_id,
                        requirements=command.requirements,
                        locale=command.locale,
                        catalog_answers=llm_candidates,
                    ),
                    timeout=max(0.001, deadline - loop.time()),
                )
                interpretations.update(_validated_interpretations(raw, llm_candidates))
                llm_status = "APPLIED"
            except (TimeoutError, SchemaInterpretationError, SchemaResolutionValidationError):
                llm_status = "FALLBACK"
                llm_warning = (
                    "The LLM interpretation was unavailable; deterministic catalog decisions were retained."
                    if command.locale == "en"
                    else "LLM-интерпретация недоступна; сохранены детерминированные решения каталога."
                )

        resolutions, needs_clarification, degraded = _resolution_payload(
            catalog_answers,
            lookup_states,
            interpretations,
        )
        return {
            "schema_version": SCHEMA_RESOLUTION_VERSION,
            "status": "DEGRADED" if degraded else "NEEDS_CLARIFICATION" if needs_clarification else "READY",
            "resolutions": resolutions,
            "llm": {
                "status": llm_status,
                "prompt": prompt.to_api() if prompt else None,
                "warning": llm_warning,
            },
        }


class SchemaEntityDetailsScenario:
    """Load complete schemas only for selected catalog entity IDs."""

    def __init__(
        self,
        catalog: CatalogResolver,
        *,
        max_concurrency: int = 3,
        lookup_timeout_seconds: float = 20.0,
        batch_timeout_seconds: float = 45.0,
    ):
        self._catalog = catalog
        self._max_concurrency = max_concurrency
        self._lookup_timeout_seconds = lookup_timeout_seconds
        self._batch_timeout_seconds = batch_timeout_seconds

    async def run(self, command: LoadSchemaEntitiesCommand) -> dict[str, Any]:
        try:
            await asyncio.wait_for(
                self._catalog.authorize(actor_id=command.actor_id, is_admin=command.is_admin),
                timeout=self._lookup_timeout_seconds,
            )
        except TimeoutError as exc:
            raise CatalogLookupError("Catalog authorization timed out") from exc

        async def load_entity(entity_id: str) -> CatalogResult:
            return await self._catalog.load_entity(
                entity_id=entity_id,
                actor_id=command.actor_id,
                locale=command.locale,
            )

        outcomes = await _bounded_batch(
            command.entity_ids,
            load_entity,
            concurrency=self._max_concurrency,
            item_timeout_seconds=self._lookup_timeout_seconds,
            batch_timeout_seconds=self._batch_timeout_seconds,
        )
        entities: list[dict[str, Any]] = []
        degraded = False
        for entity_id, outcome in zip(command.entity_ids, outcomes, strict=True):
            if isinstance(outcome, Exception):
                degraded = True
                entities.append(
                    {
                        "entity_id": entity_id,
                        "lookup": _lookup_error(
                            command.locale,
                            timeout=_is_timeout_error(outcome),
                        ),
                        "entity": None,
                        "freshness": None,
                        "retrieval": "",
                        "sources": [],
                        "warnings": [],
                    }
                )
                continue
            selected = next((candidate for candidate in outcome.candidates if candidate.id == entity_id), None)
            if selected is None:
                degraded = True
                entities.append(
                    {
                        "entity_id": entity_id,
                        "lookup": _lookup_error(command.locale),
                        "entity": None,
                        "freshness": outcome.freshness.to_api(),
                        "retrieval": outcome.retrieval,
                        "sources": [source.to_api() for source in outcome.sources],
                        "warnings": list(outcome.warnings),
                    }
                )
                continue
            entities.append(
                {
                    "entity_id": entity_id,
                    "lookup": _lookup_ok(),
                    "entity": selected.to_api(),
                    "freshness": outcome.freshness.to_api(),
                    "retrieval": outcome.retrieval,
                    "sources": [source.to_api() for source in outcome.sources],
                    "warnings": list(outcome.warnings),
                }
            )
        return {
            "schema_version": SCHEMA_RESOLUTION_VERSION,
            "status": "DEGRADED" if degraded else "READY",
            "entities": entities,
        }
