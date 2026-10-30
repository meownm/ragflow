"""Durable review threads and incremental SQL/document revision commands."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict
from datetime import datetime
import json
from typing import Any

from peewee import IntegrityError

from api.apps.business_documents.authorization import BusinessDocumentAccess
from api.apps.business_documents.sql_query_agents import BusinessDocumentSqlAgentService, _stable_hash
from api.db.db_models import (
    BusinessDocumentJob,
    BusinessDocumentSqlAgentProposal,
    BusinessDocumentSqlQueryArtifact,
    BusinessDocumentSqlQueryDraft,
    BusinessDocumentSqlQueryProject,
    BusinessDocumentSqlQueryRun,
    BusinessDocumentSqlQueryThread,
)
from business_documents.application.errors import BusinessDocumentError, ConflictError, ValidationError
from business_documents.sql_query import IN_PROGRESS_RUN_STATUSES
from business_documents.sql_query.project_compilation import build_project_compile_command
from business_documents.sql_query.project_compilation import ProjectCompilationError
from business_documents.sql_query.query_specification import QuerySpecificationValidationError, SqlGuardError, compile_query_payload, parse_schema_snapshot
from business_documents.sql_query.revisions import locate_anchor, preserves_document_facts, query_blocks, validate_anchor
from common.misc_utils import get_uuid
from common.time_utils import current_timestamp


def _payload(raw: object, fields: set[str]) -> Mapping[str, Any]:
    if not isinstance(raw, Mapping) or raw.get("schema_version") != "1" or set(raw) - fields - {"schema_version"}:
        raise ValidationError("INVALID_SQL_REVISION", "Invalid SQL revision command")
    return raw


def _version(raw: Mapping[str, Any]) -> int:
    value = raw.get("expected_state_version")
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValidationError("INVALID_SQL_REVISION", "A current project version is required")
    return value


def _key(raw: Mapping[str, Any]) -> str:
    value = raw.get("idempotency_key")
    if not isinstance(value, str) or not 1 <= len(value) <= 128:
        raise ValidationError("INVALID_SQL_REVISION", "An idempotency key is required")
    return value


def _message(text: object, actor_id: str) -> dict[str, Any]:
    if not isinstance(text, str) or not 1 <= len(text.strip()) <= 4000:
        raise ValidationError("INVALID_SQL_REVISION", "Comment must contain 1 to 4000 characters")
    return {"id": get_uuid(), "author_id": actor_id, "text": text.strip(), "created_at": current_timestamp()}


def _parameters(query: BusinessDocumentSqlQueryArtifact | None, compiled: Mapping[str, Any]) -> list[dict[str, Any]]:
    saved = query.payload.get("parameters") if query else None
    if isinstance(saved, list):
        return saved
    values = compiled.get("parameters")
    if not isinstance(values, Mapping):
        return []
    result = []
    for name, value in values.items():
        if isinstance(value, bool):
            kind = "boolean"
        elif isinstance(value, int):
            kind = "integer"
        elif isinstance(value, float):
            kind = "decimal"
        elif isinstance(value, list):
            kind = "integer_list" if all(isinstance(item, int) and not isinstance(item, bool) for item in value) else "text_list"
        else:
            kind = "text"
        result.append({"name": name, "type": kind, "value": value})
    return result


class BusinessDocumentSqlRevisionService:
    @staticmethod
    def history_context(tenant_id: str, actor_id: str, project_id: str, raw: object, is_admin: bool = False, access_role: str = "AUTHOR_CREATOR") -> dict[str, Any]:
        """Read a verified historical context after authorizing every exposed table."""
        from api.apps.business_documents.sql_query_access import authorize_catalog_sources, authorize_sql_source, read_sql_project

        command = _payload(raw, {"artifact_id", "compilation_id", "query_artifact_id"})
        fields = set(command) - {"schema_version"}
        if fields not in ({"artifact_id"}, {"compilation_id", "query_artifact_id"}) or any(not isinstance(command[name], str) or not 1 <= len(command[name]) <= 32 for name in fields):
            raise ValidationError("INVALID_SQL_REVISION", "Select one saved artifact or a compilation and query")
        project = read_sql_project(tenant_id, actor_id, project_id, is_admin, access_role)
        if "artifact_id" in command:
            artifact = BusinessDocumentSqlQueryArtifact.get_or_none(
                (BusinessDocumentSqlQueryArtifact.id == command["artifact_id"])
                & (BusinessDocumentSqlQueryArtifact.project_id == project.id)
                & (BusinessDocumentSqlQueryArtifact.tenant_id == tenant_id)
                & (BusinessDocumentSqlQueryArtifact.kind.in_(("REQUIREMENTS", "SCHEMA")))
            )
            if artifact is None or not isinstance(artifact.payload, Mapping) or artifact.content_hash != _stable_hash(artifact.payload):
                raise ConflictError("SQL_HISTORY_SOURCE_UNAVAILABLE", "The saved requirements or schema cannot be verified")
            requirements = artifact if artifact.kind == "REQUIREMENTS" else None
            schema = artifact if artifact.kind == "SCHEMA" else None
            source_ids = {"artifact_id": artifact.id, "requirements_artifact_id" if requirements else "schema_artifact_id": artifact.id}
            changed = False
            if schema is not None:
                _, changed = authorize_catalog_sources(project, actor_id, schema.payload, None, is_admin=is_admin, access_role=access_role, include_schema_tables=True)
        else:
            source = authorize_sql_source(project, actor_id, command["compilation_id"], is_admin=is_admin, access_role=access_role, include_schema_tables=True)
            if source.query.id != command["query_artifact_id"]:
                raise ConflictError("SQL_HISTORY_SOURCE_MISMATCH", "The selected query does not match its compilation")
            requirements, schema, changed = source.requirements, source.schema, source.catalog_changed
            source_ids = {"compilation_id": source.compilation.id, "query_artifact_id": source.query.id, "requirements_artifact_id": requirements.id, "schema_artifact_id": schema.id}
        if requirements is not None and (
            not isinstance(requirements.payload.get("requirements"), list)
            or not requirements.payload["requirements"]
            or any(not isinstance(item, Mapping) or not isinstance(item.get("statement"), str) or not item["statement"].strip() for item in requirements.payload["requirements"])
            or not isinstance(requirements.payload.get("questions", []), list)
        ):
            raise ConflictError("SQL_HISTORY_SOURCE_UNAVAILABLE", "The saved requirements cannot be verified")

        def identity(artifact):
            return {
                "id": artifact.id,
                "revision": artifact.revision,
                "accepted_by": artifact.accepted_by,
                "created_at": artifact.create_time,
                "content_hash": artifact.content_hash,
                "source_proposal_id": artifact.source_proposal_id,
            }

        return {
            "schema_version": "1",
            "project_id": project.id,
            "source": source_ids,
            "catalog_changed": changed,
            "requirements": {
                **identity(requirements),
                "text": "\n".join(f"- {item.get('statement', '')}" for item in requirements.payload.get("requirements", []) if isinstance(item, Mapping)),
                "requirements": requirements.payload.get("requirements", []),
                "questions": requirements.payload.get("questions", []),
            }
            if requirements
            else None,
            "schema": {**identity(schema), "accepted_schema": schema.payload["accepted_schema"], "snapshot": asdict(parse_schema_snapshot(schema.payload["schema_snapshot"]))} if schema else None,
        }

    @staticmethod
    def _project(tenant_id: str, actor_id: str, project_id: str, is_admin: bool, access_role: str):
        project = BusinessDocumentSqlAgentService._get_project(tenant_id, project_id)
        access = BusinessDocumentAccess(actor_id, access_role, is_admin)
        access.require_edit(project.owner_id)
        BusinessDocumentSqlAgentService._require_active(project)
        return project

    @staticmethod
    def _source(project: BusinessDocumentSqlQueryProject, target: str):
        if target == "DOCUMENT":
            artifact = (
                BusinessDocumentSqlQueryArtifact.select()
                .where(
                    (BusinessDocumentSqlQueryArtifact.project_id == project.id)
                    & (BusinessDocumentSqlQueryArtifact.tenant_id == project.tenant_id)
                    & (BusinessDocumentSqlQueryArtifact.kind == "DOCUMENT")
                )
                .order_by(BusinessDocumentSqlQueryArtifact.revision.desc())
                .first()
            )
            return artifact, ""
        if target != "QUERY" or not project.query_artifact_id:
            return None, ""
        artifact, sql, _ = BusinessDocumentSqlRevisionService._query_revision(project, project.query_artifact_id)
        return artifact, sql

    @staticmethod
    def _query_revision(project: BusinessDocumentSqlQueryProject, query_id: str):
        artifact = BusinessDocumentSqlQueryArtifact.get_or_none(
            (BusinessDocumentSqlQueryArtifact.id == query_id)
            & (BusinessDocumentSqlQueryArtifact.project_id == project.id)
            & (BusinessDocumentSqlQueryArtifact.tenant_id == project.tenant_id)
            & (BusinessDocumentSqlQueryArtifact.kind == "QUERY")
        )
        if artifact is None:
            return None, "", {}
        compilations = (
            BusinessDocumentSqlQueryArtifact.select()
            .where(
                (BusinessDocumentSqlQueryArtifact.project_id == project.id)
                & (BusinessDocumentSqlQueryArtifact.tenant_id == project.tenant_id)
                & (BusinessDocumentSqlQueryArtifact.kind == "COMPILATION")
            )
            .order_by(BusinessDocumentSqlQueryArtifact.revision.desc())
        )
        source_ids = [project.requirements_artifact_id, project.schema_artifact_id, query_id]
        for compilation in compilations:
            if compilation.payload.get("source_artifact_ids") == source_ids:
                result = compilation.payload.get("result") or {}
                if isinstance(result, Mapping) and isinstance(result.get("guard"), Mapping) and result["guard"].get("status") == "PASS":
                    return artifact, str(result.get("sql") or ""), result
        return artifact, "", {}

    @classmethod
    def workspace(cls, tenant_id: str, actor_id: str, project_id: str, is_admin: bool = False, access_role: str = "AUTHOR_CREATOR") -> dict[str, Any]:
        project = BusinessDocumentSqlAgentService._get_project(tenant_id, project_id)
        BusinessDocumentAccess(actor_id, access_role, is_admin).require_view_sql(project.owner_id)
        query, sql = cls._source(project, "QUERY")
        document, _ = cls._source(project, "DOCUMENT")
        documents_by_id = {
            item.id: item
            for item in BusinessDocumentSqlQueryArtifact.select().where(
                (BusinessDocumentSqlQueryArtifact.project_id == project_id) & (BusinessDocumentSqlQueryArtifact.tenant_id == tenant_id) & (BusinessDocumentSqlQueryArtifact.kind == "DOCUMENT")
            )
        }
        blocks = query_blocks(sql) if sql else []
        draft = BusinessDocumentSqlQueryDraft.get_or_none((BusinessDocumentSqlQueryDraft.project_id == project_id) & (BusinessDocumentSqlQueryDraft.tenant_id == tenant_id))
        artifacts = list(
            BusinessDocumentSqlQueryArtifact.select()
            .where(
                (BusinessDocumentSqlQueryArtifact.project_id == project_id)
                & (BusinessDocumentSqlQueryArtifact.tenant_id == tenant_id)
                & (BusinessDocumentSqlQueryArtifact.kind.in_(("REQUIREMENTS", "SCHEMA", "QUERY", "COMPILATION", "DOCUMENT")))
            )
            .order_by(BusinessDocumentSqlQueryArtifact.create_time.desc(), BusinessDocumentSqlQueryArtifact.id.desc())
        )
        queries_by_id = {artifact.id: artifact for artifact in artifacts if artifact.kind == "QUERY"}
        artifacts_by_id = {artifact.id: artifact for artifact in artifacts}
        query_sql = {}
        query_compilations = {}
        query_sources = {}
        query_parameters = {}
        for artifact in sorted((item for item in artifacts if item.kind == "COMPILATION"), key=lambda item: item.revision, reverse=True):
            source_ids = artifact.payload.get("source_artifact_ids")
            if isinstance(source_ids, list) and len(source_ids) == 3 and all(isinstance(identity, str) for identity in source_ids):
                sources = [artifacts_by_id.get(identity) for identity in source_ids]
                if any(source is None or source.kind != kind for source, kind in zip(sources, ("REQUIREMENTS", "SCHEMA", "QUERY"), strict=True)):
                    continue
                result = artifact.payload.get("result") or {}
                if isinstance(result, Mapping) and isinstance(result.get("guard"), Mapping) and result["guard"].get("status") == "PASS" and isinstance(result.get("sql"), str) and result["sql"]:
                    query_sql.setdefault(source_ids[2], result["sql"])
                    query_compilations.setdefault(source_ids[2], artifact.id)
                    query_sources.setdefault(source_ids[2], source_ids[:2])
                    if source_ids[2] not in query_parameters:
                        try:
                            command = build_project_compile_command(*(source.payload for source in sources))
                            declared = command["parameters"] if "manual_sql" in command else command["specification"]["parameters"]
                            query_parameters[source_ids[2]] = [
                                {
                                    "name": item["name"],
                                    "type": item["type"],
                                    "value": result["parameters"][item["name"]],
                                    "value_text": json.dumps(result["parameters"][item["name"]], ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                                }
                                for item in declared
                            ]
                        except (ProjectCompilationError, KeyError, TypeError):
                            query_parameters[source_ids[2]] = None
        query_blocks_by_id = {project.query_artifact_id: blocks} if project.query_artifact_id and sql else {}
        proposals_by_thread: dict[str, list[dict[str, Any]]] = {}
        for proposal in (
            BusinessDocumentSqlAgentProposal.select()
            .where((BusinessDocumentSqlAgentProposal.project_id == project_id) & (BusinessDocumentSqlAgentProposal.tenant_id == tenant_id) & (BusinessDocumentSqlAgentProposal.kind == "REVISION"))
            .order_by(BusinessDocumentSqlAgentProposal.create_time.asc())
        ):
            result = proposal.payload.get("agent_result", {})
            thread_id = result.get("source", {}).get("thread_id")
            if isinstance(thread_id, str):
                proposals_by_thread.setdefault(thread_id, []).append(
                    {
                        "id": proposal.id,
                        "status": proposal.status,
                        "proposal": result.get("proposal"),
                        "decided_by": proposal.decided_by,
                        "decided_at": proposal.decided_at,
                    }
                )
        threads = []
        for item in (
            BusinessDocumentSqlQueryThread.select()
            .where((BusinessDocumentSqlQueryThread.project_id == project_id) & (BusinessDocumentSqlQueryThread.tenant_id == tenant_id))
            .order_by(BusinessDocumentSqlQueryThread.create_time.asc())
        ):
            current = query if item.target == "QUERY" else documents_by_id.get(item.base_artifact_id)
            current_sql, current_blocks = sql, blocks
            if item.target == "QUERY" and item.anchor.get("pinned_revision"):
                current = queries_by_id.get(item.base_artifact_id)
                current_sql = query_sql.get(item.base_artifact_id, "")
                if current_sql and item.base_artifact_id not in query_blocks_by_id:
                    query_blocks_by_id[item.base_artifact_id] = query_blocks(current_sql)
                current_blocks = query_blocks_by_id.get(item.base_artifact_id, [])
            location = (
                locate_anchor(item.anchor, same_revision=current.id == item.base_artifact_id, sql=current_sql, blocks=current_blocks)
                if current and (item.target == "DOCUMENT" or current_sql)
                else {"status": "UNRESOLVED", "anchor": item.anchor}
            )
            threads.append(
                {
                    "id": item.id,
                    "target": item.target,
                    "base_artifact_id": item.base_artifact_id,
                    "anchor": location["anchor"],
                    "anchor_status": location["status"],
                    "messages": item.messages,
                    "rebindings": item.rebindings,
                    "status": item.status,
                    "version": item.version,
                    "proposals": proposals_by_thread.get(item.id, []),
                }
            )
        history = []
        query_revisions = []
        for artifact in artifacts:
            payload = artifact.payload
            payload = payload if isinstance(payload, Mapping) else {}
            if artifact.kind == "REQUIREMENTS":
                preview = "\n".join(str(item.get("statement") or "") for item in payload.get("requirements", []) if isinstance(item, Mapping))
            elif artifact.kind == "SCHEMA":
                preview = ", ".join(str(item.get("entity_id") or "") for item in payload.get("accepted_schema", []) if isinstance(item, Mapping))
            elif artifact.kind == "QUERY":
                preview = str(payload.get("sql") or "Структурированный запрос; SQL показан в ревизии проверки")
                if artifact.id in query_sql:
                    revision_sql = query_sql[artifact.id]
                    if artifact.id not in query_blocks_by_id:
                        query_blocks_by_id[artifact.id] = query_blocks(revision_sql)
                    query_revisions.append(
                        {
                            "id": artifact.id,
                            "revision": artifact.revision,
                            "sql": revision_sql,
                            "compilation_id": query_compilations[artifact.id],
                            "blocks": query_blocks_by_id[artifact.id],
                            "parameters": query_parameters[artifact.id],
                            "accepted_by": artifact.accepted_by,
                            "source_proposal_id": artifact.source_proposal_id,
                            "created_at": artifact.create_time,
                            "source_artifact_ids": query_sources[artifact.id],
                            "matches_current_context": query_sources[artifact.id] == [project.requirements_artifact_id, project.schema_artifact_id],
                            "restored_from": payload.get("restored_from"),
                        }
                    )
            elif artifact.kind == "COMPILATION":
                result = payload.get("result")
                preview = str(result.get("sql") or "") if isinstance(result, Mapping) else ""
            else:
                conclusion = payload.get("confirmed_conclusion")
                preview = str(conclusion.get("text") or "") if isinstance(conclusion, Mapping) else ""
                preview = preview or f"Сохранённый документ · {payload.get('row_count', 0)} строк"
            history.append(
                {
                    "id": artifact.id,
                    "kind": artifact.kind,
                    "revision": artifact.revision,
                    "content_hash": artifact.content_hash,
                    "accepted_by": artifact.accepted_by,
                    "source_proposal_id": artifact.source_proposal_id,
                    "created_at": artifact.create_time,
                    "preview": preview[:20_000],
                    "source_artifact_ids": payload.get("source_artifact_ids", []),
                    "restored_from": payload.get("restored_from"),
                }
            )
        return {
            "project_id": project_id,
            "state_version": project.state_version,
            "query_artifact_id": query.id if query else None,
            "document_artifact_id": document.id if document else None,
            "sql": sql,
            "blocks": blocks,
            "query_revisions": query_revisions,
            "draft": ({"version": draft.version, "base_query_artifact_id": draft.base_query_artifact_id, "steps": draft.steps} if draft else None),
            "threads": threads,
            "history": history,
        }

    @classmethod
    def save_draft(cls, tenant_id: str, actor_id: str, project_id: str, raw: object, is_admin: bool = False, access_role: str = "AUTHOR_CREATOR") -> dict[str, Any]:
        data = _payload(raw, {"expected_state_version", "expected_draft_version", "steps"})
        with BusinessDocumentSqlQueryProject._meta.database.atomic():
            project = cls._project(tenant_id, actor_id, project_id, is_admin, access_role)
            if project.state_version != _version(data):
                raise ConflictError("SQL_PROJECT_VERSION_CONFLICT", "Project changed; reload the draft")
            steps = data.get("steps")
            if (
                not isinstance(steps, list)
                or not 1 <= len(steps) <= 12
                or any(
                    not isinstance(item, Mapping)
                    or set(item) != {"title", "sql", "parameters"}
                    or not isinstance(item["title"], str)
                    or not 1 <= len(item["title"].strip()) <= 200
                    or not isinstance(item["sql"], str)
                    or len(item["sql"]) > 20_000
                    or not isinstance(item["parameters"], str)
                    or len(item["parameters"]) > 10_000
                    for item in steps
                )
            ):
                raise ValidationError("INVALID_SQL_DRAFT", "Draft must contain 1 to 12 bounded SQL steps")
            draft = BusinessDocumentSqlQueryDraft.get_or_none(BusinessDocumentSqlQueryDraft.project_id == project_id)
            expected = data.get("expected_draft_version")
            if expected is not None and (isinstance(expected, bool) or not isinstance(expected, int) or expected < 0):
                raise ValidationError("INVALID_SQL_DRAFT", "Draft version must be a non-negative integer")
            if draft is None:
                if expected not in (None, 0):
                    raise ConflictError("SQL_DRAFT_VERSION_CONFLICT", "Draft changed; reload it")
                try:
                    draft = BusinessDocumentSqlQueryDraft.create(id=get_uuid(), project_id=project_id, tenant_id=tenant_id, base_query_artifact_id=project.query_artifact_id, version=1, steps=steps)
                except IntegrityError as exc:
                    raise ConflictError("SQL_DRAFT_VERSION_CONFLICT", "Draft changed; reload it") from exc
            else:
                if draft.tenant_id != tenant_id:
                    raise ConflictError("SQL_DRAFT_VERSION_CONFLICT", "Draft changed; reload it")
                if draft.base_query_artifact_id != project.query_artifact_id:
                    if expected not in (None, 0):
                        raise ConflictError("SQL_DRAFT_VERSION_CONFLICT", "Draft changed; reload it")
                    next_base = project.query_artifact_id
                elif draft.version != expected:
                    raise ConflictError("SQL_DRAFT_VERSION_CONFLICT", "Draft changed; reload it")
                else:
                    next_base = draft.base_query_artifact_id
                changed = (
                    BusinessDocumentSqlQueryDraft.update(
                        base_query_artifact_id=next_base,
                        steps=steps,
                        version=draft.version + 1,
                        update_time=current_timestamp(),
                        update_date=datetime.now(),
                    )
                    .where(
                        (BusinessDocumentSqlQueryDraft.id == draft.id)
                        & (BusinessDocumentSqlQueryDraft.version == draft.version)
                        & (BusinessDocumentSqlQueryDraft.base_query_artifact_id == draft.base_query_artifact_id)
                    )
                    .execute()
                )
                if changed != 1:
                    raise ConflictError("SQL_DRAFT_VERSION_CONFLICT", "Draft changed; reload it")
                draft = BusinessDocumentSqlQueryDraft.get_by_id(draft.id)
            return {"version": draft.version, "base_query_artifact_id": draft.base_query_artifact_id, "steps": draft.steps}

    @classmethod
    def create_thread(
        cls, tenant_id: str, actor_id: str, project_id: str, raw: object, is_admin: bool = False, access_role: str = "AUTHOR_CREATOR", *, compilation_id: str | None = None
    ) -> dict[str, Any]:
        if compilation_id is not None and (not isinstance(compilation_id, str) or len(compilation_id) != 32):
            raise ValidationError("INVALID_SQL_REVISION", "Select one saved compilation")
        data = _payload(raw, {"expected_state_version", "idempotency_key", "target", "base_artifact_id", "anchor", "text", "additional_entity_ids"})
        additional = data.get("additional_entity_ids", [])
        if (
            not isinstance(additional, list)
            or len(additional) > 8
            or any(not isinstance(item, str) or not 1 <= len(item) <= 500 for item in additional)
            or len(set(additional)) != len(additional)
            or (additional and data.get("target") != "QUERY")
        ):
            raise ValidationError("INVALID_SQL_SCHEMA_CHANGE", "Select at most eight distinct tables for a query discussion")
        key = _key(data)
        identity = {"type": "CREATE_SQL_THREAD", "request": data}
        if compilation_id is not None:
            identity["compilation_id"] = compilation_id
        request_hash = _stable_hash(identity)
        with BusinessDocumentSqlQueryProject._meta.database.atomic():
            project = cls._project(tenant_id, actor_id, project_id, is_admin, access_role)
            replay = BusinessDocumentSqlAgentService._command_replay(tenant_id, project_id, key, request_hash)
            if replay is not None:
                return replay
            if project.state_version != _version(data):
                raise ConflictError("SQL_PROJECT_VERSION_CONFLICT", "Project changed; reload before commenting")
            target = data.get("target")
            if target not in {"QUERY", "DOCUMENT"}:
                raise ValidationError("INVALID_SQL_REVISION", "Comment target is unsupported")
            artifact, sql = cls._source(project, target)
            requested_base = data.get("base_artifact_id")
            pinned_revision = False
            if requested_base is not None:
                if not isinstance(requested_base, str) or len(requested_base) != 32:
                    raise ValidationError("INVALID_SQL_REVISION", "Selected revision is invalid")
                if target == "DOCUMENT":
                    artifact = BusinessDocumentSqlQueryArtifact.get_or_none(
                        (BusinessDocumentSqlQueryArtifact.id == requested_base)
                        & (BusinessDocumentSqlQueryArtifact.project_id == project_id)
                        & (BusinessDocumentSqlQueryArtifact.tenant_id == tenant_id)
                        & (BusinessDocumentSqlQueryArtifact.kind == "DOCUMENT")
                    )
                elif compilation_id is not None:
                    from api.apps.business_documents.sql_query_access import authorize_sql_source

                    selected = authorize_sql_source(project, actor_id, compilation_id, is_admin=is_admin, access_role=access_role, include_schema_tables=True)
                    if selected.query.id != requested_base:
                        raise ConflictError("SQL_HISTORY_SOURCE_MISMATCH", "Selected query does not match its compilation")
                    artifact, sql = selected.query, selected.compiled["sql"]
                    pinned_revision = requested_base != project.query_artifact_id
                else:
                    artifact, sql, _ = cls._query_revision(project, requested_base)
                    if not sql:
                        raise ConflictError("SQL_REVISION_SOURCE_MISSING", "Selected query revision needs its confirmed schema")
                    pinned_revision = requested_base != project.query_artifact_id
            if artifact is None or (target == "QUERY" and not sql):
                raise ConflictError("SQL_REVISION_SOURCE_MISSING", "Save a query or document before commenting")
            try:
                anchor = validate_anchor(data.get("anchor"), target=target, sql=sql, blocks=query_blocks(sql) if sql else [])
            except ValueError as exc:
                raise ValidationError("INVALID_SQL_ANCHOR", str(exc)) from exc
            if pinned_revision:
                anchor["pinned_revision"] = True
            if compilation_id is not None:
                if target != "QUERY" or requested_base is None:
                    raise ValidationError("INVALID_SQL_REVISION", "A saved compilation requires its selected query")
                anchor["pinned_compilation_id"] = compilation_id
            thread = BusinessDocumentSqlQueryThread.create(
                id=get_uuid(),
                project_id=project_id,
                tenant_id=tenant_id,
                base_artifact_id=artifact.id,
                target=target,
                anchor=anchor,
                messages=[{**_message(data.get("text"), actor_id), **({"additional_entity_ids": additional} if additional else {})}],
            )
            response = {
                "id": thread.id,
                "target": target,
                "base_artifact_id": artifact.id,
                "anchor": anchor,
                "anchor_status": "CURRENT",
                "messages": thread.messages,
                "rebindings": [],
                "status": thread.status,
                "version": thread.version,
            }
            BusinessDocumentSqlAgentService._record_command(tenant_id, project_id, key, request_hash, response)
            return response

    @classmethod
    def reattach(cls, tenant_id: str, actor_id: str, project_id: str, thread_id: str, raw: object, is_admin: bool = False, access_role: str = "AUTHOR_CREATOR") -> dict[str, Any]:
        data = _payload(raw, {"expected_state_version", "expected_thread_version", "idempotency_key", "anchor"})
        version, key = _version(data), _key(data)
        request_hash = _stable_hash({"type": "REATTACH_SQL_THREAD", "thread_id": thread_id, "request": data})
        with BusinessDocumentSqlQueryProject._meta.database.atomic():
            project = cls._project(tenant_id, actor_id, project_id, is_admin, access_role)
            replay = BusinessDocumentSqlAgentService._command_replay(tenant_id, project_id, key, request_hash)
            if replay is not None:
                return replay
            if project.state_version != version or project.operation_state != "IDLE":
                raise ConflictError("SQL_PROJECT_VERSION_CONFLICT", "Project changed; reload before reattaching")
            thread = BusinessDocumentSqlQueryThread.get_or_none(
                (BusinessDocumentSqlQueryThread.id == thread_id) & (BusinessDocumentSqlQueryThread.project_id == project_id) & (BusinessDocumentSqlQueryThread.tenant_id == tenant_id)
            )
            if thread is None or thread.status != "OPEN" or thread.version != data.get("expected_thread_version"):
                raise ConflictError("SQL_THREAD_VERSION_CONFLICT", "Discussion changed; reload it")
            artifact, sql = cls._source(project, thread.target)
            if thread.target == "DOCUMENT":
                artifact = BusinessDocumentSqlQueryArtifact.get_or_none(
                    (BusinessDocumentSqlQueryArtifact.id == thread.base_artifact_id)
                    & (BusinessDocumentSqlQueryArtifact.project_id == project_id)
                    & (BusinessDocumentSqlQueryArtifact.tenant_id == tenant_id)
                    & (BusinessDocumentSqlQueryArtifact.kind == "DOCUMENT")
                )
            if artifact is None or (thread.target == "QUERY" and not sql):
                raise ConflictError("SQL_REVISION_SOURCE_MISSING", "Current revision is unavailable")
            try:
                anchor = validate_anchor(data.get("anchor"), target=thread.target, sql=sql, blocks=query_blocks(sql) if sql else [])
            except ValueError as exc:
                raise ValidationError("INVALID_SQL_ANCHOR", str(exc)) from exc
            rebindings = [*thread.rebindings, {"base_artifact_id": thread.base_artifact_id, "anchor": thread.anchor, "replaced_by": actor_id, "replaced_at": current_timestamp()}]
            changed = (
                BusinessDocumentSqlQueryThread.update(
                    rebindings=rebindings,
                    base_artifact_id=artifact.id,
                    anchor=anchor,
                    version=thread.version + 1,
                    update_time=current_timestamp(),
                    update_date=datetime.now(),
                )
                .where((BusinessDocumentSqlQueryThread.id == thread.id) & (BusinessDocumentSqlQueryThread.version == thread.version) & (BusinessDocumentSqlQueryThread.status == "OPEN"))
                .execute()
            )
            if changed != 1:
                raise ConflictError("SQL_THREAD_VERSION_CONFLICT", "Discussion changed; reload it")
            thread = BusinessDocumentSqlQueryThread.get_by_id(thread.id)
            response = {
                "id": thread.id,
                "target": thread.target,
                "base_artifact_id": artifact.id,
                "anchor": anchor,
                "anchor_status": "CURRENT",
                "messages": thread.messages,
                "rebindings": thread.rebindings,
                "status": thread.status,
                "version": thread.version,
            }
            BusinessDocumentSqlAgentService._record_command(tenant_id, project_id, key, request_hash, response)
            return response

    @classmethod
    def reply(cls, tenant_id: str, actor_id: str, project_id: str, thread_id: str, raw: object, is_admin: bool = False, access_role: str = "AUTHOR_CREATOR") -> dict[str, Any]:
        data = _payload(raw, {"expected_thread_version", "idempotency_key", "text"})
        key = _key(data)
        request_hash = _stable_hash({"type": "REPLY_SQL_THREAD", "thread_id": thread_id, "request": data})
        with BusinessDocumentSqlQueryProject._meta.database.atomic():
            cls._project(tenant_id, actor_id, project_id, is_admin, access_role)
            replay = BusinessDocumentSqlAgentService._command_replay(tenant_id, project_id, key, request_hash)
            if replay is not None:
                return replay
            thread = BusinessDocumentSqlQueryThread.get_or_none(
                (BusinessDocumentSqlQueryThread.id == thread_id) & (BusinessDocumentSqlQueryThread.project_id == project_id) & (BusinessDocumentSqlQueryThread.tenant_id == tenant_id)
            )
            if thread is None:
                raise BusinessDocumentError("SQL_THREAD_NOT_FOUND", "Comment thread not found", 404)
            if thread.status != "OPEN" or thread.version != data.get("expected_thread_version"):
                raise ConflictError("SQL_THREAD_VERSION_CONFLICT", "Discussion changed; reload it")
            messages = [*thread.messages, _message(data.get("text"), actor_id)]
            changed = (
                BusinessDocumentSqlQueryThread.update(
                    messages=messages,
                    version=thread.version + 1,
                    update_time=current_timestamp(),
                    update_date=datetime.now(),
                )
                .where((BusinessDocumentSqlQueryThread.id == thread.id) & (BusinessDocumentSqlQueryThread.version == thread.version) & (BusinessDocumentSqlQueryThread.status == "OPEN"))
                .execute()
            )
            if changed != 1:
                raise ConflictError("SQL_THREAD_VERSION_CONFLICT", "Discussion changed; reload it")
            thread = BusinessDocumentSqlQueryThread.get_by_id(thread.id)
            response = {"id": thread.id, "version": thread.version, "messages": thread.messages, "status": thread.status}
            BusinessDocumentSqlAgentService._record_command(tenant_id, project_id, key, request_hash, response)
            return response

    @classmethod
    def request_change(
        cls,
        tenant_id: str,
        actor_id: str,
        project_id: str,
        thread_id: str,
        raw: object,
        is_admin: bool = False,
        access_role: str = "AUTHOR_CREATOR",
        *,
        proposed_schema: Mapping[str, Any] | None = None,
        source_text: str | None = None,
    ) -> dict[str, Any]:
        data = _payload(raw, {"expected_state_version", "idempotency_key", "additional_entity_ids"})
        additional = data.get("additional_entity_ids", [])
        if (
            not isinstance(additional, list)
            or len(additional) > 8
            or any(not isinstance(item, str) or not item for item in additional)
            or len(set(additional)) != len(additional)
            or (additional and proposed_schema is None)
        ):
            raise ValidationError("INVALID_SQL_SCHEMA_CHANGE", "Additional tables require verified catalog evidence")
        version, key = _version(data), _key(data)
        request_identity = {"type": "REQUEST_SQL_REVISION", "thread_id": thread_id, "request": data}
        if source_text is not None:
            if not isinstance(source_text, str) or not source_text.strip() or len(source_text) > 20_000:
                raise ValidationError("INVALID_SQL_REVISION", "A bounded human source selection is required")
            request_identity["source_text"] = source_text
        request_hash = _stable_hash(request_identity)
        with BusinessDocumentSqlQueryProject._meta.database.atomic():
            project = cls._project(tenant_id, actor_id, project_id, is_admin, access_role)
            replay = BusinessDocumentSqlAgentService._command_replay(tenant_id, project_id, key, request_hash)
            if replay is not None:
                return replay
            if project.state_version != version or project.operation_state != "IDLE":
                raise ConflictError("SQL_PROJECT_VERSION_CONFLICT", "Project changed or is busy")
            thread = BusinessDocumentSqlQueryThread.get_or_none(
                (BusinessDocumentSqlQueryThread.id == thread_id) & (BusinessDocumentSqlQueryThread.project_id == project_id) & (BusinessDocumentSqlQueryThread.tenant_id == tenant_id)
            )
            if thread is None or thread.status != "OPEN":
                raise ConflictError("SQL_THREAD_UNAVAILABLE", "Discussion is unavailable")
            artifact, sql = cls._source(project, thread.target)
            compiled_result = {}
            pinned_source = None
            if thread.target == "QUERY" and thread.anchor.get("pinned_compilation_id"):
                from api.apps.business_documents.sql_query_access import authorize_sql_source

                pinned_source = authorize_sql_source(project, actor_id, thread.anchor["pinned_compilation_id"], is_admin=is_admin, access_role=access_role, include_schema_tables=True)
                if pinned_source.query.id != thread.base_artifact_id:
                    raise ConflictError("SQL_HISTORY_SOURCE_MISMATCH", "Selected query does not match its compilation")
                artifact, sql, compiled_result = pinned_source.query, pinned_source.compiled["sql"], pinned_source.compiled
                if proposed_schema is None and pinned_source.schema.id != project.schema_artifact_id:
                    proposed_schema = pinned_source.schema.payload
            elif thread.target == "QUERY" and thread.anchor.get("pinned_revision"):
                artifact, sql, compiled_result = cls._query_revision(project, thread.base_artifact_id)
            if thread.target == "DOCUMENT":
                artifact = BusinessDocumentSqlQueryArtifact.get_or_none(
                    (BusinessDocumentSqlQueryArtifact.id == thread.base_artifact_id)
                    & (BusinessDocumentSqlQueryArtifact.project_id == project_id)
                    & (BusinessDocumentSqlQueryArtifact.tenant_id == tenant_id)
                    & (BusinessDocumentSqlQueryArtifact.kind == "DOCUMENT")
                )
            if artifact is None:
                raise ConflictError("SQL_THREAD_STALE", "Accepted revision is unavailable")
            if thread.target == "QUERY" and not sql:
                raise ConflictError("SQL_REVISION_SOURCE_MISSING", "Current SQL is unavailable")
            location = locate_anchor(thread.anchor, same_revision=artifact.id == thread.base_artifact_id, sql=sql, blocks=query_blocks(sql) if sql else [])
            if location["status"] != "CURRENT":
                raise ConflictError("SQL_THREAD_STALE", "Reattach the comment to the current revision")
            if (
                thread.target == "QUERY"
                and BusinessDocumentSqlQueryRun.select()
                .where(
                    (BusinessDocumentSqlQueryRun.project_id == project_id) & (BusinessDocumentSqlQueryRun.tenant_id == tenant_id) & (BusinessDocumentSqlQueryRun.status.in_(IN_PROGRESS_RUN_STATUSES))
                )
                .exists()
            ):
                raise ConflictError("SQL_RESULT_ACTIVE", "Finish or cancel the current result before revising SQL")
            current = BusinessDocumentSqlAgentService._project(project)
            result = current.get("compilation", {}).get("result") if current.get("compilation") else None
            query_artifact, _ = cls._source(project, "QUERY")
            if thread.target == "QUERY" and (thread.anchor.get("pinned_revision") or pinned_source is not None):
                query_artifact = artifact
                result = compiled_result
            parameters = _parameters(query_artifact, result or {})
            messages = [dict(message) for message in thread.messages]
            if source_text is not None:
                messages[0]["catalog_source_text"] = source_text
            human = [message for message in messages if message.get("role") != "assistant" and message.get("author_id") != "assistant"]
            original_source_text = messages[0].get("catalog_source_text") if messages else None
            catalog_source_text = "\n".join([original_source_text, *[message["text"] for message in human[1:]]]) if original_source_text is not None else human[-1]["text"] if human else ""
            request = {
                "target": thread.target,
                "base_artifact_id": artifact.id,
                "base_requirements_artifact_id": project.requirements_artifact_id,
                "base_schema_artifact_id": project.schema_artifact_id,
                "base_query_artifact_id": project.query_artifact_id,
                "thread_id": thread.id,
                "thread_version": thread.version + 1,
                "anchor": location["anchor"],
                "comments": [{key: value for key, value in message.items() if key != "catalog_source_text"} for message in messages],
                "catalog_source_text": catalog_source_text,
                "sql": sql,
                "parameters": parameters,
                "requirements": pinned_source.requirements.payload if pinned_source is not None else (current.get("artifacts") or {}).get("requirements"),
                "schema": proposed_schema if proposed_schema is not None else pinned_source.schema.payload if pinned_source is not None else (current.get("artifacts") or {}).get("schema"),
                "proposed_schema": proposed_schema,
                "pinned_compilation_id": pinned_source.compilation.id if pinned_source is not None else None,
                "document": artifact.payload if thread.target == "DOCUMENT" else None,
                "locale": project.locale,
            }
            changed_thread = (
                BusinessDocumentSqlQueryThread.update(
                    messages=messages,
                    version=thread.version + 1,
                    update_time=current_timestamp(),
                    update_date=datetime.now(),
                )
                .where((BusinessDocumentSqlQueryThread.id == thread.id) & (BusinessDocumentSqlQueryThread.version == thread.version) & (BusinessDocumentSqlQueryThread.status == "OPEN"))
                .execute()
            )
            if changed_thread != 1:
                raise ConflictError("SQL_THREAD_VERSION_CONFLICT", "Discussion changed; reload before requesting a proposal")
            job_id = get_uuid()
            new_version = version + 1
            changed = (
                BusinessDocumentSqlQueryProject.update(
                    operation_state="RUNNING", current_job_id=job_id, state_version=new_version, last_error=None, update_time=current_timestamp(), update_date=datetime.now()
                )
                .where((BusinessDocumentSqlQueryProject.id == project_id) & (BusinessDocumentSqlQueryProject.state_version == version) & (BusinessDocumentSqlQueryProject.operation_state == "IDLE"))
                .execute()
            )
            if changed != 1:
                raise ConflictError("SQL_PROJECT_VERSION_CONFLICT", "Project changed during request")
            BusinessDocumentJob.create(
                id=job_id,
                document_id=project_id,
                tenant_id=tenant_id,
                job_type="SQL_AGENT_REVISION",
                dedupe_key=_stable_hash({"project_id": project_id, "thread_id": thread_id, "version": new_version}),
                source_state_version=new_version,
                payload={"schema_version": "1", "project_id": project_id, "actor_id": actor_id, "request": request},
                available_at=current_timestamp(),
                correlation_id=get_uuid(),
            )
            response = BusinessDocumentSqlAgentService.get_project(tenant_id, actor_id, project_id, is_admin, access_role)
            BusinessDocumentSqlAgentService._record_command(tenant_id, project_id, key, request_hash, response)
            return response

    @classmethod
    def cancel_change(cls, tenant_id: str, actor_id: str, project_id: str, raw: object, is_admin: bool = False, access_role: str = "AUTHOR_CREATOR") -> dict[str, Any]:
        data = _payload(raw, {"expected_state_version", "idempotency_key"})
        version, key = _version(data), _key(data)
        request_hash = _stable_hash({"type": "CANCEL_SQL_REVISION", "request": data})
        with BusinessDocumentSqlQueryProject._meta.database.atomic():
            project = cls._project(tenant_id, actor_id, project_id, is_admin, access_role)
            replay = BusinessDocumentSqlAgentService._command_replay(tenant_id, project_id, key, request_hash)
            if replay is not None:
                return replay
            job = BusinessDocumentJob.get_or_none((BusinessDocumentJob.id == project.current_job_id) & (BusinessDocumentJob.document_id == project_id) & (BusinessDocumentJob.tenant_id == tenant_id))
            if project.state_version != version or project.operation_state != "RUNNING" or job is None or job.job_type != "SQL_AGENT_REVISION":
                raise ConflictError("SQL_PROJECT_VERSION_CONFLICT", "No active revision proposal to cancel")
            changed = (
                BusinessDocumentSqlQueryProject.update(
                    operation_state="IDLE",
                    current_job_id=None,
                    state_version=version + 1,
                    last_error=None,
                    update_time=current_timestamp(),
                    update_date=datetime.now(),
                )
                .where((BusinessDocumentSqlQueryProject.id == project_id) & (BusinessDocumentSqlQueryProject.state_version == version) & (BusinessDocumentSqlQueryProject.current_job_id == job.id))
                .execute()
            )
            if changed != 1:
                raise ConflictError("SQL_PROJECT_VERSION_CONFLICT", "Project changed during cancellation")
            BusinessDocumentJob.update(status="STALE", progress_stage="CANCELLED", progress_message="Предложение отменено", update_time=current_timestamp(), update_date=datetime.now()).where(
                (BusinessDocumentJob.id == job.id) & (BusinessDocumentJob.status.in_(("PENDING", "RETRY")))
            ).execute()
            response = BusinessDocumentSqlAgentService.get_project(tenant_id, actor_id, project_id, is_admin, access_role)
            BusinessDocumentSqlAgentService._record_command(tenant_id, project_id, key, request_hash, response)
            return response

    @classmethod
    def reopen_schema(cls, tenant_id: str, actor_id: str, project_id: str, raw: object, is_admin: bool = False, access_role: str = "AUTHOR_CREATOR") -> dict[str, Any]:
        data = _payload(raw, {"expected_state_version", "idempotency_key"})
        version, key = _version(data), _key(data)
        request_hash = _stable_hash({"type": "REOPEN_SQL_SCHEMA", "request": data})
        with BusinessDocumentSqlQueryProject._meta.database.atomic():
            project = cls._project(tenant_id, actor_id, project_id, is_admin, access_role)
            replay = BusinessDocumentSqlAgentService._command_replay(tenant_id, project_id, key, request_hash)
            if replay is not None:
                return replay
            if project.state_version != version or project.operation_state not in {"IDLE", "REVIEW"} or not project.requirements_artifact_id:
                raise ConflictError("SQL_PROJECT_VERSION_CONFLICT", "Project is not ready to review its schema")
            if project.operation_state == "REVIEW":
                pending = (
                    BusinessDocumentSqlAgentProposal.select()
                    .where(
                        (BusinessDocumentSqlAgentProposal.project_id == project_id) & (BusinessDocumentSqlAgentProposal.tenant_id == tenant_id) & (BusinessDocumentSqlAgentProposal.status == "PENDING")
                    )
                    .order_by(BusinessDocumentSqlAgentProposal.create_time.desc())
                    .first()
                )
                if pending is None or pending.kind != "REVISION" or pending.payload.get("agent_result", {}).get("proposal", {}).get("checks", {}).get("status") not in {"BLOCKED", "NEEDS_SCHEMA"}:
                    raise ConflictError("SQL_PROJECT_VERSION_CONFLICT", "Decide the active proposal before reviewing schema")
                pending.status = "REJECTED"
                pending.decided_by = actor_id
                pending.decided_at = current_timestamp()
                pending.save()
            if (
                BusinessDocumentSqlQueryRun.select()
                .where(
                    (BusinessDocumentSqlQueryRun.project_id == project_id) & (BusinessDocumentSqlQueryRun.tenant_id == tenant_id) & (BusinessDocumentSqlQueryRun.status.in_(IN_PROGRESS_RUN_STATUSES))
                )
                .exists()
            ):
                raise ConflictError("SQL_RESULT_ACTIVE", "Finish or cancel the current result before changing schema")
            query, sql = cls._source(project, "QUERY")
            if query is not None and sql:
                compilation = BusinessDocumentSqlAgentService._project(project).get("compilation") or {}
                params = _parameters(query, compilation.get("result") or {})
                steps = [{"title": "Прежний проверенный SQL — адаптируйте к новой схеме", "sql": sql, "parameters": json.dumps(params, ensure_ascii=False, indent=2)}]
                draft = BusinessDocumentSqlQueryDraft.get_or_none(BusinessDocumentSqlQueryDraft.project_id == project_id)
                if draft is None:
                    try:
                        BusinessDocumentSqlQueryDraft.create(id=get_uuid(), project_id=project_id, tenant_id=tenant_id, base_query_artifact_id=None, version=1, steps=steps)
                    except IntegrityError as exc:
                        raise ConflictError("SQL_DRAFT_VERSION_CONFLICT", "Draft changed; reload it") from exc
                else:
                    changed_draft = (
                        BusinessDocumentSqlQueryDraft.update(
                            base_query_artifact_id=None,
                            version=draft.version + 1,
                            update_time=current_timestamp(),
                            update_date=datetime.now(),
                        )
                        .where((BusinessDocumentSqlQueryDraft.id == draft.id) & (BusinessDocumentSqlQueryDraft.version == draft.version))
                        .execute()
                    )
                    if changed_draft != 1:
                        raise ConflictError("SQL_DRAFT_VERSION_CONFLICT", "Draft changed; reload it")
            changed = (
                BusinessDocumentSqlQueryProject.update(
                    schema_artifact_id=None,
                    query_artifact_id=None,
                    stage="SCHEMA",
                    operation_state="IDLE",
                    current_job_id=None,
                    last_error=None,
                    state_version=version + 1,
                    update_time=current_timestamp(),
                    update_date=datetime.now(),
                )
                .where(
                    (BusinessDocumentSqlQueryProject.id == project_id)
                    & (BusinessDocumentSqlQueryProject.state_version == version)
                    & (BusinessDocumentSqlQueryProject.operation_state == project.operation_state)
                )
                .execute()
            )
            if changed != 1:
                raise ConflictError("SQL_PROJECT_VERSION_CONFLICT", "Project changed during schema review")
            response = BusinessDocumentSqlAgentService.get_project(tenant_id, actor_id, project_id, is_admin, access_role)
            BusinessDocumentSqlAgentService._record_command(tenant_id, project_id, key, request_hash, response)
            return response

    @classmethod
    def decide_change(cls, tenant_id: str, actor_id: str, project_id: str, proposal_id: str, raw: object, is_admin: bool = False, access_role: str = "AUTHOR_CREATOR") -> dict[str, Any]:
        data = _payload(raw, {"expected_state_version", "idempotency_key", "decision", "confirmed_no_new_facts"})
        version, key = _version(data), _key(data)
        decision = data.get("decision")
        if decision not in {"ACCEPT", "REJECT"}:
            raise ValidationError("INVALID_SQL_REVISION", "Decision must be ACCEPT or REJECT")
        request_hash = _stable_hash({"type": "DECIDE_SQL_REVISION", "proposal_id": proposal_id, "request": data})
        with BusinessDocumentSqlQueryProject._meta.database.atomic():
            project = cls._project(tenant_id, actor_id, project_id, is_admin, access_role)
            replay = BusinessDocumentSqlAgentService._command_replay(tenant_id, project_id, key, request_hash)
            if replay is not None:
                return replay
            if project.state_version != version or project.operation_state != "REVIEW":
                raise ConflictError("SQL_PROJECT_VERSION_CONFLICT", "Project changed; reload before deciding")
            proposal = BusinessDocumentSqlAgentProposal.get_or_none(
                (BusinessDocumentSqlAgentProposal.id == proposal_id)
                & (BusinessDocumentSqlAgentProposal.project_id == project_id)
                & (BusinessDocumentSqlAgentProposal.tenant_id == tenant_id)
                & (BusinessDocumentSqlAgentProposal.kind == "REVISION")
                & (BusinessDocumentSqlAgentProposal.status == "PENDING")
            )
            if proposal is None:
                raise ConflictError("SQL_PROPOSAL_UNAVAILABLE", "Revision proposal is unavailable")
            result = proposal.payload.get("agent_result", {}).get("proposal", {})
            source = proposal.payload.get("agent_result", {}).get("source", {})
            target = source.get("target")
            artifact, _ = cls._source(project, target)
            pinned_source = None
            if target == "QUERY" and source.get("pinned_compilation_id") and decision == "ACCEPT":
                from api.apps.business_documents.sql_query_access import authorize_sql_source

                pinned_source = authorize_sql_source(project, actor_id, source["pinned_compilation_id"], is_admin=is_admin, access_role=access_role, include_schema_tables=True)
                artifact = pinned_source.query
            elif target == "QUERY" and source.get("base_artifact_id") != project.query_artifact_id:
                artifact, _, _ = cls._query_revision(project, source.get("base_artifact_id"))
            if target == "DOCUMENT":
                artifact = BusinessDocumentSqlQueryArtifact.get_or_none(
                    (BusinessDocumentSqlQueryArtifact.id == source.get("base_artifact_id"))
                    & (BusinessDocumentSqlQueryArtifact.project_id == project_id)
                    & (BusinessDocumentSqlQueryArtifact.tenant_id == tenant_id)
                    & (BusinessDocumentSqlQueryArtifact.kind == "DOCUMENT")
                )
            if artifact is None or artifact.id != source.get("base_artifact_id"):
                raise ConflictError("SQL_PROPOSAL_STALE", "Accepted revision changed; request a new proposal")
            if decision == "ACCEPT":
                thread = BusinessDocumentSqlQueryThread.get_or_none(
                    (BusinessDocumentSqlQueryThread.id == source.get("thread_id")) & (BusinessDocumentSqlQueryThread.project_id == project_id) & (BusinessDocumentSqlQueryThread.tenant_id == tenant_id)
                )
                if thread is None or thread.version != source.get("thread_version"):
                    raise ConflictError("SQL_THREAD_VERSION_CONFLICT", "Discussion changed; reject this proposal and request a new one")
            values: dict[str, Any] = {}
            if decision == "ACCEPT" and target == "QUERY":
                if result.get("checks", {}).get("status") != "PASS":
                    raise ValidationError("SQL_QUERY_BLOCKED", "Proposed SQL did not pass the catalog guard")
                if (
                    BusinessDocumentSqlQueryRun.select()
                    .where(
                        (BusinessDocumentSqlQueryRun.project_id == project_id)
                        & (BusinessDocumentSqlQueryRun.tenant_id == tenant_id)
                        & (BusinessDocumentSqlQueryRun.status.in_(IN_PROGRESS_RUN_STATUSES))
                    )
                    .exists()
                ):
                    raise ConflictError("SQL_RESULT_ACTIVE", "Finish or cancel the active result before accepting SQL")
                requirements = BusinessDocumentSqlAgentService._artifact(project.requirements_artifact_id, "REQUIREMENTS")
                schema = BusinessDocumentSqlAgentService._artifact(project.schema_artifact_id, "SCHEMA")
                if source.get("base_schema_artifact_id") != project.schema_artifact_id or source.get("base_requirements_artifact_id") != project.requirements_artifact_id:
                    raise ConflictError("SQL_PROPOSAL_STALE", "Requirements or schema changed; prepare a new proposal")
                if pinned_source is not None:
                    requirements = pinned_source.requirements
                    values["requirements_artifact_id"] = requirements.id
                proposed_schema = source.get("proposed_schema")
                schema_payload = proposed_schema if isinstance(proposed_schema, Mapping) else schema.payload
                candidate = {"mode": "manual", "sql": result.get("sql"), "parameters": result.get("parameters"), "confirmed_alignment": True}
                try:
                    compiled = compile_query_payload(build_project_compile_command(requirements.payload, schema_payload, candidate))
                except (ProjectCompilationError, QuerySpecificationValidationError, SqlGuardError) as exc:
                    raise ValidationError("SQL_QUERY_BLOCKED", "Proposed SQL did not pass current checks", {"reason": str(exc)}) from exc
                if compiled.get("status") != "READY" or compiled.get("guard", {}).get("status") != "PASS":
                    raise ValidationError("SQL_QUERY_BLOCKED", "Proposed SQL did not pass current checks")
                if proposed_schema is not None:
                    previous_entities = {table.id for table in parse_schema_snapshot(schema.payload["schema_snapshot"]).tables}
                    current_entities = {table.id for table in parse_schema_snapshot(schema_payload["schema_snapshot"]).tables}
                    if previous_entities - current_entities:
                        values["execution_profile_id"] = None
                    schema = BusinessDocumentSqlQueryArtifact.create(
                        id=get_uuid(),
                        project_id=project_id,
                        tenant_id=tenant_id,
                        kind="SCHEMA",
                        revision=BusinessDocumentSqlQueryArtifact.select()
                        .where((BusinessDocumentSqlQueryArtifact.project_id == project_id) & (BusinessDocumentSqlQueryArtifact.kind == "SCHEMA"))
                        .count()
                        + 1,
                        payload=schema_payload,
                        content_hash=_stable_hash(schema_payload),
                        source_proposal_id=proposal.id,
                        accepted_by=actor_id,
                    )
                    values["schema_artifact_id"] = schema.id
                revision = BusinessDocumentSqlQueryArtifact.select().where((BusinessDocumentSqlQueryArtifact.project_id == project_id) & (BusinessDocumentSqlQueryArtifact.kind == "QUERY")).count() + 1
                query = BusinessDocumentSqlQueryArtifact.create(
                    id=get_uuid(),
                    project_id=project_id,
                    tenant_id=tenant_id,
                    kind="QUERY",
                    revision=revision,
                    payload=candidate,
                    content_hash=_stable_hash(candidate),
                    source_proposal_id=proposal.id,
                    accepted_by=actor_id,
                )
                compilation_payload = {"result": compiled, "source_artifact_ids": [requirements.id, schema.id, query.id], "source_state_version": version}
                compilation_revision = (
                    BusinessDocumentSqlQueryArtifact.select().where((BusinessDocumentSqlQueryArtifact.project_id == project_id) & (BusinessDocumentSqlQueryArtifact.kind == "COMPILATION")).count() + 1
                )
                BusinessDocumentSqlQueryArtifact.create(
                    id=get_uuid(),
                    project_id=project_id,
                    tenant_id=tenant_id,
                    kind="COMPILATION",
                    revision=compilation_revision,
                    payload=compilation_payload,
                    content_hash=_stable_hash(compilation_payload),
                    source_proposal_id=proposal.id,
                    accepted_by=actor_id,
                )
                values["query_artifact_id"] = query.id
                values["stage"] = "COMPLETE"
            elif decision == "ACCEPT" and target == "DOCUMENT":
                if result.get("checks", {}).get("status") != "PASS":
                    raise ValidationError("SQL_DOCUMENT_RUN_REQUIRED", "This document change requires a new verified run")
                if data.get("confirmed_no_new_facts") is not True:
                    raise ValidationError("SQL_DOCUMENT_REVIEW_REQUIRED", "Confirm that the wording introduces no new facts")
                old_text = str((artifact.payload.get("confirmed_conclusion") or {}).get("text") or "")
                new_text = result.get("document_text")
                if not isinstance(new_text, str) or not new_text.strip() or not preserves_document_facts(old_text, new_text):
                    raise ValidationError("SQL_DOCUMENT_FACTS_CHANGED", "A data-dependent change requires a new verified run")
                payload = {
                    **artifact.payload,
                    "confirmed_conclusion": {**(artifact.payload.get("confirmed_conclusion") or {}), "text": new_text.strip()},
                    "text_revision_of": artifact.id,
                    "wording_review_confirmed_by": actor_id,
                }
                revision = (
                    BusinessDocumentSqlQueryArtifact.select().where((BusinessDocumentSqlQueryArtifact.project_id == project_id) & (BusinessDocumentSqlQueryArtifact.kind == "DOCUMENT")).count() + 1
                )
                BusinessDocumentSqlQueryArtifact.create(
                    id=get_uuid(),
                    project_id=project_id,
                    tenant_id=tenant_id,
                    kind="DOCUMENT",
                    revision=revision,
                    payload=payload,
                    content_hash=_stable_hash(payload),
                    source_proposal_id=proposal.id,
                    accepted_by=actor_id,
                )
            if decision == "ACCEPT":
                changed_thread = (
                    BusinessDocumentSqlQueryThread.update(version=BusinessDocumentSqlQueryThread.version + 1)
                    .where(
                        (BusinessDocumentSqlQueryThread.id == source.get("thread_id"))
                        & (BusinessDocumentSqlQueryThread.project_id == project_id)
                        & (BusinessDocumentSqlQueryThread.tenant_id == tenant_id)
                        & (BusinessDocumentSqlQueryThread.version == source.get("thread_version"))
                        & (BusinessDocumentSqlQueryThread.status == "OPEN")
                    )
                    .execute()
                )
                if changed_thread != 1:
                    raise ConflictError("SQL_THREAD_VERSION_CONFLICT", "Discussion changed; reject this proposal and request a new one")
            values.update(operation_state="IDLE", current_job_id=None, last_error=None, state_version=version + 1, update_time=current_timestamp(), update_date=datetime.now())
            changed = (
                BusinessDocumentSqlQueryProject.update(**values)
                .where((BusinessDocumentSqlQueryProject.id == project_id) & (BusinessDocumentSqlQueryProject.state_version == version) & (BusinessDocumentSqlQueryProject.operation_state == "REVIEW"))
                .execute()
            )
            if changed != 1:
                raise ConflictError("SQL_PROJECT_VERSION_CONFLICT", "Project changed during decision")
            proposal.status = "ACCEPTED" if decision == "ACCEPT" else "REJECTED"
            proposal.decided_by = actor_id
            proposal.decided_at = current_timestamp()
            proposal.save()
            response = BusinessDocumentSqlAgentService.get_project(tenant_id, actor_id, project_id, is_admin, access_role)
            BusinessDocumentSqlAgentService._record_command(tenant_id, project_id, key, request_hash, response)
            return response
