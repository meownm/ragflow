"""OpenMetadata catalog connector.

Each visible table is materialized as one Markdown document with stable
OpenMetadata identity and structured metadata.  The search index listing is
also used as the cheap fingerprint snapshot, so unchanged tables do not need
to be downloaded and parsed again.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import time
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote, urlparse

import requests

from common.data_source.config import INDEX_BATCH_SIZE
from common.data_source.exceptions import ConnectorMissingCredentialError, ConnectorValidationError
from common.data_source.interfaces import FingerprintConnector, LoadConnector, PollConnector, SlimConnectorWithPermSync
from common.data_source.models import Document, KeyRecord, SlimDocument


_SAFE_FILENAME = re.compile(r"[^\w.-]+", re.UNICODE)
LOGGER = logging.getLogger(__name__)


def _validate_origin(value: str, label: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ConnectorValidationError(f"{label} must be an http(s) origin without embedded credentials")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ConnectorValidationError(f"{label} must not contain a path, query, or fragment")


def _reference_names(value: Any) -> list[str]:
    values = value if isinstance(value, list) else [value] if value else []
    result = []
    for item in values:
        if isinstance(item, dict):
            name = item.get("displayName") or item.get("name") or item.get("fullyQualifiedName") or item.get("tagFQN")
            if name:
                result.append(str(name))
    return result


def _epoch_to_iso(value: Any) -> str | None:
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=UTC).isoformat()
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _unique_strings(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        value = str(value or "").strip()
        key = value.casefold()
        if value and key not in seen:
            seen.add(key)
            result.append(value)
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
    result = []
    for constraint in entity.get("tableConstraints") or []:
        if not isinstance(constraint, dict):
            continue
        constraint_type = str(constraint.get("constraintType") or "").strip().upper()
        columns = _unique_strings([str(value) for value in constraint.get("columns") or []])
        if not constraint_type or not columns:
            continue
        result.append(
            {
                "constraint_type": constraint_type,
                "columns": columns,
                "referred_columns": _unique_strings([str(value) for value in constraint.get("referredColumns") or []]),
                "relationship_type": str(constraint.get("relationshipType") or ""),
            }
        )
    return result


def _normalize_table(entity: dict[str, Any], public_url: str) -> dict[str, Any]:
    columns = entity.get("columns") or []
    service = entity.get("service") if isinstance(entity.get("service"), dict) else {}
    schema = entity.get("databaseSchema") if isinstance(entity.get("databaseSchema"), dict) else {}
    database = entity.get("database") if isinstance(entity.get("database"), dict) else {}
    fqn = str(entity.get("fullyQualifiedName") or entity.get("name") or "")
    return {
        "id": str(entity.get("id") or ""),
        "technical_name": str(entity.get("name") or ""),
        "fqn": fqn,
        "description": entity.get("description") or "",
        "updated_at": _epoch_to_iso(entity.get("updatedAt")),
        "service": str(service.get("displayName") or service.get("name") or ""),
        "schema": str(schema.get("displayName") or schema.get("name") or ""),
        "database": str(database.get("displayName") or database.get("name") or ""),
        "owners": _reference_names(entity.get("owners") or entity.get("owner")),
        "domains": _reference_names(entity.get("domains") or entity.get("domain")),
        "tags": _reference_names(entity.get("tags")),
        "glossary_terms": _glossary_terms(entity),
        "columns": columns,
        "table_constraints": _table_constraints(entity),
        "processed_lineage": bool(entity.get("processedLineage") or entity.get("upstreamLineage")),
        "url": f"{public_url}/table/{quote(fqn, safe='')}",
    }


class OpenMetadataConnector(LoadConnector, PollConnector, SlimConnectorWithPermSync, FingerprintConnector):
    """Synchronize OpenMetadata tables into a RAGFlow Dataset."""

    DEFAULT_MAX_ENTITIES = 5000
    DEFAULT_RETRY_COUNT = 2
    DEFAULT_TIMEOUT_SECONDS = 12.0
    MAX_BATCH_SIZE = 1000
    MAX_ENTITIES = 100_000

    def __init__(
        self,
        base_url: str,
        public_url: str | None = None,
        *,
        include_columns: bool = True,
        services: list[str] | str | None = None,
        domains: list[str] | str | None = None,
        tags: list[str] | str | None = None,
        batch_size: int = INDEX_BATCH_SIZE,
        max_entities: int = DEFAULT_MAX_ENTITIES,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        retry_count: int = DEFAULT_RETRY_COUNT,
    ) -> None:
        self.base_url = str(base_url or "").strip().rstrip("/")
        self.public_url = str(public_url or base_url or "").strip().rstrip("/")
        self.include_columns = self._as_bool(include_columns)
        self.services = self._string_set(services)
        self.domains = self._string_set(domains)
        self.tags = self._string_set(tags)
        self.batch_size = self._bounded_int(batch_size, 1, self.MAX_BATCH_SIZE, "batch_size")
        self.max_entities = self._bounded_int(max_entities, 1, self.MAX_ENTITIES, "max_entities")
        self.timeout_seconds = self._bounded_float(timeout_seconds, 1.0, 120.0, "timeout_seconds")
        self.retry_count = self._bounded_int(retry_count, 0, 10, "retry_count")
        self.credentials: dict[str, Any] = {}
        self._session = requests.Session()
        self._token = ""
        self._entities: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _as_bool(value: Any) -> bool:
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    @staticmethod
    def _string_set(value: list[str] | str | None) -> set[str]:
        if not value:
            return set()
        values = value.split(",") if isinstance(value, str) else value
        return {str(item).strip().casefold() for item in values if str(item).strip()}

    @staticmethod
    def _bounded_int(value: Any, minimum: int, maximum: int, name: str) -> int:
        try:
            result = int(value)
        except (TypeError, ValueError) as exc:
            raise ConnectorValidationError(f"OpenMetadata {name} must be an integer") from exc
        if result < minimum or result > maximum:
            raise ConnectorValidationError(f"OpenMetadata {name} must be between {minimum} and {maximum}")
        return result

    @staticmethod
    def _bounded_float(value: Any, minimum: float, maximum: float, name: str) -> float:
        try:
            result = float(value)
        except (TypeError, ValueError) as exc:
            raise ConnectorValidationError(f"OpenMetadata {name} must be a number") from exc
        if result < minimum or result > maximum:
            raise ConnectorValidationError(f"OpenMetadata {name} must be between {minimum:g} and {maximum:g}")
        return result

    def load_credentials(self, credentials: dict[str, Any]) -> dict[str, Any] | None:
        if not isinstance(credentials, dict):
            raise ConnectorValidationError("OpenMetadata credentials must be an object")
        self.credentials = dict(credentials)
        self._token = str(self.credentials.get("openmetadata_jwt_token") or "").strip()
        return None

    def _validate_config(self) -> None:
        _validate_origin(self.base_url, "OpenMetadata base_url")
        _validate_origin(self.public_url, "OpenMetadata public_url")
        username = str(self.credentials.get("openmetadata_username") or "").strip()
        password = str(self.credentials.get("openmetadata_password") or "")
        jwt_token = str(self.credentials.get("openmetadata_jwt_token") or "").strip()
        if not jwt_token and not (username and password):
            raise ConnectorMissingCredentialError("OpenMetadata JWT token or username/password")

    def _login(self) -> str:
        self._validate_config()
        username = str(self.credentials.get("openmetadata_username") or "").strip()
        password = str(self.credentials.get("openmetadata_password") or "")
        try:
            response = self._session.post(
                f"{self.base_url}/api/v1/users/login",
                json={
                    "email": username,
                    "password": base64.b64encode(password.encode("utf-8")).decode("ascii"),
                },
                headers={"Accept": "application/json"},
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise ConnectorValidationError("OpenMetadata login is unavailable") from exc
        if response.status_code >= 400:
            raise ConnectorValidationError("OpenMetadata rejected the configured credentials")
        try:
            token = str(response.json()["accessToken"])
        except (ValueError, KeyError, TypeError) as exc:
            raise ConnectorValidationError("OpenMetadata login returned an invalid response") from exc
        if not token:
            raise ConnectorValidationError("OpenMetadata login returned an empty token")
        return token

    def _request(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._validate_config()
        if not path.startswith("/api/v1/") or "//" in path:
            raise ConnectorValidationError("OpenMetadata API path is not allowed")
        refreshed = False
        for attempt in range(self.retry_count + 1):
            if not self._token:
                self._token = self._login()
            try:
                response = self._session.get(
                    f"{self.base_url}{path}",
                    params=params,
                    headers={"Accept": "application/json", "Authorization": f"Bearer {self._token}"},
                    timeout=self.timeout_seconds,
                )
            except requests.RequestException as exc:
                if attempt < self.retry_count:
                    time.sleep(min(0.2 * (2**attempt), 1.0))
                    continue
                raise ConnectorValidationError("OpenMetadata is unavailable") from exc
            if response.status_code == 401 and not refreshed and not self.credentials.get("openmetadata_jwt_token"):
                self._token = ""
                refreshed = True
                continue
            if response.status_code in {429, 500, 502, 503, 504} and attempt < self.retry_count:
                time.sleep(min(0.2 * (2**attempt), 1.0))
                continue
            if response.status_code >= 400:
                raise ConnectorValidationError(f"OpenMetadata request failed with HTTP {response.status_code}")
            try:
                payload = response.json()
            except ValueError as exc:
                raise ConnectorValidationError("OpenMetadata returned malformed JSON") from exc
            if not isinstance(payload, dict):
                raise ConnectorValidationError("OpenMetadata returned an unexpected response")
            return payload
        raise ConnectorValidationError("OpenMetadata request failed")

    def _list_tables(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        while len(result) < self.max_entities:
            size = min(1000, self.max_entities - len(result))
            payload = self._request(
                "/api/v1/search/query",
                params={"q": "*", "index": "table_search_index", "from": len(result), "size": size},
            )
            hits_block = payload.get("hits") if isinstance(payload.get("hits"), dict) else {}
            hits = hits_block.get("hits") or []
            rows = [hit.get("_source") for hit in hits if isinstance(hit, dict) and isinstance(hit.get("_source"), dict)]
            result.extend(rows)
            total_block = hits_block.get("total")
            total = total_block.get("value") if isinstance(total_block, dict) else total_block
            if not rows or (isinstance(total, int) and len(result) >= total):
                break
        return result[: self.max_entities]

    def _get_table(self, entity_id: str) -> dict[str, Any]:
        return self._request(
            f"/api/v1/tables/{entity_id}",
            params={"fields": "owners,domains,tags,columns,databaseSchema,database,tableConstraints,joins,dataProducts"},
        )

    def _get_lineage(self, entity_id: str) -> dict[str, Any]:
        return self._request(
            f"/api/v1/lineage/table/{entity_id}",
            params={"upstreamDepth": 1, "downstreamDepth": 1},
        )

    def _attach_lineage(self, entity: dict[str, Any]) -> dict[str, Any]:
        if not (entity.get("processedLineage") or entity.get("upstreamLineage")):
            return entity
        enriched = dict(entity)
        try:
            enriched["_ragflow_lineage"] = self._get_lineage(str(entity.get("id") or ""))
        except ConnectorValidationError as exc:
            LOGGER.warning("OpenMetadata lineage could not be indexed for %s: %s", entity.get("id"), exc)
            enriched["_ragflow_lineage"] = {"nodes": [], "upstreamEdges": [], "downstreamEdges": [], "unavailable": True}
        return enriched

    def validate_connector_settings(self) -> None:
        try:
            version = self._request("/api/v1/system/version")
            if not version.get("version"):
                raise ConnectorValidationError("OpenMetadata did not return its version")
            self._request(
                "/api/v1/search/query",
                params={"q": "*", "index": "table_search_index", "from": 0, "size": 1},
            )
        except (ConnectorValidationError, ConnectorMissingCredentialError):
            raise
        except Exception as exc:
            raise ConnectorValidationError(f"OpenMetadata connection validation failed: {exc}") from exc

    def _load_snapshot(self) -> dict[str, dict[str, Any]]:
        try:
            rows = self._list_tables()
        except Exception as exc:
            raise ConnectorValidationError(f"OpenMetadata catalog listing failed: {exc}") from exc

        entities: dict[str, dict[str, Any]] = {}
        for row in rows:
            if not isinstance(row, dict) or row.get("deleted"):
                continue
            entity_id = str(row.get("id") or "").strip()
            if not entity_id or not self._matches_scope(row):
                continue
            entities[entity_id] = self._attach_lineage(row)
        self._entities = entities
        return entities

    def _matches_scope(self, entity: dict[str, Any]) -> bool:
        table = _normalize_table(entity, self.public_url)
        if self.services and str(table.get("service") or "").casefold() not in self.services:
            return False
        if self.domains and not self.domains.intersection(str(item).casefold() for item in table.get("domains") or []):
            return False
        if self.tags and not self.tags.intersection(str(item).casefold() for item in table.get("tags") or []):
            return False
        return True

    @staticmethod
    def _fingerprint(entity: dict[str, Any]) -> str:
        payload = json.dumps(entity, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.blake2s(payload.encode("utf-8"), digest_size=16).hexdigest()

    def list_keys(self):
        for entity_id, entity in sorted(self._load_snapshot().items()):
            yield KeyRecord(key=entity_id, fingerprint=self._fingerprint(entity))

    def get_value(self, key: str) -> Document:
        entity = self._entities.get(str(key))
        if entity is None:
            try:
                entity = self._get_table(str(key))
            except Exception as exc:
                raise ConnectorValidationError(f"OpenMetadata table could not be loaded: {key}") from exc
            entity = self._attach_lineage(entity)
        if entity.get("deleted") or not self._matches_scope(entity):
            raise ConnectorValidationError(f"OpenMetadata table is outside connector scope: {key}")
        return self._to_document(entity)

    def load_from_state(self):
        batch: list[Document] = []
        for record in self.list_keys():
            batch.append(self.get_value(record.key))
            if len(batch) >= self.batch_size:
                yield batch
                batch = []
        if batch:
            yield batch

    def poll_source(self, start: float, end: float):
        if start > end:
            raise ConnectorValidationError("OpenMetadata poll start must not be later than poll end")
        # Fingerprints provide the authoritative incremental boundary.  The
        # method remains complete for callers that only know PollConnector.
        yield from self.load_from_state()

    def retrieve_all_slim_docs_perm_sync(self, callback: Any = None):
        del callback
        batch: list[SlimDocument] = []
        for entity_id in sorted(self._load_snapshot()):
            batch.append(SlimDocument(id=entity_id))
            if len(batch) >= self.batch_size:
                yield batch
                batch = []
        if batch:
            yield batch

    @staticmethod
    def _markdown_text(value: Any) -> str:
        return str(value or "").replace("\r", " ").strip()

    @staticmethod
    def _reference_name(value: Any) -> str:
        if not isinstance(value, dict):
            return ""
        return str(value.get("displayName") or value.get("name") or value.get("fullyQualifiedName") or "")

    def _to_document(self, entity: dict[str, Any]) -> Document:
        table = _normalize_table(entity, self.public_url)
        lines = [
            f"# {self._markdown_text(table['fqn'])}",
            "",
            "## Catalog identity",
            "- Entity type: table",
            f"- OpenMetadata entity ID: {table['id']}",
            f"- Fully qualified name: {self._markdown_text(table['fqn'])}",
            f"- Service: {self._markdown_text(table['service']) or 'not assigned'}",
            f"- Database: {self._markdown_text(table['database']) or 'not assigned'}",
            f"- Schema: {self._markdown_text(table['schema']) or 'not assigned'}",
            f"- Owners: {', '.join(table['owners']) or 'not assigned'}",
            f"- Domains: {', '.join(table['domains']) or 'not assigned'}",
            f"- Tags: {', '.join(table['tags']) or 'not assigned'}",
            f"- Updated at: {table['updated_at'] or 'unknown'}",
            f"- Source URL: {table['url']}",
            "",
            "## Description",
            self._markdown_text(table["description"]) or "No description is registered in OpenMetadata.",
        ]

        constraints = table.get("table_constraints") or []
        lineage = entity.get("_ragflow_lineage") if isinstance(entity.get("_ragflow_lineage"), dict) else {}
        lines.extend(["", "## Relationships"])
        if table.get("glossary_terms"):
            lines.append(f"- Glossary concepts: {', '.join(table['glossary_terms'])}")
        for constraint in constraints:
            local_columns = ", ".join(constraint.get("columns") or [])
            if constraint.get("constraint_type") == "FOREIGN_KEY":
                referred = ", ".join(constraint.get("referred_columns") or []) or "not registered"
                cardinality = self._markdown_text(constraint.get("relationship_type"))
                suffix = f" ({cardinality})" if cardinality else ""
                lines.append(f"- Foreign key: {local_columns} -> {referred}{suffix}")
            else:
                lines.append(f"- {constraint.get('constraint_type')}: {local_columns}")
        nodes = {str(node.get("id") or ""): str(node.get("fullyQualifiedName") or node.get("name") or node.get("id") or "") for node in lineage.get("nodes") or [] if isinstance(node, dict)}
        nodes[table["id"]] = table["fqn"]
        for direction, key in (("Upstream lineage", "upstreamEdges"), ("Downstream lineage", "downstreamEdges")):
            for edge in lineage.get(key) or []:
                if not isinstance(edge, dict):
                    continue
                source_id = str(edge.get("fromEntity") or "")
                target_id = str(edge.get("toEntity") or "")
                source = nodes.get(source_id, source_id)
                target = nodes.get(target_id, target_id)
                details = edge.get("lineageDetails") if isinstance(edge.get("lineageDetails"), dict) else {}
                mappings = []
                for mapping in details.get("columnsLineage") or []:
                    if not isinstance(mapping, dict):
                        continue
                    from_columns = ", ".join(str(value) for value in mapping.get("fromColumns") or [])
                    to_column = str(mapping.get("toColumn") or "")
                    if from_columns and to_column:
                        mappings.append(f"{from_columns} -> {to_column}")
                suffix = f"; columns: {'; '.join(mappings)}" if mappings else ""
                lines.append(f"- {direction}: {source} -> {target}{suffix}")
        if len(lines) and lines[-1] == "## Relationships":
            lines.append("No structural, semantic, or lineage relationships are registered in OpenMetadata.")

        columns = entity.get("columns") or []
        if self.include_columns:
            lines.extend(["", "## Columns"])
            if not columns:
                lines.append("No columns are registered in OpenMetadata.")
            for column in columns:
                if not isinstance(column, dict):
                    continue
                name = self._markdown_text(column.get("displayName") or column.get("name"))
                data_type = self._markdown_text(column.get("dataTypeDisplay") or column.get("dataType"))
                description = self._markdown_text(column.get("description"))
                tags = [self._reference_name(tag) or str(tag.get("tagFQN") or "") for tag in column.get("tags") or [] if isinstance(tag, dict)]
                details = [part for part in (data_type, description, f"tags: {', '.join(filter(None, tags))}" if tags else "") if part]
                lines.append(f"- **{name or 'unnamed'}**: {'; '.join(details) or 'no type or description'}")

        content = "\n".join(lines).strip() + "\n"
        blob = content.encode("utf-8")
        updated_at = self._updated_at(entity.get("updatedAt"))
        semantic = _SAFE_FILENAME.sub("_", str(table.get("technical_name") or "table")).strip("._") or "table"
        semantic = f"{semantic[:180]}-{table['id'][:8]}"
        metadata = {
            "omd_entity_id": table["id"],
            "omd_entity_type": "table",
            "omd_fqn": table["fqn"],
            "omd_updated_at_epoch": entity.get("updatedAt"),
            "omd_service": table["service"],
            "omd_database": table["database"],
            "omd_schema": table["schema"],
            "omd_owners": table["owners"],
            "omd_domains": table["domains"],
            "omd_tags": table["tags"],
            "omd_glossary_terms": table["glossary_terms"],
            "omd_foreign_keys": _unique_strings(
                [referred for constraint in constraints if constraint.get("constraint_type") == "FOREIGN_KEY" for referred in constraint.get("referred_columns") or []]
            ),
            "omd_has_lineage": bool((lineage.get("upstreamEdges") or []) or (lineage.get("downstreamEdges") or [])),
            "omd_url": table["url"],
        }
        return Document(
            id=table["id"],
            source="openmetadata",
            semantic_identifier=semantic,
            extension=".md",
            blob=blob,
            doc_updated_at=updated_at,
            size_bytes=len(blob),
            metadata=metadata,
            fingerprint=self._fingerprint(entity),
        )

    @staticmethod
    def _updated_at(value: Any) -> datetime:
        try:
            return datetime.fromtimestamp(int(value) / 1000, tz=UTC)
        except (TypeError, ValueError, OSError, OverflowError):
            return datetime.now(UTC)
