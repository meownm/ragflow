#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#

"""OpenMetadata-backed catalog agents used by the RAGFlow Copilot surface.

OpenMetadata remains the source of truth.  The in-process catalog snapshot is
only a short-lived search projection and every response carries its freshness.
Write operations are deliberately separated into a preview/confirm protocol.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from common.openmetadata_agents import public_agent_roles
from urllib.parse import quote, urlparse

import requests
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer


LOGGER = logging.getLogger(__name__)

_TABLE_FIELDS = "owners,domains,tags,columns,databaseSchema,database,tableConstraints,joins,dataProducts"
_TOKEN_PATTERN = re.compile(r"[\w.-]+", re.UNICODE)
_WRITE_FIELDS = {"description", "displayName"}
_STARTER_ACTIONS = {"missing_descriptions", "domain", "impact", "recent", "quality"}
_DOMAIN_QUERY_PATTERN = re.compile(r"(?:домен(?:а|е|у|ом)?|domain)\s+[\"'`«“]?([\w.-]+)", re.IGNORECASE)
_OWNER_QUERY_PATTERN = re.compile(r"(?:владел(?:ец|ьца|ьцу|ьцем)|owner)\s+[\"'`«“]?([\w.-]+)", re.IGNORECASE)
_COLUMN_QUERY_MARKERS = ("поле", "поля", "полями", "колон", "column", "хранят", "содержат", "структур")
_CONTEXT_REFERENCE_MARKERS = (
    "из них",
    "среди них",
    "этих",
    "этой таблиц",
    "этого",
    "those",
    "these",
    "them",
    "that table",
)
_CONTEXT_REFERENCE_PATTERNS = (
    re.compile(r"(?<!\w)(?:у\s+не[её]|для\s+не[её]|о\s+не[её]|она|е[её])(?=\W|$)", re.IGNORECASE),
)
_MISSING_DESCRIPTION_MARKERS = ("без описан", "не имеют описан", "missing description", "without description")
_WITH_DESCRIPTION_MARKERS = ("с описан", "имеют описан", "with description")
_RECENT_MARKERS = ("последн", "недавн", "свеж", "recent", "latest", "updated last")
_DESCRIPTION_QUERY_MARKERS = ("описан", "description")
_COLUMN_COUNT_QUERY_PATTERNS = (
    re.compile(r"(?:сколько|количеств\w*)\s+(?:у\s+не[её]\s+)?колон", re.IGNORECASE),
    re.compile(r"how\s+many\s+columns?", re.IGNORECASE),
)
_CAPABILITY_ENDPOINTS = {
    "tables": "/api/v1/tables",
    "database_services": "/api/v1/services/databaseServices",
    "domains": "/api/v1/domains",
    "glossaries": "/api/v1/glossaries",
    "dashboards": "/api/v1/dashboards",
    "pipelines": "/api/v1/pipelines",
    "topics": "/api/v1/topics",
    "ml_models": "/api/v1/mlmodels",
    "data_products": "/api/v1/dataProducts",
    "test_cases": "/api/v1/dataQuality/testCases",
}


class OpenMetadataError(RuntimeError):
    """Base error safe to translate at the REST boundary."""


class OpenMetadataConfigurationError(OpenMetadataError):
    pass


class OpenMetadataAuthenticationError(OpenMetadataError):
    pass


class OpenMetadataPermissionError(OpenMetadataError):
    pass


class OpenMetadataNotFoundError(OpenMetadataError):
    pass


class OpenMetadataConflictError(OpenMetadataError):
    pass


@dataclass(frozen=True)
class OpenMetadataConfig:
    base_url: str
    public_url: str
    username: str
    password: str
    jwt_token: str
    timeout_seconds: float
    retries: int
    cache_ttl_seconds: int
    stale_after_hours: int
    max_entities: int
    max_results: int
    write_enabled: bool
    confirmation_ttl_seconds: int
    dataset_id: str = ""
    dataset_top_n: int = 20
    dataset_similarity_threshold: float = 0.05
    dataset_vector_similarity_weight: float = 0.3

    @classmethod
    def from_env(cls) -> "OpenMetadataConfig":
        base_url = os.getenv("OPENMETADATA_URL", "http://host.docker.internal:8585").strip().rstrip("/")
        public_url = os.getenv("OPENMETADATA_PUBLIC_URL", "http://127.0.0.1:8585").strip().rstrip("/")
        _validate_base_url(base_url, "OPENMETADATA_URL")
        _validate_base_url(public_url, "OPENMETADATA_PUBLIC_URL")
        return cls(
            base_url=base_url,
            public_url=public_url,
            username=os.getenv("OPENMETADATA_USERNAME", "").strip(),
            password=os.getenv("OPENMETADATA_PASSWORD", ""),
            jwt_token=os.getenv("OPENMETADATA_JWT_TOKEN", "").strip(),
            timeout_seconds=max(1.0, float(os.getenv("OPENMETADATA_TIMEOUT_SECONDS", "12"))),
            retries=max(0, min(4, int(os.getenv("OPENMETADATA_RETRIES", "2")))),
            cache_ttl_seconds=max(10, int(os.getenv("OPENMETADATA_CACHE_TTL_SECONDS", "900"))),
            stale_after_hours=max(1, int(os.getenv("OPENMETADATA_STALE_AFTER_HOURS", "168"))),
            max_entities=max(1, int(os.getenv("OPENMETADATA_MAX_ENTITIES", "5000"))),
            max_results=max(1, min(100, int(os.getenv("OPENMETADATA_MAX_RESULTS", "25")))),
            write_enabled=_env_bool("OPENMETADATA_WRITE_ENABLED", False),
            confirmation_ttl_seconds=max(30, int(os.getenv("OPENMETADATA_CONFIRMATION_TTL_SECONDS", "300"))),
            dataset_id=os.getenv("OPENMETADATA_DATASET_ID", "").strip(),
            dataset_top_n=max(1, min(100, int(os.getenv("OPENMETADATA_DATASET_TOP_N", "20")))),
            dataset_similarity_threshold=max(
                0.0,
                min(1.0, float(os.getenv("OPENMETADATA_DATASET_SIMILARITY_THRESHOLD", "0.05"))),
            ),
            dataset_vector_similarity_weight=max(
                0.0,
                min(1.0, float(os.getenv("OPENMETADATA_DATASET_VECTOR_WEIGHT", "0.3"))),
            ),
        )

    @property
    def configured(self) -> bool:
        return bool(self.jwt_token or (self.username and self.password))


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _validate_base_url(value: str, name: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise OpenMetadataConfigurationError(f"{name} must be an http(s) origin without embedded credentials")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise OpenMetadataConfigurationError(f"{name} must not contain a path, query, or fragment")


def _tokens(value: str) -> list[str]:
    return [token.casefold() for token in _TOKEN_PATTERN.findall(value or "") if len(token) > 1]


def _language(locale: str | None) -> str:
    return "en" if str(locale or "").casefold().startswith("en") else "ru"


def _localized(locale: str | None, *, ru: str, en: str) -> str:
    return en if _language(locale) == "en" else ru


def _reference_names(value: Any) -> list[str]:
    if not value:
        return []
    values = value if isinstance(value, list) else [value]
    result: list[str] = []
    for item in values:
        if not isinstance(item, dict):
            continue
        name = item.get("displayName") or item.get("name") or item.get("fullyQualifiedName")
        if name:
            result.append(str(name))
    return result


def _reference_aliases(value: Any) -> list[str]:
    if not value:
        return []
    values = value if isinstance(value, list) else [value]
    aliases: list[str] = []
    for item in values:
        if not isinstance(item, dict):
            continue
        for key in ("displayName", "name", "fullyQualifiedName"):
            alias = str(item.get(key) or "").strip()
            if alias and alias.casefold() not in {value.casefold() for value in aliases}:
                aliases.append(alias)
    return aliases


def _unique_strings(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = str(value or "").strip()
        key = normalized.casefold()
        if normalized and key not in seen:
            seen.add(key)
            result.append(normalized)
    return result


def _glossary_terms(entity: dict[str, Any]) -> list[str]:
    terms = [str(value) for value in entity.get("glossaryTags") or [] if value]
    for tag in entity.get("tags") or []:
        if not isinstance(tag, dict) or str(tag.get("source") or "").casefold() != "glossary":
            continue
        value = tag.get("tagFQN") or tag.get("displayName") or tag.get("name")
        if value:
            terms.append(str(value))
    return _unique_strings(terms)


def _table_constraints(entity: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for constraint in entity.get("tableConstraints") or []:
        if not isinstance(constraint, dict):
            continue
        constraint_type = str(constraint.get("constraintType") or "").strip().upper()
        columns = _unique_strings([str(value) for value in constraint.get("columns") or []])
        referred_columns = _unique_strings([str(value) for value in constraint.get("referredColumns") or []])
        if not constraint_type or not columns:
            continue
        item = {
            "constraint_type": constraint_type,
            "columns": columns,
            "referred_columns": referred_columns,
        }
        relationship_type = str(constraint.get("relationshipType") or "").strip()
        if relationship_type:
            item["relationship_type"] = relationship_type
        result.append(item)
    return result


def _epoch_to_iso(value: Any) -> str | None:
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=UTC).isoformat()
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _entity_url(public_url: str, entity: dict[str, Any]) -> str:
    fqn = entity.get("fullyQualifiedName") or entity.get("name") or ""
    return f"{public_url}/table/{quote(str(fqn), safe='')}"


def normalize_table(entity: dict[str, Any], public_url: str) -> dict[str, Any]:
    columns = entity.get("columns") or []
    owner_refs = entity.get("owners") or entity.get("owner")
    domain_refs = entity.get("domains") or entity.get("domain")
    owners = _unique_strings(_reference_names(owner_refs))
    domains = _unique_strings(_reference_names(domain_refs))
    column_details = []
    for column in columns:
        if not isinstance(column, dict):
            continue
        name = str(column.get("displayName") or column.get("name") or "").strip()
        if not name:
            continue
        column_details.append(
            {
                "name": name,
                "fqn": str(column.get("fullyQualifiedName") or ""),
                "data_type": str(column.get("dataTypeDisplay") or column.get("dataType") or ""),
                "description": column.get("description") or "",
                "constraint": str(column.get("constraint") or ""),
                "glossary_terms": _glossary_terms(column),
            }
        )
    column_names = _unique_strings([column["name"] for column in column_details])
    tags: list[str] = []
    for tag in entity.get("tags") or []:
        if not isinstance(tag, dict):
            continue
        value = tag.get("displayName") or tag.get("tagFQN") or tag.get("name")
        if value:
            tags.append(str(value))
    service = entity.get("service") if isinstance(entity.get("service"), dict) else {}
    schema = entity.get("databaseSchema") if isinstance(entity.get("databaseSchema"), dict) else {}
    database = entity.get("database") if isinstance(entity.get("database"), dict) else {}
    card = {
        "id": str(entity.get("id") or ""),
        "type": "table",
        "name": str(entity.get("displayName") or entity.get("name") or ""),
        "display_name": entity.get("displayName"),
        "technical_name": str(entity.get("name") or ""),
        "fqn": str(entity.get("fullyQualifiedName") or entity.get("name") or ""),
        "description": entity.get("description"),
        "version": entity.get("version"),
        "updated_at": _epoch_to_iso(entity.get("updatedAt")),
        "updated_at_epoch": entity.get("updatedAt"),
        "updated_by": entity.get("updatedBy") or "",
        "service": service.get("displayName") or service.get("name") or "",
        "schema": schema.get("displayName") or schema.get("name") or "",
        "database": database.get("displayName") or database.get("name") or "",
        "owners": owners,
        "owner_keys": _unique_strings(_reference_aliases(owner_refs)),
        "domains": domains,
        "domain_keys": _unique_strings(_reference_aliases(domain_refs)),
        "tags": _unique_strings(tags),
        "glossary_terms": _glossary_terms(entity),
        "columns": column_names,
        "column_details": column_details,
        "column_count": len(columns),
        "described_column_count": sum(1 for column in columns if isinstance(column, dict) and column.get("description")),
        "deleted": bool(entity.get("deleted")),
        "processed_lineage": bool(entity.get("processedLineage") or entity.get("upstreamLineage")),
        "table_constraints": _table_constraints(entity),
    }
    card["url"] = _entity_url(public_url, entity)
    return card


def _normalize_test_case(test_case: dict[str, Any]) -> dict[str, Any]:
    test_definition = test_case.get("testDefinition") if isinstance(test_case.get("testDefinition"), dict) else {}
    test_suite = test_case.get("testSuite") if isinstance(test_case.get("testSuite"), dict) else {}
    latest_result = test_case.get("testCaseResult") if isinstance(test_case.get("testCaseResult"), dict) else {}
    return {
        "id": str(test_case.get("id") or ""),
        "name": str(test_case.get("displayName") or test_case.get("name") or ""),
        "fqn": str(test_case.get("fullyQualifiedName") or test_case.get("name") or ""),
        "entity_link": str(test_case.get("entityLink") or ""),
        "definition": str(test_definition.get("displayName") or test_definition.get("name") or ""),
        "suite": str(test_suite.get("displayName") or test_suite.get("name") or ""),
        "status": str(latest_result.get("testCaseStatus") or latest_result.get("status") or ""),
        "result_timestamp": _epoch_to_iso(latest_result.get("timestamp")),
    }


def _table_counts(tables: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "tables": len(tables),
        "columns": sum(int(table["column_count"]) for table in tables),
        "described_tables": sum(bool(table["description"]) for table in tables),
        "described_columns": sum(int(table["described_column_count"]) for table in tables),
        "owned_tables": sum(bool(table["owners"]) for table in tables),
        "domain_tables": sum(bool(table["domains"]) for table in tables),
        "tagged_tables": sum(bool(table["tags"]) for table in tables),
        "lineage_candidates": sum(bool(table["processed_lineage"]) for table in tables),
        "foreign_key_constraints": sum(constraint.get("constraint_type") == "FOREIGN_KEY" for table in tables for constraint in table.get("table_constraints") or []),
        "tables_with_foreign_keys": sum(any(constraint.get("constraint_type") == "FOREIGN_KEY" for constraint in table.get("table_constraints") or []) for table in tables),
        "glossary_linked_tables": sum(bool(table.get("glossary_terms")) for table in tables),
    }


class OpenMetadataClient:
    """Small fixed-origin client; user input never controls the destination URL."""

    def __init__(self, config: OpenMetadataConfig, session: requests.Session | None = None):
        self.config = config
        self._session = session or requests.Session()
        self._token = config.jwt_token
        self._token_lock = threading.Lock()

    def _login(self) -> str:
        if self.config.jwt_token:
            return self.config.jwt_token
        if not self.config.username or not self.config.password:
            raise OpenMetadataConfigurationError("OpenMetadata credentials are not configured; set OPENMETADATA_JWT_TOKEN or username/password")
        password = base64.b64encode(self.config.password.encode("utf-8")).decode("ascii")
        try:
            response = self._session.post(
                f"{self.config.base_url}/api/v1/users/login",
                json={"email": self.config.username, "password": password},
                headers={"Accept": "application/json"},
                timeout=self.config.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise OpenMetadataAuthenticationError("OpenMetadata login is unavailable") from exc
        if response.status_code >= 400:
            raise OpenMetadataAuthenticationError("OpenMetadata rejected the configured credentials")
        try:
            token = str(response.json()["accessToken"])
        except (ValueError, KeyError, TypeError) as exc:
            raise OpenMetadataAuthenticationError("OpenMetadata login returned an invalid response") from exc
        if not token:
            raise OpenMetadataAuthenticationError("OpenMetadata login returned an empty token")
        return token

    def _authorization(self, *, force_refresh: bool = False) -> str:
        with self._token_lock:
            if force_refresh and not self.config.jwt_token:
                self._token = ""
            if not self._token:
                self._token = self._login()
            return self._token

    def request(self, method: str, path: str, *, params=None, json_body=None, content_type: str | None = None) -> dict[str, Any]:
        if not path.startswith("/api/v1/") or "//" in path:
            raise OpenMetadataConfigurationError("OpenMetadata API path is not allowed")
        method = method.upper()
        transport_attempts = self.config.retries + 1 if method == "GET" else 1
        transport_attempt = 0
        refreshed = False
        while transport_attempt < transport_attempts:
            headers = {
                "Accept": "application/json",
                "Authorization": f"Bearer {self._authorization()}",
            }
            if content_type:
                headers["Content-Type"] = content_type
            try:
                response = self._session.request(
                    method,
                    f"{self.config.base_url}{path}",
                    params=params,
                    json=json_body,
                    headers=headers,
                    timeout=self.config.timeout_seconds,
                )
            except requests.RequestException as exc:
                transport_attempt += 1
                if transport_attempt < transport_attempts:
                    time.sleep(min(0.2 * (2 ** (transport_attempt - 1)), 1.0))
                    continue
                raise OpenMetadataError("OpenMetadata is unavailable") from exc

            if response.status_code == 401 and not refreshed and not self.config.jwt_token:
                self._authorization(force_refresh=True)
                refreshed = True
                continue
            if response.status_code in {429, 500, 502, 503, 504}:
                transport_attempt += 1
                if transport_attempt < transport_attempts:
                    time.sleep(min(0.2 * (2 ** (transport_attempt - 1)), 1.0))
                    continue
            if response.status_code == 401:
                raise OpenMetadataAuthenticationError("OpenMetadata authentication expired or is invalid")
            if response.status_code == 403:
                raise OpenMetadataPermissionError("OpenMetadata denied this operation")
            if response.status_code == 404:
                raise OpenMetadataNotFoundError("OpenMetadata entity was not found")
            if response.status_code == 409:
                raise OpenMetadataConflictError("OpenMetadata reported a concurrent change")
            if response.status_code >= 400:
                raise OpenMetadataError(f"OpenMetadata request failed with HTTP {response.status_code}")
            if not response.content:
                return {}
            try:
                payload = response.json()
            except ValueError as exc:
                raise OpenMetadataError("OpenMetadata returned malformed JSON") from exc
            if not isinstance(payload, dict):
                raise OpenMetadataError("OpenMetadata returned an unexpected response")
            return payload
        raise OpenMetadataError("OpenMetadata request failed")

    def get(self, path: str, *, params=None) -> dict[str, Any]:
        return self.request("GET", path, params=params)

    def patch(self, path: str, patch: list[dict[str, Any]]) -> dict[str, Any]:
        return self.request("PATCH", path, json_body=patch, content_type="application/json-patch+json")

    def list_tables(self, max_entities: int) -> list[dict[str, Any]]:
        """Load the catalog from OMD's search projection in bounded pages.

        The entity-list endpoint becomes prohibitively slow when all table
        columns are requested (the local 853-table catalog already exceeds a
        normal API timeout).  OMD's table search index contains the same card
        fields and returns the full local catalog in under a second.  Exact
        entity details are still re-read from the entity API before writes.
        """
        result: list[dict[str, Any]] = []
        while len(result) < max_entities:
            size = min(1000, max_entities - len(result))
            page = self.get(
                "/api/v1/search/query",
                params={"q": "*", "index": "table_search_index", "from": len(result), "size": size},
            )
            hits_block = page.get("hits") if isinstance(page.get("hits"), dict) else {}
            hits = hits_block.get("hits") or []
            data = [hit.get("_source") for hit in hits if isinstance(hit, dict) and isinstance(hit.get("_source"), dict)]
            result.extend(data)
            total_block = hits_block.get("total")
            total = total_block.get("value") if isinstance(total_block, dict) else total_block
            if not data or (isinstance(total, int) and len(result) >= total):
                break
        return result[:max_entities]

    def search_tables(self, query: str, size: int) -> list[dict[str, Any]]:
        response = self.get(
            "/api/v1/search/query",
            params={"q": query, "index": "table_search_index", "from": 0, "size": size},
        )
        hits = ((response.get("hits") or {}).get("hits") or []) if isinstance(response.get("hits"), dict) else []
        return [hit.get("_source") for hit in hits if isinstance(hit, dict) and isinstance(hit.get("_source"), dict)]

    def get_table(self, entity_id: str) -> dict[str, Any]:
        if not _is_uuid(entity_id):
            raise OpenMetadataNotFoundError("Invalid OpenMetadata entity id")
        return self.get(f"/api/v1/tables/{entity_id}", params={"fields": _TABLE_FIELDS})

    def lineage(self, entity_id: str, depth: int) -> dict[str, Any]:
        if not _is_uuid(entity_id):
            raise OpenMetadataNotFoundError("Invalid OpenMetadata entity id")
        return self.get(
            f"/api/v1/lineage/table/{entity_id}",
            params={"upstreamDepth": depth, "downstreamDepth": depth},
        )

    def list_test_cases(self, entity_fqn: str, max_results: int = 100) -> dict[str, Any]:
        entity_fqn = str(entity_fqn or "").strip()
        if not entity_fqn:
            raise OpenMetadataNotFoundError("OpenMetadata table FQN is required")
        entity_link = f"<#E::table::{entity_fqn}>"
        result: list[dict[str, Any]] = []
        after = ""
        total: int | None = None
        max_results = max(1, min(1000, int(max_results)))
        while len(result) < max_results:
            params = {
                "entityLink": entity_link,
                "fields": "testDefinition,testSuite,testCaseResult",
                "limit": min(100, max_results - len(result)),
            }
            if after:
                params["after"] = after
            page = self.get("/api/v1/dataQuality/testCases", params=params)
            data = [item for item in page.get("data") or [] if isinstance(item, dict)]
            result.extend(data)
            paging = page.get("paging") if isinstance(page.get("paging"), dict) else {}
            if total is None:
                raw_total = paging.get("total")
                total = int(raw_total) if isinstance(raw_total, int) else None
            next_after = str(paging.get("after") or "")
            if not data or not next_after or next_after == after:
                break
            after = next_after
        return {
            "data": result[:max_results],
            "total": total if total is not None else len(result),
            "truncated": bool(total is not None and total > len(result[:max_results])),
        }

    def rdf_status(self) -> dict[str, Any]:
        return self.get("/api/v1/rdf/status")

    def knowledge_graph(self, entity_id: str, depth: int) -> dict[str, Any]:
        if not _is_uuid(entity_id):
            raise OpenMetadataNotFoundError("Invalid OpenMetadata entity id")
        return self.get(
            "/api/v1/rdf/graph/explore",
            params={"entityId": entity_id, "entityType": "table", "depth": max(1, min(3, int(depth)))},
        )


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(str(value))
        return True
    except (ValueError, TypeError, AttributeError):
        return False


class CatalogProjection:
    def __init__(self, config: OpenMetadataConfig, client: OpenMetadataClient):
        self.config = config
        self.client = client
        self._lock = threading.Lock()
        self._value: dict[str, Any] | None = None
        self._expires_at = 0.0

    def invalidate(self) -> None:
        with self._lock:
            self._expires_at = 0

    def get(self, *, force: bool = False) -> dict[str, Any]:
        now = time.monotonic()
        if not force and self._value is not None and now < self._expires_at:
            return self._value
        with self._lock:
            now = time.monotonic()
            if not force and self._value is not None and now < self._expires_at:
                return self._value
            value = self._load()
            self._value = value
            self._expires_at = now + self.config.cache_ttl_seconds
            return value

    def _load(self) -> dict[str, Any]:
        warnings: list[str] = []
        raw_tables = self.client.list_tables(self.config.max_entities)
        tables = [normalize_table(table, self.config.public_url) for table in raw_tables if not table.get("deleted")]
        counts: dict[str, int | None] = _table_counts(tables)
        for name, endpoint in _CAPABILITY_ENDPOINTS.items():
            if name == "tables":
                continue
            try:
                payload = self.client.get(endpoint, params={"limit": 1})
                paging = payload.get("paging") or {}
                counts[name] = int(paging.get("total", len(payload.get("data") or [])))
            except OpenMetadataError:
                counts[name] = None
                warnings.append(f"Не удалось проверить возможность: {name}")

        latest_epoch = max((int(table["updated_at_epoch"]) for table in tables if table.get("updated_at_epoch")), default=0)
        latest_iso = _epoch_to_iso(latest_epoch)
        age_hours = max(0.0, (time.time() * 1000 - latest_epoch) / 3_600_000) if latest_epoch else None
        stale = age_hours is None or age_hours > self.config.stale_after_hours
        truncated = len(raw_tables) >= self.config.max_entities
        checked_at = datetime.now(UTC).isoformat()
        if stale:
            warnings.append(
                (
                    f"Последнее изменение сущности OpenMetadata было {latest_iso}; это старше порога {self.config.stale_after_hours} ч. Проверьте ingestion."
                    if latest_iso
                    else "OpenMetadata не вернул дату изменения сущностей; проверьте ingestion."
                )
            )
        if truncated:
            warnings.append(f"Каталог ограничен первыми {self.config.max_entities} сущностями")
        return {
            "tables": tables,
            "counts": counts,
            "warnings": warnings,
            "freshness": {
                "snapshot_at": latest_iso,
                "latest_entity_updated_at": latest_iso,
                "age_hours": round(age_hours, 1) if age_hours is not None else None,
                "stale": stale,
                "threshold_hours": self.config.stale_after_hours,
                "checked_at": checked_at,
                "catalog_checked_at": checked_at,
            },
            "truncated": truncated,
        }


def _query_constraints(question: str, tables: list[dict[str, Any]]) -> dict[str, Any]:
    text = str(question or "")
    folded = text.casefold()
    constraints: dict[str, Any] = {}

    exact_fqns = sorted(
        (str(table.get("fqn") or "") for table in tables if table.get("fqn")),
        key=len,
        reverse=True,
    )
    exact_fqn = next((fqn for fqn in exact_fqns if fqn.casefold() in folded), "")
    if exact_fqn:
        constraints["fqn"] = exact_fqn

    domain_match = _DOMAIN_QUERY_PATTERN.search(text)
    if domain_match:
        requested_domain = domain_match.group(1).rstrip(".,?!:;»”")
        known_domains = {str(value).casefold() for table in tables for value in (table.get("domain_keys") or table.get("domains") or []) if value}
        if requested_domain.casefold() in known_domains:
            constraints["domain"] = requested_domain
    owner_match = _OWNER_QUERY_PATTERN.search(text)
    if owner_match:
        requested_owner = owner_match.group(1).rstrip(".,?!:;»”")
        known_owners = {str(value).casefold() for table in tables for value in (table.get("owner_keys") or table.get("owners") or []) if value}
        if requested_owner.casefold() in known_owners:
            constraints["owner"] = requested_owner

    column_markers = [(folded.rfind(marker), marker) for marker in _COLUMN_QUERY_MARKERS if marker in folded]
    if column_markers:
        marker_position, marker = max(column_markers, key=lambda item: item[0])
        column_query = text[marker_position + len(marker) :]
        known_columns = {str(column).casefold(): str(column) for table in tables for column in table.get("columns") or [] if column}
        requested_columns = _unique_strings([known_columns[token] for token in _tokens(column_query) if token in known_columns])
        if requested_columns:
            constraints["columns"] = requested_columns
    return constraints


def _matches_constraints(table: dict[str, Any], constraints: dict[str, Any] | None) -> bool:
    if not constraints:
        return True
    expected_fqn = str(constraints.get("fqn") or "").casefold()
    if expected_fqn and str(table.get("fqn") or "").casefold() != expected_fqn:
        return False
    for key, aliases_key in (("owner", "owner_keys"), ("domain", "domain_keys")):
        expected = str(constraints.get(key) or "").casefold()
        aliases = {str(value).casefold() for value in (table.get(aliases_key) or table.get(f"{key}s") or []) if value}
        if expected and expected not in aliases:
            return False
    expected_columns = {str(value).casefold() for value in constraints.get("columns") or [] if value}
    actual_columns = {str(value).casefold() for value in table.get("columns") or [] if value}
    return not expected_columns or expected_columns.issubset(actual_columns)


def _matches_filters(table: dict[str, Any], filters: dict[str, Any] | None, allowed_domains: set[str] | None) -> bool:
    if table.get("deleted"):
        return False
    table_domains = {str(value).casefold() for value in table.get("domain_keys") or table.get("domains") or []}
    if allowed_domains is not None and not table_domains.intersection(allowed_domains):
        return False
    if not filters:
        return True
    mappings = {
        "owner": table.get("owner_keys") or table.get("owners") or [],
        "domain": table.get("domain_keys") or table.get("domains") or [],
        "service": [table.get("service")],
        "tag": table.get("tags") or [],
    }
    for key, values in mappings.items():
        expected = filters.get(key)
        if expected and str(expected).casefold() not in {str(value).casefold() for value in values if value}:
            return False
    if filters.get("has_description") is True and not table.get("description"):
        return False
    if filters.get("has_description") is False and table.get("description"):
        return False
    return True


def _local_score(table: dict[str, Any], query: str) -> float:
    normalized_query = query.casefold().strip()
    query_tokens = set(_tokens(query))
    if not query_tokens:
        return 0
    name = str(table.get("technical_name") or table.get("name") or "").casefold()
    display_name = str(table.get("name") or "").casefold()
    fqn = str(table.get("fqn") or "").casefold()
    score = 0.0
    if normalized_query in {name, display_name, fqn}:
        score += 150
    if name and name in normalized_query:
        score += 80
    if fqn and fqn in normalized_query:
        score += 100
    fields = {
        "name": f"{name} {display_name}",
        "fqn": fqn,
        "description": str(table.get("description") or "").casefold(),
        "columns": " ".join(table.get("columns") or []).casefold(),
        "facets": " ".join(
            [
                str(table.get("service") or ""),
                str(table.get("schema") or ""),
                *(table.get("owner_keys") or table.get("owners") or []),
                *(table.get("domain_keys") or table.get("domains") or []),
                *(table.get("tags") or []),
            ]
        ).casefold(),
    }
    weights = {"name": 20, "fqn": 12, "description": 3, "columns": 12, "facets": 6}
    for field, text in fields.items():
        text_tokens = set(_tokens(text))
        score += weights[field] * len(query_tokens.intersection(text_tokens))
    return score


class DatasetRetrievalAgent:
    """Map untrusted Dataset chunks back to current, ACL-filtered OMD entities."""

    name = "dataset_retrieval"

    def __init__(self, service: "OpenMetadataCopilotService"):
        self.service = service

    @staticmethod
    def _metadata(hit: dict[str, Any]) -> dict[str, Any]:
        for key in ("metadata", "doc_metadata", "meta_fields"):
            value = hit.get(key)
            if isinstance(value, dict):
                return value
        return {}

    def resolve_hits(
        self,
        hits: list[dict[str, Any]] | None,
        *,
        candidates: list[dict[str, Any]],
    ) -> list[tuple[dict[str, Any], float]]:
        by_id = {table["id"]: table for table in candidates if table.get("id")}
        by_fqn = {table["fqn"].casefold(): table for table in candidates if table.get("fqn")}
        resolved: list[tuple[dict[str, Any], float]] = []
        seen: set[str] = set()
        for hit in hits or []:
            if not isinstance(hit, dict):
                continue
            metadata = self._metadata(hit)
            entity_id = str(metadata.get("omd_entity_id") or hit.get("omd_entity_id") or "")
            fqn = str(metadata.get("omd_fqn") or hit.get("omd_fqn") or "").casefold()
            table = by_id.get(entity_id) or by_fqn.get(fqn)
            if not table:
                # A deleted, stale, or unauthorized Dataset document must never
                # reintroduce an entity that is absent from the live projection.
                continue
            current_updated_at = table.get("updated_at_epoch")
            indexed_updated_at = metadata.get("omd_updated_at_epoch", hit.get("omd_updated_at_epoch"))
            if current_updated_at is not None:
                try:
                    if indexed_updated_at is None or int(indexed_updated_at) != int(current_updated_at):
                        continue
                except (TypeError, ValueError):
                    continue
            key = table["id"] or table["fqn"]
            if key in seen:
                continue
            seen.add(key)
            try:
                similarity = max(0.0, min(1.0, float(hit.get("similarity", 0.0))))
            except (TypeError, ValueError):
                similarity = 0.0
            resolved.append((table, similarity))
        return resolved


class DiscoveryAgent:
    name = "discovery"

    def __init__(self, service: "OpenMetadataCopilotService"):
        self.service = service

    def search(
        self,
        query: str,
        *,
        filters=None,
        limit: int | None = None,
        offset: int = 0,
        sort: str = "relevance",
        user_id: str = "",
        dataset_hits: list[dict[str, Any]] | None = None,
        dataset_warning: str | None = None,
        dataset_only: bool = False,
        locale: str = "ru",
    ) -> dict[str, Any]:
        query = (query or "").strip()
        if len(query) > 2000:
            raise ValueError(
                _localized(
                    locale,
                    ru="Вопрос слишком длинный; максимум 2000 символов",
                    en="The query is too long; the maximum is 2000 characters",
                )
            )
        if sort not in {"relevance", "updated_at", "fqn"}:
            raise ValueError("sort must be one of: relevance, updated_at, fqn")
        size = max(1, min(int(limit or self.service.config.max_results), self.service.config.max_results))
        offset = max(0, int(offset))
        projection = self.service.projection.get()
        allowed_domains = self.service.allowed_domains(user_id)
        visible_candidates = [table for table in projection["tables"] if _matches_filters(table, filters or {}, allowed_domains)]
        constraints = _query_constraints(query, visible_candidates)
        candidates = [table for table in visible_candidates if _matches_constraints(table, constraints)]
        warnings = list(projection["warnings"])
        if dataset_warning:
            warnings.append(dataset_warning)

        if not query:
            if sort == "updated_at":
                ordered = sorted(
                    candidates,
                    key=lambda table: (-(int(table.get("updated_at_epoch") or 0)), table["fqn"]),
                )
            else:
                ordered = sorted(candidates, key=lambda table: table["fqn"])
            results = []
            for table in ordered[offset : offset + size]:
                item = dict(table)
                item["score"] = 0.0
                item["matched_by"] = ["catalog_projection"]
                results.append(item)
            return {
                "entities": results,
                "total_matches": len(ordered),
                "total_visible_candidates": len(candidates),
                "limit": size,
                "offset": offset,
                "freshness": projection["freshness"],
                "warnings": warnings,
                "retrieval": "catalog_projection",
            }

        use_dataset_only = bool(dataset_only and dataset_hits is not None and not dataset_warning)
        local: list[tuple[dict[str, Any], float]] = []
        remote: list[dict[str, Any]] = []
        if not use_dataset_only:
            local = sorted(
                ((table, _local_score(table, query)) for table in candidates),
                key=lambda item: (-item[1], item[0]["fqn"]),
            )
            local = [(table, score) for table, score in local if score > 0]
            try:
                remote_raw = self.service.client.search_tables(query, min(100, self.service.config.max_results * 3))
                remote = [normalize_table(item, self.service.config.public_url) for item in remote_raw]
                remote = [item for item in remote if _matches_filters(item, filters or {}, allowed_domains) and _matches_constraints(item, constraints)]
            except OpenMetadataError:
                warnings.append(
                    _localized(
                        locale,
                        ru="OMD Search недоступен; использована локальная проекция каталога",
                        en="OMD Search is unavailable; the local catalog projection was used",
                    )
                )

        combined: dict[str, tuple[dict[str, Any], float, set[str]]] = {}
        for rank, table in enumerate(remote, start=1):
            key = table["id"] or table["fqn"]
            combined[key] = (table, 1 / (60 + rank), {"omd_search"})
        for rank, (table, score) in enumerate(local, start=1):
            key = table["id"] or table["fqn"]
            existing = combined.get(key)
            rrf = 1 / (60 + rank) + min(score, 200) / 20_000
            if existing:
                combined[key] = (existing[0], existing[1] + rrf, existing[2] | {"catalog_projection"})
            else:
                combined[key] = (table, rrf, {"catalog_projection"})
        for rank, (table, similarity) in enumerate(
            self.service.dataset_retrieval.resolve_hits(dataset_hits, candidates=candidates),
            start=1,
        ):
            key = table["id"] or table["fqn"]
            existing = combined.get(key)
            rrf = 1 / (60 + rank) + similarity / 100
            if existing:
                combined[key] = (existing[0], existing[1] + rrf, existing[2] | {"ragflow_dataset"})
            else:
                combined[key] = (table, rrf, {"ragflow_dataset"})
        ranked = sorted(combined.values(), key=lambda item: (-item[1], item[0]["fqn"]))
        if sort == "updated_at":
            ranked.sort(key=lambda item: (-(int(item[0].get("updated_at_epoch") or 0)), item[0]["fqn"]))
        elif sort == "fqn":
            ranked.sort(key=lambda item: item[0]["fqn"])
        total_matches = len(ranked)
        results = []
        for table, score, sources in ranked[offset : offset + size]:
            item = dict(table)
            item["score"] = round(score, 6)
            item["matched_by"] = sorted(sources)
            if constraints.get("columns"):
                item["matched_columns"] = list(constraints["columns"])
            results.append(item)
        return {
            "entities": results,
            "total_matches": total_matches,
            "total_visible_candidates": len(candidates),
            "limit": size,
            "offset": offset,
            "freshness": projection["freshness"],
            "warnings": warnings,
            "retrieval": "ragflow_dataset" if use_dataset_only else "omd_dataset_hybrid_rrf" if dataset_hits else "hybrid_rrf",
            "constraints": constraints,
        }

    def resolve(
        self,
        query: str,
        *,
        user_id: str = "",
        filters: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        result = self.search(query, limit=10, user_id=user_id, filters=filters)
        entities = result["entities"]
        if not entities:
            return None, []
        query_tokens = set(_tokens(query))
        exact_fqn = [entity for entity in entities if entity["fqn"].casefold() in query_tokens]
        if len(exact_fqn) == 1:
            return exact_fqn[0], []
        if len(exact_fqn) > 1:
            return None, exact_fqn
        exact_name = [entity for entity in entities if entity["technical_name"].casefold() in query_tokens]
        if len(exact_name) == 1:
            return exact_name[0], []
        if len(exact_name) > 1:
            return None, exact_name
        # Impact and quality answers must never be attached to a merely
        # top-ranked fuzzy result.  Let the caller render explicit candidates.
        return None, entities


class ImpactQualityAgent:
    name = "impact_quality"

    def __init__(self, service: "OpenMetadataCopilotService"):
        self.service = service

    def impact(
        self,
        query: str,
        *,
        depth: int = 2,
        user_id: str = "",
        locale: str = "ru",
        entity: dict[str, Any] | None = None,
        filters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        depth = max(1, min(3, int(depth)))
        ambiguous: list[dict[str, Any]] = []
        if entity is None:
            entity, ambiguous = self.service.discovery.resolve(query, user_id=user_id, filters=filters)
        projection = self.service.projection.get()
        if ambiguous:
            return {
                "needs_clarification": True,
                "clarification": _localized(
                    locale,
                    ru="Не удалось однозначно определить таблицу. Выберите нужную таблицу.",
                    en="The table could not be identified uniquely. Select the table you mean.",
                ),
                "entities": ambiguous,
                "freshness": projection["freshness"],
            }
        if not entity:
            return {
                "needs_clarification": True,
                "clarification": _localized(
                    locale,
                    ru="Не удалось определить таблицу для анализа влияния.",
                    en="The table for impact analysis could not be identified.",
                ),
                "entities": [],
                "freshness": projection["freshness"],
            }
        raw = self.service.client.lineage(entity["id"], depth)
        nodes_by_id: dict[str, dict[str, Any]] = {entity["id"]: entity}
        allowed_domains = self.service.allowed_domains(user_id)
        visible_lineage_ids: set[str] | None = None
        if allowed_domains is not None:
            visible_lineage_ids = {table["id"] for table in projection["tables"] if _matches_filters(table, {}, allowed_domains)}
        for node in raw.get("nodes") or []:
            if not isinstance(node, dict):
                continue
            normalized = {
                "id": str(node.get("id") or ""),
                "type": node.get("type") or "unknown",
                "name": node.get("displayName") or node.get("name") or "",
                "fqn": node.get("fullyQualifiedName") or node.get("name") or "",
                "description": node.get("description") or "",
                "deleted": bool(node.get("deleted")),
            }
            if visible_lineage_ids is not None and normalized["id"] not in visible_lineage_ids:
                continue
            nodes_by_id[normalized["id"]] = normalized
        upstream = self._edges(raw.get("upstreamEdges") or [], nodes_by_id, visible_lineage_ids)
        downstream = self._edges(raw.get("downstreamEdges") or [], nodes_by_id, visible_lineage_ids)
        foreign_keys = self._foreign_keys(entity, projection, allowed_domains, depth)
        knowledge_graph, graph_warnings = self._knowledge_graph(
            entity,
            depth,
            visible_lineage_ids,
            locale,
        )
        semantic_relations, semantic_truncated = self._semantic_relations(
            entity,
            projection,
            knowledge_graph,
            visible_lineage_ids,
        )
        warnings = [
            _localized(
                locale,
                ru="Показаны только связи, зарегистрированные в OpenMetadata; агент не достраивает их по названиям.",
                en="Only relationships registered in OpenMetadata are shown; none are inferred from names.",
            ),
            *graph_warnings,
        ]
        return {
            "entity": entity,
            "depth": depth,
            "nodes": list(nodes_by_id.values()),
            "upstream": upstream,
            "downstream": downstream,
            "foreign_keys": foreign_keys,
            "semantic_relations": semantic_relations,
            "semantic_relations_truncated": semantic_truncated,
            "knowledge_graph": knowledge_graph,
            "relationship_counts": {
                "lineage_upstream": len(upstream),
                "lineage_downstream": len(downstream),
                "foreign_key_outbound": sum(edge["from"]["id"] == entity["id"] for edge in foreign_keys),
                "foreign_key_inbound": sum(edge["to"]["id"] == entity["id"] for edge in foreign_keys),
                "semantic": len(semantic_relations),
                "knowledge_graph_edges": len(knowledge_graph.get("edges") or []),
            },
            "truncated": depth == 3 and bool(upstream or downstream),
            "freshness": projection["freshness"],
            "warnings": warnings,
        }

    @staticmethod
    def _edges(
        edges: list[Any],
        nodes: dict[str, dict[str, Any]],
        allowed_node_ids: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for edge in edges:
            if not isinstance(edge, dict):
                continue
            source = str(edge.get("fromEntity") or "")
            target = str(edge.get("toEntity") or "")
            key = (source, target)
            if not source or not target or key in seen:
                continue
            if allowed_node_ids is not None and (source not in allowed_node_ids or target not in allowed_node_ids):
                continue
            seen.add(key)
            details = edge.get("lineageDetails") if isinstance(edge.get("lineageDetails"), dict) else {}
            source_info = details.get("source") if isinstance(details.get("source"), dict) else {}
            column_lineage = []
            for mapping in details.get("columnsLineage") or []:
                if not isinstance(mapping, dict):
                    continue
                from_columns = _unique_strings([str(value) for value in mapping.get("fromColumns") or []])
                to_column = str(mapping.get("toColumn") or "").strip()
                if from_columns and to_column:
                    column_lineage.append({"from_columns": from_columns, "to_column": to_column})
            result.append(
                {
                    "from": nodes.get(source, {"id": source}),
                    "to": nodes.get(target, {"id": target}),
                    "relationship_type": "lineage",
                    "source": source_info.get("type") or source_info.get("name") or "manual_or_unspecified",
                    "pipeline": details.get("pipeline"),
                    "column_lineage": column_lineage,
                }
            )
        return result

    @staticmethod
    def _foreign_keys(
        entity: dict[str, Any],
        projection: dict[str, Any],
        allowed_domains: set[str] | None,
        depth: int,
    ) -> list[dict[str, Any]]:
        tables = [table for table in projection["tables"] if _matches_filters(table, {}, allowed_domains)]
        column_owner: dict[str, tuple[dict[str, Any], str]] = {}
        for table in tables:
            for column in table.get("column_details") or []:
                column_fqn = str(column.get("fqn") or "")
                if column_fqn:
                    column_owner[column_fqn.casefold()] = (table, str(column.get("name") or ""))

        all_edges: list[dict[str, Any]] = []
        seen: set[tuple[Any, ...]] = set()
        for source in tables:
            for constraint in source.get("table_constraints") or []:
                if constraint.get("constraint_type") != "FOREIGN_KEY":
                    continue
                targets: dict[str, dict[str, Any]] = {}
                for referred in constraint.get("referred_columns") or []:
                    resolved = column_owner.get(str(referred).casefold())
                    if not resolved:
                        continue
                    target, target_column = resolved
                    item = targets.setdefault(target["id"], {"table": target, "columns": []})
                    item["columns"].append(target_column or str(referred).rsplit(".", 1)[-1])
                for target in targets.values():
                    target_table = target["table"]
                    key = (
                        source["id"],
                        target_table["id"],
                        tuple(constraint.get("columns") or []),
                        tuple(target["columns"]),
                    )
                    if key in seen:
                        continue
                    seen.add(key)
                    all_edges.append(
                        {
                            "from": source,
                            "to": target_table,
                            "relationship_type": "foreign_key",
                            "source": "OpenMetadata table constraint",
                            "from_columns": list(constraint.get("columns") or []),
                            "to_columns": _unique_strings(target["columns"]),
                            "cardinality": constraint.get("relationship_type") or "",
                        }
                    )

        visited = {entity["id"]}
        selected: list[dict[str, Any]] = []
        for _hop in range(max(1, depth)):
            frontier: set[str] = set()
            for edge in all_edges:
                source_id = edge["from"]["id"]
                target_id = edge["to"]["id"]
                if source_id in visited or target_id in visited:
                    if edge not in selected:
                        selected.append(edge)
                    frontier.update((source_id, target_id))
            new_nodes = frontier - visited
            visited.update(frontier)
            if not new_nodes:
                break
        return sorted(selected, key=lambda edge: (edge["from"]["fqn"], edge["to"]["fqn"], edge["from_columns"]))

    def _knowledge_graph(
        self,
        entity: dict[str, Any],
        depth: int,
        visible_table_ids: set[str] | None,
        locale: str,
    ) -> tuple[dict[str, Any], list[str]]:
        try:
            raw = self.service.client.knowledge_graph(entity["id"], depth)
        except OpenMetadataError:
            return (
                {"nodes": [], "edges": [], "source": "OpenMetadata RDF"},
                [
                    _localized(
                        locale,
                        ru="RDF-граф OpenMetadata недоступен; показаны доступные FK и lineage.",
                        en="The OpenMetadata RDF graph is unavailable; available foreign keys and lineage are still shown.",
                    )
                ],
            )

        nodes_by_id: dict[str, dict[str, Any]] = {}
        raw_to_canonical: dict[str, str] = {}
        raw_nodes = raw.get("nodes") or []
        for node in raw_nodes:
            if not isinstance(node, dict):
                continue
            raw_id = str(node.get("id") or "").strip()
            entity_id = str(node.get("entityId") or "").strip()
            canonical_id = entity_id if _is_uuid(entity_id) else raw_id
            normalized = {
                "id": canonical_id,
                "type": str(node.get("type") or "unknown"),
                "name": str(node.get("label") or node.get("name") or ""),
                "fqn": str(node.get("fullyQualifiedName") or ""),
                "description": node.get("description") or "",
            }
            if not normalized["id"]:
                continue
            if raw_id:
                raw_to_canonical[raw_id] = canonical_id
            if normalized["type"].casefold() == "table" and visible_table_ids is not None and normalized["id"] not in visible_table_ids:
                continue
            nodes_by_id[canonical_id] = normalized
        node_ids = set(nodes_by_id)
        edges = []
        seen: set[tuple[str, str, str]] = set()
        for edge in raw.get("edges") or []:
            if not isinstance(edge, dict):
                continue
            source = raw_to_canonical.get(str(edge.get("from") or ""), str(edge.get("from") or ""))
            target = raw_to_canonical.get(str(edge.get("to") or ""), str(edge.get("to") or ""))
            label = str(edge.get("label") or "relatedTo")
            key = (source, target, label)
            if source not in node_ids or target not in node_ids or key in seen:
                continue
            seen.add(key)
            edges.append({"from": source, "to": target, "label": label})
        connected = {value for edge in edges for value in (edge["from"], edge["to"])}
        nodes = [node for node in nodes_by_id.values() if node["id"] in connected or node["id"] == entity["id"]]
        return {"nodes": nodes, "edges": edges, "source": "OpenMetadata RDF"}, []

    def _semantic_relations(
        self,
        entity: dict[str, Any],
        projection: dict[str, Any],
        graph: dict[str, Any],
        visible_table_ids: set[str] | None,
    ) -> tuple[list[dict[str, Any]], bool]:
        nodes = {node["id"]: node for node in graph.get("nodes") or [] if node.get("id")}
        table_ids = {node_id for node_id, node in nodes.items() if str(node.get("type") or "").casefold() == "table"}
        concept_ids = {node_id for node_id, node in nodes.items() if str(node.get("type") or "").casefold() in {"glossaryterm", "concept"}}
        mappings: dict[str, set[str]] = {}
        mapping_labels = {"mappedto", "hasglossaryterm", "glossaryterm"}
        for edge in graph.get("edges") or []:
            label = re.sub(r"[^a-z0-9]", "", str(edge.get("label") or "").casefold())
            if label not in mapping_labels:
                continue
            source = str(edge.get("from") or "")
            target = str(edge.get("to") or "")
            concept_id = source if source in concept_ids else target if target in concept_ids else ""
            table_id = source if source in table_ids else target if target in table_ids else ""
            if concept_id and table_id:
                mappings.setdefault(concept_id, set()).add(table_id)

        focus_id = entity["id"]
        by_table_id = {table["id"]: table for table in projection["tables"]}
        shared_by_table: dict[str, list[str]] = {}
        focus_terms = {str(term).casefold(): str(term) for term in entity.get("glossary_terms") or [] if str(term).strip()}
        for table_id, table in by_table_id.items():
            if table_id == focus_id or (visible_table_ids is not None and table_id not in visible_table_ids):
                continue
            for term in table.get("glossary_terms") or []:
                registered = focus_terms.get(str(term).casefold())
                if registered:
                    shared_by_table.setdefault(table_id, []).append(registered)
        for concept_id, mapped_tables in mappings.items():
            if focus_id not in mapped_tables:
                continue
            concept_name = nodes[concept_id].get("fqn") or nodes[concept_id].get("name") or concept_id
            for table_id in mapped_tables - {focus_id}:
                if table_id in by_table_id:
                    shared_by_table.setdefault(table_id, []).append(str(concept_name))

        relations = [
            {
                "from": entity,
                "to": by_table_id[table_id],
                "relationship_type": "semantic",
                "source": "OpenMetadata RDF glossary",
                "shared_terms": _unique_strings(terms),
            }
            for table_id, terms in shared_by_table.items()
        ]
        relations.sort(key=lambda edge: (-len(edge["shared_terms"]), edge["to"]["fqn"]))
        limit = min(100, self.service.config.max_results * 4)
        return relations[:limit], len(relations) > limit

    def quality(
        self,
        query: str = "",
        *,
        user_id: str = "",
        locale: str = "ru",
        entity: dict[str, Any] | None = None,
        filters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        projection = self.service.projection.get()
        ambiguous: list[dict[str, Any]] = []
        query_tokens = set(_tokens(query))
        if entity is None and query and any(table["technical_name"].casefold() in query_tokens or table["fqn"].casefold() in query_tokens for table in projection["tables"]):
            entity, ambiguous = self.service.discovery.resolve(query, user_id=user_id, filters=filters)
        if ambiguous:
            return {
                "needs_clarification": True,
                "clarification": _localized(
                    locale,
                    ru="Уточните таблицу для проверки качества.",
                    en="Select a table for the quality check.",
                ),
                "entities": ambiguous,
                "freshness": projection["freshness"],
            }
        test_cases: list[dict[str, Any]] = []
        truncated = False
        if entity:
            try:
                payload = self.service.client.list_test_cases(
                    entity["fqn"],
                    max_results=min(100, self.service.config.max_results * 4),
                )
                entity_link_prefix = f"<#E::table::{entity['fqn']}"
                test_cases = [
                    _normalize_test_case(item)
                    for item in payload.get("data") or []
                    if str(item.get("entityLink") or "") == f"{entity_link_prefix}>" or str(item.get("entityLink") or "").startswith(f"{entity_link_prefix}::columns::")
                ]
                total = int(payload.get("total", len(test_cases)))
                truncated = bool(payload.get("truncated"))
            except OpenMetadataError:
                total = None
        else:
            total = self.service.capabilities_for_user(user_id).get("test_cases")

        if total == 0:
            status = "not_configured"
            message = _localized(
                locale,
                ru=(
                    f"Для {entity['fqn']} не настроено ни одного test case; это не означает, что данные качественные."
                    if entity
                    else "В OpenMetadata не настроено ни одного test case; это не означает, что данные качественные."
                ),
                en=(
                    f"No test cases are configured for {entity['fqn']}; this does not mean the data is healthy."
                    if entity
                    else "No test cases are configured in OpenMetadata; this does not mean the data is healthy."
                ),
            )
        elif total is None:
            status = "unknown"
            message = _localized(
                locale,
                ru=(f"Проверки качества для {entity['fqn']} получить не удалось." if entity else "Состояние проверок качества получить не удалось."),
                en=(f"Quality tests for {entity['fqn']} could not be retrieved." if entity else "The quality-test status could not be retrieved."),
            )
        else:
            status = "configured"
            message = _localized(
                locale,
                ru=(f"Для {entity['fqn']} настроено test cases: {total}." if entity else f"В OpenMetadata настроено тестов качества: {total}."),
                en=(f"{entity['fqn']} has {total} configured test cases." if entity else f"OpenMetadata has {total} configured quality tests."),
            )
        return {
            "status": status,
            "message": message,
            "test_case_count": total,
            "test_cases": test_cases,
            "truncated": truncated,
            "entity": entity,
            "freshness": projection["freshness"],
        }


class StarterQuestionAgent:
    name = "starter_questions"

    def __init__(self, service: "OpenMetadataCopilotService"):
        self.service = service

    def _relationship_candidate(self, tables: list[dict[str, Any]]) -> dict[str, Any] | None:
        visible_ids = {table["id"] for table in tables if table.get("id")}
        column_owner = {str(column.get("fqn") or "").casefold(): table for table in tables for column in table.get("column_details") or [] if column.get("fqn")}
        for table in tables:
            if any(
                column_owner.get(str(referred).casefold()) is not None
                for constraint in table.get("table_constraints") or []
                if constraint.get("constraint_type") == "FOREIGN_KEY"
                for referred in constraint.get("referred_columns") or []
            ):
                return table

        terms_to_tables: dict[str, set[str]] = {}
        for table in tables:
            for term in table.get("glossary_terms") or []:
                terms_to_tables.setdefault(str(term).casefold(), set()).add(table["id"])
        shared_terms = {term for term, table_ids in terms_to_tables.items() if len(table_ids) > 1}
        semantic_candidate = next(
            (table for table in tables if any(str(term).casefold() in shared_terms for term in table.get("glossary_terms") or [])),
            None,
        )
        if semantic_candidate:
            return semantic_candidate

        for table in (table for table in tables if table.get("processed_lineage")):
            try:
                raw = self.service.client.lineage(table["id"], 1)
            except OpenMetadataError:
                continue
            for edge in [*(raw.get("upstreamEdges") or []), *(raw.get("downstreamEdges") or [])]:
                if not isinstance(edge, dict):
                    continue
                source = str(edge.get("fromEntity") or "")
                target = str(edge.get("toEntity") or "")
                if source in visible_ids and target in visible_ids:
                    return table
        return None

    def generate(self, *, user_id: str = "", locale: str = "ru", question: str = "") -> dict[str, Any]:
        projection = self.service.projection.get()
        tables = [table for table in projection["tables"] if _matches_filters(table, {}, self.service.allowed_domains(user_id))]
        constraints = _query_constraints(question, tables)
        scope_constraints = {key: value for key, value in constraints.items() if key in {"fqn", "owner", "domain"}}
        if scope_constraints:
            tables = [table for table in tables if _matches_constraints(table, scope_constraints)]
        counts = self.service.capabilities_for_user(user_id)
        language = _language(locale)
        prefix = "As of the latest snapshot" if language == "en" else "По последнему снимку"
        snapshot = projection["freshness"].get("snapshot_at")
        if snapshot:
            try:
                date_label = datetime.fromisoformat(snapshot).strftime("%Y-%m-%d" if language == "en" else "%d.%m.%Y")
                prefix = f"As of snapshot {date_label}" if language == "en" else f"По снимку от {date_label}"
            except ValueError:
                pass
        questions: list[dict[str, Any]] = []
        missing_descriptions = sum(not table["description"] for table in tables)
        if missing_descriptions:
            questions.append(
                {
                    "id": "missing-descriptions",
                    "agent": "discovery",
                    "question": (f"{prefix}, which tables are missing descriptions?" if language == "en" else f"{prefix}: какие таблицы не имеют описания?"),
                    "reason": (f"Missing descriptions: {missing_descriptions}" if language == "en" else f"Без описания: {missing_descriptions}"),
                    "action": {"type": "missing_descriptions"},
                }
            )
        domains: dict[str, int] = {}
        for table in tables:
            for domain in table["domains"]:
                domains[domain] = domains.get(domain, 0) + 1
        if domains:
            top_domain = sorted(domains.items(), key=lambda item: (-item[1], item[0]))[0][0]
            questions.append(
                {
                    "id": "top-domain",
                    "agent": "discovery",
                    "question": (f"Show the key tables in the {top_domain} domain" if language == "en" else f"Покажи ключевые таблицы домена {top_domain}"),
                    "reason": ("The most represented accessible domain" if language == "en" else "Самый представленный доступный домен"),
                    "action": {"type": "domain", "domain": top_domain},
                }
            )
        related_table = self._relationship_candidate(tables)
        if related_table:
            questions.append(
                {
                    "id": "relationships",
                    "agent": "impact_quality",
                    "question": (
                        f"Show all registered relationships for the table {related_table['fqn']}" if language == "en" else f"Покажи все зарегистрированные связи таблицы {related_table['fqn']}"
                    ),
                    "reason": (
                        "OpenMetadata has structural, semantic, or lineage evidence for this table"
                        if language == "en"
                        else "Для таблицы есть структурные, семантические или lineage-связи OpenMetadata"
                    ),
                    "action": {"type": "impact", "entity_id": related_table["id"]},
                }
            )
        if any(table.get("updated_at_epoch") for table in tables):
            questions.append(
                {
                    "id": "recent",
                    "agent": "discovery",
                    "question": (f"{prefix}, which tables were updated most recently?" if language == "en" else f"{prefix}: какие таблицы обновлялись последними?"),
                    "reason": "Review catalog freshness" if language == "en" else "Проверка актуальности каталога",
                    "action": {"type": "recent"},
                }
            )
        if counts.get("test_cases") == 0:
            questions.append(
                {
                    "id": "quality-gap",
                    "agent": "impact_quality",
                    "question": ("Are data quality checks configured in the catalog?" if language == "en" else "Настроены ли в каталоге проверки качества данных?"),
                    "reason": "No test cases are configured" if language == "en" else "Сейчас test cases отсутствуют",
                    "action": {"type": "quality"},
                }
            )
        return {
            "questions": questions[:5],
            "freshness": projection["freshness"],
            "capabilities": counts,
            "warnings": projection["warnings"],
            "constraints": scope_constraints,
        }


class GovernanceAgent:
    name = "governance"

    _DISPLAY_NAME_PATTERN = re.compile(
        r"display\s*name\s*(?:=|:|to)?\s*[\"'«“]([^\"'»”]+)[\"'»”]",
        re.IGNORECASE,
    )
    _DESCRIPTION_PATTERN = re.compile(r"(?:описани[ея]|description)\s*:?[ \t]*(.+)$", re.IGNORECASE)

    def __init__(self, service: "OpenMetadataCopilotService", secret_key: str):
        self.service = service
        self._signer = URLSafeTimedSerializer(secret_key=secret_key, salt="ragflow-openmetadata-governance-v1")

    @classmethod
    def requested_changes(cls, question: str) -> dict[str, str]:
        changes: dict[str, str] = {}
        display_name = cls._DISPLAY_NAME_PATTERN.search(question or "")
        if display_name:
            changes["displayName"] = display_name.group(1).strip()
        description = cls._DESCRIPTION_PATTERN.search(question or "")
        if description and "без описан" not in (question or "").casefold():
            value = description.group(1).strip().strip("\"'«»“”")
            if value:
                changes["description"] = value
        return changes

    def preview(self, *, user_id: str, entity_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        if not self.service.config.write_enabled:
            raise OpenMetadataPermissionError("Изменения OpenMetadata отключены конфигурацией")
        if not isinstance(changes, dict) or not changes:
            raise ValueError("Не переданы изменения")
        unknown = set(changes) - _WRITE_FIELDS
        if unknown:
            raise ValueError(f"Поля не разрешены: {', '.join(sorted(unknown))}")
        entity = self.service.client.get_table(entity_id)
        patch: list[dict[str, Any]] = [{"op": "test", "path": "/version", "value": entity.get("version")}]
        diff: list[dict[str, Any]] = []
        normalized_changes: dict[str, Any] = {}
        for field, new_value in changes.items():
            if new_value is not None and not isinstance(new_value, str):
                raise ValueError(f"Поле {field} должно быть строкой или null")
            if isinstance(new_value, str) and len(new_value) > 10_000:
                raise ValueError(f"Поле {field} слишком длинное")
            old_value = entity.get(field)
            if old_value == new_value:
                continue
            if new_value is None:
                patch.append({"op": "remove", "path": f"/{field}"})
            elif field in entity:
                patch.append({"op": "replace", "path": f"/{field}", "value": new_value})
            else:
                patch.append({"op": "add", "path": f"/{field}", "value": new_value})
            diff.append({"field": field, "before": old_value, "after": new_value})
            normalized_changes[field] = new_value
        if not diff:
            raise ValueError("Изменения совпадают с текущими значениями")
        nonce = uuid.uuid4().hex
        payload = {
            "user_id": user_id,
            "entity_id": entity_id,
            "entity_fqn": entity.get("fullyQualifiedName") or entity.get("name"),
            "expected_version": entity.get("version"),
            "changes": normalized_changes,
            "patch": patch,
            "nonce": nonce,
            "patch_hash": hashlib.sha256(json.dumps(patch, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest(),
        }
        token = self._signer.dumps(payload)
        self._audit(user_id, entity_id, "previewed", normalized_changes.keys())
        return {
            "entity": normalize_table(entity, self.service.config.public_url),
            "diff": diff,
            "confirmation_token": token,
            "expires_in_seconds": self.service.config.confirmation_ttl_seconds,
            "write_enabled": True,
        }

    def confirm(self, *, user_id: str, confirmation_token: str) -> dict[str, Any]:
        if not self.service.config.write_enabled:
            raise OpenMetadataPermissionError("Изменения OpenMetadata отключены конфигурацией")
        try:
            payload = self._signer.loads(confirmation_token, max_age=self.service.config.confirmation_ttl_seconds)
        except SignatureExpired as exc:
            raise OpenMetadataConflictError("Подтверждение истекло; сформируйте новый preview") from exc
        except BadSignature as exc:
            raise OpenMetadataPermissionError("Подтверждение недействительно") from exc
        if not isinstance(payload, dict) or payload.get("user_id") != user_id:
            raise OpenMetadataPermissionError("Подтверждение принадлежит другому пользователю")
        patch = payload.get("patch")
        expected_hash = hashlib.sha256(json.dumps(patch, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
        if expected_hash != payload.get("patch_hash"):
            raise OpenMetadataPermissionError("Содержимое подтверждения повреждено")
        entity_id = str(payload.get("entity_id") or "")
        if not self._consume_nonce(str(payload.get("nonce") or "")):
            self._audit(user_id, entity_id, "replay_rejected", (payload.get("changes") or {}).keys())
            raise OpenMetadataConflictError("Это подтверждение уже использовано")
        current = self.service.client.get_table(entity_id)
        if current.get("version") != payload.get("expected_version"):
            self._audit(user_id, entity_id, "version_conflict", (payload.get("changes") or {}).keys())
            raise OpenMetadataConflictError("Сущность изменилась после preview; подтвердите новый diff")
        updated = self.service.client.patch(f"/api/v1/tables/{entity_id}", patch)
        verified = self.service.client.get_table(entity_id)
        for field, value in (payload.get("changes") or {}).items():
            if verified.get(field) != value:
                self._audit(user_id, entity_id, "verification_failed", (payload.get("changes") or {}).keys())
                raise OpenMetadataConflictError("OpenMetadata не подтвердил применённое изменение")
        self.service.projection.invalidate()
        self._audit(user_id, entity_id, "applied", (payload.get("changes") or {}).keys())
        return {
            "entity": normalize_table(verified or updated, self.service.config.public_url),
            "applied": True,
            "fields": sorted((payload.get("changes") or {}).keys()),
        }

    @staticmethod
    def _consume_nonce(nonce: str) -> bool:
        if not nonce:
            return False
        try:
            from rag.utils.redis_conn import REDIS_CONN

            return bool(REDIS_CONN.set_if_absent(f"openmetadata:governance:used:{nonce}", "1", 86_400))
        except Exception:
            LOGGER.exception("OpenMetadata governance nonce store unavailable")
            return False

    @staticmethod
    def _audit(user_id: str, entity_id: str, status: str, fields: Any) -> None:
        LOGGER.info(
            "openmetadata_governance user_id=%s entity_id=%s status=%s fields=%s",
            user_id,
            entity_id,
            status,
            ",".join(sorted(str(field) for field in fields)),
        )


class CatalogCopilotAgent:
    name = "catalog_copilot"

    _IMPACT_MARKERS = (
        "lineage",
        "upstream",
        "downstream",
        "depend",
        "related",
        "relationship",
        "foreign key",
        "join",
        "завис",
        "влия",
        "источник",
        "потомк",
        "связ",
        "внешн ключ",
        "джойн",
    )
    _QUALITY_MARKERS = ("quality", "test case", "тест", "качеств", "провер")
    _GOVERNANCE_MARKERS = (
        "измени",
        "назнач",
        "обнови",
        "добавь",
        "установи",
        "задай",
        "поменяй",
        "удали",
        "переимен",
        "change",
        "set ",
        "update",
    )
    _CATALOG_MARKERS = ("сколько", "статист", "покрыти", "состояние каталога", "возможност", "capabilit")

    def __init__(self, service: "OpenMetadataCopilotService"):
        self.service = service

    def classify(self, question: str) -> str:
        text = question.casefold()
        if any(marker in text for marker in _RECENT_MARKERS) and ("обнов" in text or "updated" in text):
            return "discovery"
        if any(marker in text for marker in self._GOVERNANCE_MARKERS):
            return "governance"
        if any(marker in text for marker in self._IMPACT_MARKERS):
            return "impact"
        if any(marker in text for marker in self._QUALITY_MARKERS):
            return "quality"
        if any(marker in text for marker in self._CATALOG_MARKERS):
            return "catalog"
        return "discovery"

    def run(
        self,
        question: str,
        *,
        user_id: str = "",
        filters=None,
        depth: int = 2,
        dataset_hits: list[dict[str, Any]] | None = None,
        dataset_warning: str | None = None,
        context: list[dict[str, Any]] | None = None,
        selected_entity_id: str = "",
        action: dict[str, Any] | None = None,
        locale: str = "ru",
        forced_intent: str = "",
        agent_name: str = "",
        dataset_only: bool = False,
    ) -> dict[str, Any]:
        question = (question or "").strip()
        if not question:
            raise ValueError(_localized(locale, ru="Введите вопрос по каталогу", en="Enter a catalog question"))
        if len(question) > 2000:
            raise ValueError(
                _localized(
                    locale,
                    ru="Вопрос слишком длинный; максимум 2000 символов",
                    en="The question is too long; the maximum is 2000 characters",
                )
            )
        if action is not None and not isinstance(action, dict):
            raise ValueError("action must be an object")
        action_type = str((action or {}).get("type") or "")
        if action_type and action_type not in _STARTER_ACTIONS:
            raise ValueError("Unsupported catalog action")
        if forced_intent and forced_intent not in {"impact", "quality", "catalog", "governance", "discovery"}:
            raise ValueError("Unsupported forced OpenMetadata intent")
        intent = {
            "impact": "impact",
            "quality": "quality",
            "missing_descriptions": "discovery",
            "domain": "discovery",
            "recent": "discovery",
        }.get(action_type, forced_intent or self.classify(question))
        projection = self.service.projection.get()
        base = {
            "agent": agent_name or self.name,
            "intent": intent,
            "question": question,
            "freshness": projection["freshness"],
            "warnings": list(projection["warnings"]),
            "sources": [{"label": "OpenMetadata", "url": self.service.config.public_url}],
        }
        if dataset_hits is not None and intent in {"discovery", "governance"}:
            base["sources"].append({"label": "RAGFlow Dataset", "dataset_id": self.service.config.dataset_id})
        if dataset_warning:
            base["warnings"].append(dataset_warning)

        selected_entity = self._visible_entity(selected_entity_id, user_id=user_id, filters=filters) if selected_entity_id else None
        if selected_entity_id and not selected_entity:
            raise OpenMetadataNotFoundError(
                _localized(
                    locale,
                    ru="Выбранная таблица недоступна в текущей области каталога",
                    en="The selected table is not available in the current catalog scope",
                )
            )
        context_entities = self._context_entities(context, user_id=user_id, filters=filters)
        references_context = self._references_context(question)

        if action_type in {"missing_descriptions", "domain", "recent"}:
            action_filters = dict(filters or {})
            sort = "fqn"
            if action_type == "missing_descriptions":
                action_filters["has_description"] = False
            elif action_type == "domain":
                domain = str((action or {}).get("domain") or "").strip()
                if not domain or len(domain) > 500:
                    raise ValueError("Catalog domain action requires a valid domain")
                action_filters["domain"] = domain
            else:
                sort = "updated_at"
            discovery = self.service.discovery.search(
                "",
                filters=action_filters,
                user_id=user_id,
                sort=sort,
                locale=locale,
            )
            base.update(discovery)
            base["answer"] = (
                self._recent_answer(discovery["entities"], discovery["total_matches"], locale)
                if action_type == "recent"
                else self._discovery_answer(
                    discovery["entities"],
                    projection["freshness"],
                    locale,
                    total=discovery["total_matches"],
                )
            )
            return base

        if intent == "impact":
            target = selected_entity or self._action_entity(action, user_id=user_id, filters=filters)
            if not target and references_context:
                if len(context_entities) == 1:
                    target = context_entities[0]
                elif len(context_entities) > 1:
                    base.update(
                        {
                            "needs_clarification": True,
                            "answer": _localized(
                                locale,
                                ru="Выберите таблицу для анализа влияния.",
                                en="Select a table for impact analysis.",
                            ),
                            "entities": context_entities,
                            "context_applied": True,
                        }
                    )
                    return base
            impact = self.service.impact_quality.impact(
                target["fqn"] if target else question,
                depth=depth,
                user_id=user_id,
                locale=locale,
                entity=target,
                filters=filters,
            )
            impact_warnings = list(impact.pop("warnings", []))
            base.update(impact)
            base["warnings"].extend(warning for warning in impact_warnings if warning not in base["warnings"])
            if impact.get("needs_clarification"):
                base["answer"] = impact["clarification"]
            else:
                base["answer"] = self._impact_answer(impact, locale)
            return base
        if intent == "quality":
            target = selected_entity or self._action_entity(action, user_id=user_id, filters=filters)
            if not target and references_context and len(context_entities) == 1:
                target = context_entities[0]
            quality = self.service.impact_quality.quality(
                target["fqn"] if target else question,
                user_id=user_id,
                locale=locale,
                entity=target,
                filters=filters,
            )
            base["quality"] = quality
            base["answer"] = quality.get("message") or quality.get("clarification")
            base["entities"] = quality.get("entities") or ([quality["entity"]] if quality.get("entity") else [])
            return base
        if intent == "catalog" and references_context and context_entities:
            contextual = list(context_entities)[: self.service.config.max_results]
            base.update(
                {
                    "entities": contextual,
                    "total_matches": len(context_entities),
                    "context_applied": True,
                    "retrieval": "conversation_context",
                }
            )
            base["answer"] = self._context_answer(question, contextual, projection["freshness"], locale)
            return base
        if intent == "catalog":
            scoped_projection = dict(projection)
            scoped_projection["counts"] = self.service.capabilities_for_user(user_id)
            base["capabilities"] = scoped_projection["counts"]
            base["answer"] = self._catalog_answer(scoped_projection, locale)
            base["entities"] = []
            return base
        if intent == "governance":
            discovery = self.service.discovery.search(
                question,
                user_id=user_id,
                limit=5,
                dataset_hits=dataset_hits,
                dataset_warning=dataset_warning,
                locale=locale,
            )
            base.update(discovery)
            entities = discovery["entities"]
            changes = self.service.governance.requested_changes(question)
            if not entities:
                base["needs_clarification"] = True
                base["answer"] = _localized(
                    locale,
                    ru="Не удалось найти таблицу для изменения. Укажите её точный FQN.",
                    en="The table to change was not found. Provide its exact FQN.",
                )
            elif len(entities) > 1 and not discovery.get("constraints", {}).get("fqn"):
                base["needs_clarification"] = True
                base["answer"] = _localized(
                    locale,
                    ru="Найдено несколько таблиц. Выберите одну сущность перед Governance preview.",
                    en="Several tables matched. Select one entity before opening the Governance preview.",
                )
            else:
                entity = entities[0]
                base["entities"] = [entity]
                base["governance_request"] = {
                    "entity_id": entity["id"],
                    "changes": changes,
                    "preview_endpoint": "/api/v1/openmetadata/governance/preview",
                }
                change_fields = ", ".join(changes) if changes else _localized(locale, ru="не определены", en="not detected")
                base["answer"] = _localized(
                    locale,
                    ru=(f"Подготовлено изменение для {entity['fqn']}; поля: {change_fields}. Запись не выполнена. Откройте форму Governance, проверьте diff и подтвердите preview."),
                    en=(f"A change was prepared for {entity['fqn']}; fields: {change_fields}. Nothing was written. Open Governance, review the diff, and confirm the preview."),
                )
            base["write_enabled"] = self.service.config.write_enabled
            return base

        if selected_entity:
            base.update({"entities": [selected_entity], "total_matches": 1})
            base["answer"] = self._discovery_answer([selected_entity], projection["freshness"], locale, total=1)
            return base

        if references_context and context_entities:
            contextual = list(context_entities)
            text = question.casefold()
            if any(marker in text for marker in _MISSING_DESCRIPTION_MARKERS):
                contextual = [entity for entity in contextual if not entity.get("description")]
            elif any(marker in text for marker in _WITH_DESCRIPTION_MARKERS):
                contextual = [entity for entity in contextual if entity.get("description")]
            if any(marker in text for marker in _RECENT_MARKERS):
                contextual.sort(key=lambda entity: (-(int(entity.get("updated_at_epoch") or 0)), entity["fqn"]))
            total = len(contextual)
            contextual = contextual[: self.service.config.max_results]
            base.update(
                {
                    "entities": contextual,
                    "total_matches": total,
                    "context_applied": True,
                    "retrieval": "conversation_context",
                }
            )
            base["answer"] = self._context_answer(question, contextual, projection["freshness"], locale, total=total)
            return base

        text = question.casefold()
        structured_filters = dict(filters or {})
        structured_sort = "relevance"
        structured_query = question
        if any(marker in text for marker in _MISSING_DESCRIPTION_MARKERS):
            structured_filters["has_description"] = False
            structured_query = ""
            structured_sort = "fqn"
        elif any(marker in text for marker in _RECENT_MARKERS) and ("обнов" in text or "updated" in text):
            structured_query = ""
            structured_sort = "updated_at"
        discovery = self.service.discovery.search(
            structured_query,
            filters=structured_filters,
            user_id=user_id,
            dataset_hits=dataset_hits,
            dataset_warning=dataset_warning,
            dataset_only=dataset_only,
            sort=structured_sort,
            locale=locale,
        )
        base.update(discovery)
        entities = discovery["entities"]
        base["answer"] = (
            self._recent_answer(entities, discovery["total_matches"], locale)
            if structured_sort == "updated_at"
            else self._discovery_answer(
                entities,
                projection["freshness"],
                locale,
                total=discovery["total_matches"],
            )
        )
        return base

    def _visible_entity(self, entity_id: str, *, user_id: str, filters=None) -> dict[str, Any] | None:
        return self.service.get_visible_entity(entity_id, user_id=user_id, filters=filters)

    def _action_entity(self, action: dict[str, Any] | None, *, user_id: str, filters=None) -> dict[str, Any] | None:
        entity_id = str((action or {}).get("entity_id") or "")
        if not entity_id:
            return None
        entity = self._visible_entity(entity_id, user_id=user_id, filters=filters)
        if not entity:
            raise OpenMetadataNotFoundError("Catalog action entity is not available")
        return entity

    def _context_entities(self, context: list[dict[str, Any]] | None, *, user_id: str, filters=None) -> list[dict[str, Any]]:
        if context is None:
            return []
        if not isinstance(context, list):
            raise ValueError("context must be an array")
        turns: list[list[str]] = []
        for turn in context[-8:]:
            if not isinstance(turn, dict):
                raise ValueError("Each context turn must be an object")
            values = turn.get("entity_ids") or []
            if not isinstance(values, list):
                raise ValueError("context entity_ids must be an array")
            turns.append(list(dict.fromkeys(str(value) for value in values[: self.service.config.max_results] if value)))
        entity_ids = next((values for values in reversed(turns) if values), [])
        if not entity_ids:
            return []
        wanted = set(entity_ids)
        allowed_domains = self.service.allowed_domains(user_id)
        visible_by_id = {entity["id"]: entity for entity in self.service.projection.get()["tables"] if entity["id"] in wanted and _matches_filters(entity, filters or {}, allowed_domains)}
        return [visible_by_id[entity_id] for entity_id in entity_ids if entity_id in visible_by_id]

    @staticmethod
    def _references_context(question: str) -> bool:
        text = question.casefold()
        return any(marker in text for marker in _CONTEXT_REFERENCE_MARKERS) or any(pattern.search(question) for pattern in _CONTEXT_REFERENCE_PATTERNS)

    @classmethod
    def _context_answer(
        cls,
        question: str,
        entities: list[dict[str, Any]],
        freshness: dict[str, Any],
        locale: str,
        *,
        total: int | None = None,
    ) -> str:
        if len(entities) != 1:
            return cls._discovery_answer(entities, freshness, locale, total=total)

        entity = entities[0]
        text = question.casefold()
        wants_description = any(marker in text for marker in _DESCRIPTION_QUERY_MARKERS)
        wants_column_count = any(pattern.search(question) for pattern in _COLUMN_COUNT_QUERY_PATTERNS)
        if not wants_description and not wants_column_count:
            return cls._discovery_answer(entities, freshness, locale, total=total)

        def sentence(label: str, value: Any) -> str:
            detail = f"{label}: {value}"
            return detail if detail.endswith((".", "!", "?")) else f"{detail}."

        details = []
        if _language(locale) == "en":
            if wants_description:
                details.append(sentence("Description", entity.get("description") or "not set"))
            if wants_column_count:
                details.append(sentence("Columns", int(entity.get("column_count") or 0)))
        else:
            if wants_description:
                details.append(sentence("Описание", entity.get("description") or "не задано"))
            if wants_column_count:
                details.append(sentence("Колонок", int(entity.get("column_count") or 0)))
        return f"{entity['fqn']}. {' '.join(details)}"

    @staticmethod
    def _catalog_answer(projection: dict[str, Any], locale: str) -> str:
        counts = projection["counts"]
        prefix = "As of the latest snapshot" if _language(locale) == "en" else "По последнему снимку"
        if projection["freshness"].get("snapshot_at"):
            prefix += f" dated {projection['freshness']['snapshot_at'][:10]}" if _language(locale) == "en" else f" от {projection['freshness']['snapshot_at'][:10]}"
        tests = counts.get("test_cases")
        return _localized(
            locale,
            ru=(
                f"{prefix}: таблиц — {counts.get('tables', 0)}, колонок — {counts.get('columns', 0)}, "
                f"таблиц с описанием — {counts.get('described_tables', 0)}, с владельцем — {counts.get('owned_tables', 0)}, "
                f"test cases — {tests if tests is not None else 'не удалось проверить'}."
            ),
            en=(
                f"{prefix}: {counts.get('tables', 0)} tables, {counts.get('columns', 0)} columns, "
                f"{counts.get('described_tables', 0)} tables with descriptions, {counts.get('owned_tables', 0)} with owners, "
                f"and {tests if tests is not None else 'an unknown number of'} test cases."
            ),
        )

    @staticmethod
    def _discovery_answer(
        entities: list[dict[str, Any]],
        freshness: dict[str, Any],
        locale: str,
        *,
        total: int | None = None,
    ) -> str:
        if not entities:
            return _localized(
                locale,
                ru="По доступной части каталога ничего не найдено. Уточните имя, сервис, домен или тег.",
                en="Nothing was found in the accessible catalog. Refine the name, service, domain, or tag.",
            )
        total = len(entities) if total is None else total
        prefix = (
            ("In the latest snapshot" if freshness.get("stale") else "In the current catalog projection")
            if _language(locale) == "en"
            else ("По последнему снимку" if freshness.get("stale") else "В актуальной проекции каталога")
        )
        if total == 1 and len(entities) == 1:
            entity = entities[0]
            owners = ", ".join(entity.get("owners") or []) or "—"
            domains = ", ".join(entity.get("domains") or []) or "—"
            tags = ", ".join(entity.get("tags") or []) or "—"
            matched_columns = ", ".join(entity.get("matched_columns") or [])
            columns_ru = f" Совпавшие колонки: {matched_columns}." if matched_columns else ""
            columns_en = f" Matched columns: {matched_columns}." if matched_columns else ""
            return _localized(
                locale,
                ru=(f"{prefix} найдена точная таблица {entity['fqn']}. Владелец: {owners}. Домен: {domains}. Теги: {tags}.{columns_ru}"),
                en=(f"{prefix}, the exact table is {entity['fqn']}. Owner: {owners}. Domain: {domains}. Tags: {tags}.{columns_en}"),
            )
        names = ", ".join(entity["fqn"] for entity in entities[:5])
        remaining = max(0, total - min(5, len(entities)))
        return _localized(
            locale,
            ru=f"{prefix} найдено {total}: {names}." + (f" Ещё результатов: {remaining}." if remaining else ""),
            en=f"{prefix}, {total} tables were found: {names}." + (f" {remaining} more results." if remaining else ""),
        )

    @staticmethod
    def _recent_answer(entities: list[dict[str, Any]], total: int, locale: str) -> str:
        if not entities:
            return _localized(
                locale,
                ru="В доступной части каталога нет таблиц с датой обновления.",
                en="No tables with an update timestamp are available in the accessible catalog.",
            )
        names = ", ".join(entity["fqn"] for entity in entities[:5])
        return _localized(
            locale,
            ru=f"Последними обновлялись: {names}. Всего таблиц в выборке: {total}.",
            en=f"The most recently updated tables are: {names}. The result set contains {total} tables.",
        )

    @staticmethod
    def _impact_answer(impact: dict[str, Any], locale: str) -> str:
        entity = impact["entity"]
        counts = impact.get("relationship_counts") or {}
        upstream = int(counts.get("lineage_upstream") or 0)
        downstream = int(counts.get("lineage_downstream") or 0)
        fk_outbound = int(counts.get("foreign_key_outbound") or 0)
        fk_inbound = int(counts.get("foreign_key_inbound") or 0)
        semantic = int(counts.get("semantic") or 0)
        return _localized(
            locale,
            ru=(
                f"Для {entity['fqn']} зарегистрировано: lineage upstream — {upstream}, downstream — {downstream}; "
                f"FK исходящих — {fk_outbound}, входящих — {fk_inbound}; семантических связей — {semantic}. "
                "Типы связей не смешиваются и подтверждены метаданными OpenMetadata."
            ),
            en=(
                f"{entity['fqn']} has {upstream} upstream and {downstream} downstream lineage edges, "
                f"{fk_outbound} outbound and {fk_inbound} inbound foreign keys, and {semantic} semantic relationships. "
                "Relationship types remain distinct and are backed by OpenMetadata evidence."
            ),
        )


class OpenMetadataCopilotService:
    def __init__(
        self,
        config: OpenMetadataConfig | None = None,
        *,
        client: OpenMetadataClient | None = None,
        secret_key: str | None = None,
    ):
        self.config = config or OpenMetadataConfig.from_env()
        self.client = client or OpenMetadataClient(self.config)
        self.projection = CatalogProjection(self.config, self.client)
        self.dataset_retrieval = DatasetRetrievalAgent(self)
        self.discovery = DiscoveryAgent(self)
        self.impact_quality = ImpactQualityAgent(self)
        self.starter_questions = StarterQuestionAgent(self)
        if secret_key is None:
            secret_key = os.getenv("OPENMETADATA_CONFIRMATION_SECRET", "") or os.getenv("SECRET_KEY", "")
        if not secret_key:
            try:
                from common import settings

                secret_key = settings.get_secret_key()
            except Exception:
                secret_key = ""
        if not secret_key:
            raise OpenMetadataConfigurationError("RAGFlow secret key is required for governance confirmations")
        self.governance = GovernanceAgent(self, secret_key)
        self.catalog = CatalogCopilotAgent(self)

    def run_agent(
        self,
        role: str,
        question: str,
        *,
        user_id: str = "",
        locale: str = "ru",
        dataset_hits: list[dict[str, Any]] | None = None,
        dataset_warning: str | None = None,
        context: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if role == "starter_questions":
            starters = self.starter_questions.generate(user_id=user_id, locale=locale, question=question)
            questions = starters.get("questions") or []
            return {
                "agent": role,
                "intent": "starter_questions",
                "question": question,
                "answer": "\n".join(f"- {item['question']}" for item in questions),
                "entities": [],
                "freshness": starters.get("freshness"),
                "warnings": starters.get("warnings") or [],
                "starter_questions": questions,
                "constraints": starters.get("constraints") or {},
            }

        forced_intent = ""
        dataset_only = False
        if role == "dataset_retrieval":
            forced_intent = "discovery"
            dataset_only = True
            if dataset_hits is None and not dataset_warning:
                dataset_warning = _localized(
                    locale,
                    ru="OpenMetadata Dataset не настроен; использован live-поиск OMD",
                    en="The OpenMetadata Dataset is not configured; live OMD search was used",
                )
        elif role == "discovery":
            forced_intent = "discovery"
        elif role == "impact_quality":
            classified = self.catalog.classify(question)
            forced_intent = "quality" if classified == "quality" else "impact"
        elif role == "governance":
            forced_intent = "governance"
        elif role != "catalog_copilot":
            raise ValueError(f"Unsupported OpenMetadata agent role: {role}")

        return self.catalog.run(
            question,
            user_id=user_id,
            locale=locale,
            dataset_hits=dataset_hits,
            dataset_warning=dataset_warning,
            context=context,
            forced_intent=forced_intent,
            agent_name=role,
            dataset_only=dataset_only,
        )

    def allowed_domains(self, user_id: str) -> set[str] | None:
        raw_mapping = os.getenv("OPENMETADATA_USER_DOMAIN_MAP", "").strip()
        raw_global = os.getenv("OPENMETADATA_ALLOWED_DOMAINS", "").strip()
        values: list[str] | None = None
        if raw_mapping:
            try:
                mapping = json.loads(raw_mapping)
            except json.JSONDecodeError as exc:
                raise OpenMetadataConfigurationError("OPENMETADATA_USER_DOMAIN_MAP is not valid JSON") from exc
            if not isinstance(mapping, dict):
                raise OpenMetadataConfigurationError("OPENMETADATA_USER_DOMAIN_MAP must be an object")
            selected = mapping.get(user_id, mapping.get("*"))
            if selected is not None:
                if not isinstance(selected, list):
                    raise OpenMetadataConfigurationError("Each domain mapping value must be a list")
                values = [str(value) for value in selected]
            elif user_id:
                return set()
        elif raw_global:
            values = [value.strip() for value in raw_global.split(",") if value.strip()]
        return {value.casefold() for value in values} if values is not None else None

    def get_visible_entity(
        self,
        entity_id: str,
        *,
        user_id: str,
        filters: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if not entity_id:
            return None
        allowed_domains = self.allowed_domains(user_id)
        return next(
            (entity for entity in self.projection.get()["tables"] if entity["id"] == entity_id and _matches_filters(entity, filters or {}, allowed_domains)),
            None,
        )

    def capabilities_for_user(self, user_id: str) -> dict[str, int | None]:
        projection = self.projection.get()
        allowed_domains = self.allowed_domains(user_id)
        if allowed_domains is None:
            return dict(projection["counts"])
        tables = [table for table in projection["tables"] if _matches_filters(table, {}, allowed_domains)]
        counts: dict[str, int | None] = {key: None for key in projection["counts"]}
        counts.update(_table_counts(tables))
        counts["database_services"] = len({table["service"] for table in tables if table["service"]})
        counts["domains"] = len({domain for table in tables for domain in table["domains"]})
        return counts

    def status(self, *, force: bool = False, user_id: str = "") -> dict[str, Any]:
        projection = self.projection.get(force=force)
        version = self.client.get("/api/v1/system/version")
        warnings = list(projection["warnings"])
        try:
            rdf_status = self.client.rdf_status()
            knowledge_graph = {
                "enabled": bool(rdf_status.get("enabled")),
                "storage_type": rdf_status.get("storageType"),
                "inference": rdf_status.get("inference") or {},
            }
        except OpenMetadataError:
            knowledge_graph = {"enabled": False, "storage_type": None, "inference": {}}
            warnings.append("RDF-граф OpenMetadata недоступен")
        return {
            "connected": True,
            "version": version.get("version"),
            "base_url": self.config.public_url,
            "write_enabled": self.config.write_enabled,
            "dataset": {
                "id": self.config.dataset_id or None,
                "configured": bool(self.config.dataset_id),
                "top_n": self.config.dataset_top_n,
            },
            "freshness": projection["freshness"],
            "capabilities": self.capabilities_for_user(user_id),
            "knowledge_graph": knowledge_graph,
            "warnings": warnings,
            "agents": public_agent_roles(),
        }
