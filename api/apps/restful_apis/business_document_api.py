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

"""HTTP boundary for governed business requirements documents."""

import asyncio
import json
from urllib.parse import quote

from quart import Response, jsonify, request
from werkzeug.exceptions import BadRequest

from api.apps import current_user, login_required
from api.apps.business_documents.adapters.assignment import assign_business_document
from api.apps.business_documents.authorization import BusinessDocumentAccess
from business_documents.application.errors import BusinessDocumentError
from api.apps.business_documents.eva_changes import EvaDocumentChangeService
from api.apps.business_documents.exports import BusinessDocumentExportService
from api.apps.business_documents.runtime import document_queries
from api.apps.business_documents.runtime import document_commands
from api.apps.business_documents.runtime import eva_synchronization
from api.apps.business_documents.runtime import document_creation
from api.apps.business_documents.runtime import document_deletion, change_user_role
from api.apps.business_documents.sql_execution_registry import BusinessDocumentSqlExecutionRegistryService
from api.apps.business_documents.sql_query_agents import BusinessDocumentSqlAgentService
from api.apps.business_documents.sql_query_lifecycle import BusinessDocumentSqlQueryService
from api.apps.business_documents.sql_query_planner import BusinessDocumentSqlQueryPlanningService
from api.apps.business_documents.sql_query_schema import BusinessDocumentSqlQuerySchemaService
from api.apps.business_documents.worker import wake_business_document_worker
from api.utils.api_utils import get_request_json
from common.misc_utils import thread_pool_exec


def _success(data, status=200):
    return jsonify({"code": 0, "data": data}), status


def _error(error: BusinessDocumentError):
    payload = {
        "code": error.status,
        "message": error.message,
        "data": {"error_code": error.code, "details": error.details},
    }
    return jsonify(payload), error.status


def _is_admin() -> bool:
    return bool(getattr(current_user, "is_superuser", False))


def _access_role() -> str:
    return str(getattr(current_user, "business_document_role", "AUTHOR_CREATOR") or "AUTHOR_CREATOR")


@manager.route("/business-documents/sql-query/projects", methods=["POST"])  # noqa: F821
@login_required
async def create_business_document_sql_query_project():
    try:
        data = await get_request_json()
        actor_id = str(current_user.id)
        result = await thread_pool_exec(
            BusinessDocumentSqlAgentService.create_project,
            actor_id,
            actor_id,
            data,
            _is_admin(),
            _access_role(),
        )
        return _success(result, 201)
    except (AttributeError, TypeError, BadRequest):
        return _error(BusinessDocumentError("INVALID_SQL_AGENT_PROJECT", "Request body must be a valid JSON object", 422))
    except BusinessDocumentError as error:
        return _error(error)


@manager.route("/business-documents/sql-query/projects", methods=["GET"])  # noqa: F821
@login_required
async def list_business_document_sql_query_projects():
    try:
        actor_id = str(current_user.id)
        result = await thread_pool_exec(
            BusinessDocumentSqlAgentService.list_projects,
            actor_id,
            actor_id,
            _is_admin(),
            _access_role(),
        )
        return _success(result)
    except BusinessDocumentError as error:
        return _error(error)


@manager.route("/business-documents/sql-query/projects/<project_id>", methods=["GET"])  # noqa: F821
@login_required
async def get_business_document_sql_query_project(project_id: str):
    try:
        actor_id = str(current_user.id)
        result = await thread_pool_exec(
            BusinessDocumentSqlAgentService.get_project,
            actor_id,
            actor_id,
            project_id,
            _is_admin(),
            _access_role(),
        )
        return _success(result)
    except BusinessDocumentError as error:
        return _error(error)


@manager.route("/business-documents/sql-query/projects/<project_id>/agent-jobs", methods=["POST"])  # noqa: F821
@login_required
async def request_business_document_sql_agent(project_id: str):
    try:
        data = await get_request_json()
        actor_id = str(current_user.id)
        result = await thread_pool_exec(
            BusinessDocumentSqlAgentService.request_agent,
            actor_id,
            actor_id,
            project_id,
            data,
            _is_admin(),
            _access_role(),
        )
        wake_business_document_worker()
        return _success(result, 202)
    except (AttributeError, TypeError, BadRequest):
        return _error(BusinessDocumentError("INVALID_SQL_AGENT_REQUEST", "Request body must be a valid JSON object", 422))
    except BusinessDocumentError as error:
        return _error(error)


@manager.route(  # noqa: F821
    "/business-documents/sql-query/projects/<project_id>/proposals/<proposal_id>/decision",
    methods=["POST"],
)
@login_required
async def decide_business_document_sql_agent_proposal(project_id: str, proposal_id: str):
    try:
        data = await get_request_json()
        actor_id = str(current_user.id)
        result = await thread_pool_exec(
            BusinessDocumentSqlAgentService.decide_proposal,
            actor_id,
            actor_id,
            project_id,
            proposal_id,
            data,
            _is_admin(),
            _access_role(),
        )
        return _success(result)
    except (AttributeError, TypeError, BadRequest):
        return _error(BusinessDocumentError("INVALID_SQL_AGENT_DECISION", "Request body must be a valid JSON object", 422))
    except BusinessDocumentError as error:
        return _error(error)


@manager.route("/business-documents/eva/sources", methods=["GET"])  # noqa: F821
@login_required
async def search_eva_business_document_sources():
    try:
        actor_id = current_user.id
        query = request.args.get("query", "")
        limit = int(request.args.get("limit", 20))
        result = await thread_pool_exec(EvaDocumentChangeService.search_sources, actor_id, query, limit)
        return _success(result)
    except (TypeError, ValueError):
        return _error(BusinessDocumentError("INVALID_EVA_SEARCH", "limit must be an integer", 422))
    except BusinessDocumentError as error:
        return _error(error)


@manager.route("/business-documents/eva/changes", methods=["POST"])  # noqa: F821
@login_required
async def create_eva_business_document_change():
    try:
        data = await get_request_json()
        if not data:
            raise BusinessDocumentError("INVALID_EVA_CHANGE", "Request body must be a valid JSON object", 422)
        if "draft_markdown" in data:
            raise BusinessDocumentError(
                "MANUAL_EVA_DRAFT_DISABLED",
                "Нельзя передать готовый текст доработки EVA: его формирует агент по задаче автора.",
                422,
            )
        actor_id = current_user.id
        result = await thread_pool_exec(EvaDocumentChangeService.create_change, actor_id, actor_id, data)
        try:
            result = await thread_pool_exec(
                EvaDocumentChangeService.generate_draft,
                actor_id,
                actor_id,
                result["change_id"],
                {"expected_state_version": result["state_version"]},
            )
        except BusinessDocumentError:
            # The pinned request remains resumable and exposes the generation
            # error plus an explicit retry action in its projection.
            result = await thread_pool_exec(
                EvaDocumentChangeService.get_change,
                actor_id,
                actor_id,
                result["change_id"],
            )
        return _success(result, 201)
    except (AttributeError, TypeError, BadRequest):
        return _error(BusinessDocumentError("INVALID_EVA_CHANGE", "Request body must be a valid JSON object", 422))
    except BusinessDocumentError as error:
        return _error(error)


@manager.route("/business-documents/eva/changes", methods=["GET"])  # noqa: F821
@login_required
async def list_eva_business_document_changes():
    try:
        actor_id = current_user.id
        page = int(request.args.get("page", 1))
        page_size = int(request.args.get("page_size", 20))
        result = await thread_pool_exec(EvaDocumentChangeService.list_changes, actor_id, actor_id, page, page_size)
        return _success(result)
    except (TypeError, ValueError):
        return _error(BusinessDocumentError("INVALID_PAGINATION", "page and page_size must be integers", 422))
    except BusinessDocumentError as error:
        return _error(error)


@manager.route("/business-documents/eva/changes/<change_id>", methods=["GET"])  # noqa: F821
@login_required
async def get_eva_business_document_change(change_id):
    try:
        actor_id = current_user.id
        result = await thread_pool_exec(EvaDocumentChangeService.get_change, actor_id, actor_id, change_id)
        return _success(result)
    except BusinessDocumentError as error:
        return _error(error)


@manager.route("/business-documents/eva/changes/<change_id>/generate", methods=["POST"])  # noqa: F821
@login_required
async def generate_eva_business_document_change_draft(change_id):
    try:
        data = await get_request_json()
        if not data:
            raise BusinessDocumentError("INVALID_EVA_GENERATION", "Передайте параметры подготовки доработки в непустом JSON-объекте.", 422)
        actor_id = current_user.id
        result = await thread_pool_exec(EvaDocumentChangeService.generate_draft, actor_id, actor_id, change_id, data)
        return _success(result)
    except (AttributeError, TypeError, BadRequest):
        return _error(BusinessDocumentError("INVALID_EVA_GENERATION", "Передайте параметры подготовки доработки в непустом JSON-объекте.", 422))
    except BusinessDocumentError as error:
        return _error(error)


@manager.route("/business-documents/eva/changes/<change_id>/approve", methods=["POST"])  # noqa: F821
@login_required
async def approve_eva_business_document_change(change_id):
    try:
        data = await get_request_json()
        actor_id = current_user.id
        result = await thread_pool_exec(EvaDocumentChangeService.approve, actor_id, actor_id, change_id, data)
        return _success(result)
    except (AttributeError, TypeError, BadRequest):
        return _error(BusinessDocumentError("INVALID_EVA_CHANGE", "Request body must be a valid JSON object", 422))
    except BusinessDocumentError as error:
        return _error(error)


@manager.route("/business-documents/eva/changes/<change_id>/prepare", methods=["POST"])  # noqa: F821
@login_required
async def prepare_eva_business_document_change(change_id):
    try:
        data = await get_request_json()
        actor_id = current_user.id
        result = await thread_pool_exec(EvaDocumentChangeService.prepare_eva_draft, actor_id, actor_id, change_id, data)
        return _success(result)
    except (AttributeError, TypeError, BadRequest):
        return _error(BusinessDocumentError("INVALID_EVA_CHANGE", "Request body must be a valid JSON object", 422))
    except BusinessDocumentError as error:
        return _error(error)


@manager.route("/business-documents/eva/changes/<change_id>/publish", methods=["POST"])  # noqa: F821
@login_required
async def publish_eva_business_document_change(change_id):
    try:
        data = await get_request_json()
        actor_id = current_user.id
        result = await thread_pool_exec(EvaDocumentChangeService.publish, actor_id, actor_id, change_id, data)
        return _success(result)
    except (AttributeError, TypeError, BadRequest):
        return _error(BusinessDocumentError("INVALID_EVA_CHANGE", "Request body must be a valid JSON object", 422))
    except BusinessDocumentError as error:
        return _error(error)


@manager.route("/business-documents", methods=["POST"])  # noqa: F821
@login_required
async def create_business_document():
    try:
        data = await get_request_json()
        if not data:
            raise BusinessDocumentError("INVALID_DOCUMENT", "Request body must be a valid JSON object", 422)
        actor_id = current_user.id
        result = await thread_pool_exec(document_creation.execute, actor_id, actor_id, data, _is_admin(), _access_role())
        return _success(result, 201)
    except (AttributeError, TypeError, BadRequest):
        return _error(BusinessDocumentError("INVALID_DOCUMENT", "Request body must be a valid JSON object", 422))
    except BusinessDocumentError as error:
        return _error(error)


@manager.route("/business-documents", methods=["GET"])  # noqa: F821
@login_required
async def list_business_documents():
    try:
        actor_id = current_user.id
        page = int(request.args.get("page", 1))
        page_size = int(request.args.get("page_size", 20))
        scope = request.args.get("scope", "all")
        return _success(await thread_pool_exec(document_queries.list_documents, actor_id, page, page_size, _is_admin(), _access_role(), scope))
    except (TypeError, ValueError):
        return _error(BusinessDocumentError("INVALID_PAGINATION", "page and page_size must be integers", 422))
    except BusinessDocumentError as error:
        return _error(error)


@manager.route("/business-documents/capabilities", methods=["GET"])  # noqa: F821
@login_required
async def get_business_document_capabilities():
    try:
        access = BusinessDocumentAccess(
            actor_id=str(current_user.id),
            assigned_role=_access_role(),
            is_admin=_is_admin(),
        )
        return _success(
            {
                "access_role": access.role.value,
                "capabilities": access.capabilities(),
            }
        )
    except BusinessDocumentError as error:
        return _error(error)


@manager.route("/business-documents/catalog", methods=["GET"])  # noqa: F821
@login_required
async def list_business_document_catalog():
    try:
        return _success(await thread_pool_exec(document_queries.list_catalog))
    except BusinessDocumentError as error:
        return _error(error)


@manager.route("/business-documents/sql-query/schema/resolve", methods=["POST"])  # noqa: F821
@login_required
async def resolve_business_document_sql_schema():
    try:
        data = await get_request_json()
        actor_id = str(current_user.id)
        result = await BusinessDocumentSqlQuerySchemaService.resolve(
            actor_id,
            actor_id,
            data,
            _is_admin(),
            _access_role(),
        )
        return _success(result)
    except (AttributeError, TypeError, BadRequest):
        return _error(BusinessDocumentError("INVALID_SQL_SCHEMA_REQUEST", "Request body must be a valid JSON object", 422))
    except BusinessDocumentError as error:
        return _error(error)


@manager.route("/business-documents/sql-query/schema/entities", methods=["POST"])  # noqa: F821
@login_required
async def load_business_document_sql_schema_entities():
    try:
        data = await get_request_json()
        actor_id = str(current_user.id)
        result = await BusinessDocumentSqlQuerySchemaService.load_entities(
            actor_id,
            data,
            _is_admin(),
            _access_role(),
        )
        return _success(result)
    except (AttributeError, TypeError, BadRequest):
        return _error(
            BusinessDocumentError(
                "INVALID_SQL_SCHEMA_ENTITY_REQUEST",
                "Request body must be a valid JSON object",
                422,
            )
        )
    except BusinessDocumentError as error:
        return _error(error)


@manager.route("/business-documents/sql-query/compile", methods=["POST"])  # noqa: F821
@login_required
async def compile_business_document_sql_query():
    try:
        data = await get_request_json()
        actor_id = str(current_user.id)
        result = await thread_pool_exec(
            BusinessDocumentSqlQueryService.compile,
            actor_id,
            data,
            _is_admin(),
            _access_role(),
        )
        return _success(result)
    except (AttributeError, TypeError, BadRequest):
        return _error(
            BusinessDocumentError(
                "INVALID_SQL_QUERY_SPECIFICATION",
                "Request body must be a valid JSON object",
                422,
            )
        )
    except BusinessDocumentError as error:
        return _error(error)


@manager.route("/business-documents/sql-query/plan", methods=["POST"])  # noqa: F821
@login_required
async def plan_business_document_sql_query():
    try:
        data = await get_request_json()
        actor_id = str(current_user.id)
        result = await BusinessDocumentSqlQueryPlanningService.plan(
            actor_id,
            actor_id,
            data,
            _is_admin(),
            _access_role(),
        )
        return _success(result)
    except (AttributeError, TypeError, BadRequest):
        return _error(
            BusinessDocumentError(
                "INVALID_SQL_QUERY_PLAN_REQUEST",
                "Request body must be a valid JSON object",
                422,
            )
        )
    except BusinessDocumentError as error:
        return _error(error)


@manager.route("/business-documents/sql-query/execution-connectors", methods=["GET"])  # noqa: F821
@login_required
async def list_business_document_sql_execution_connectors():
    try:
        actor_id = str(current_user.id)
        result = await thread_pool_exec(
            BusinessDocumentSqlExecutionRegistryService.list_connectors,
            actor_id,
            _is_admin(),
        )
        return _success(result)
    except BusinessDocumentError as error:
        return _error(error)


@manager.route("/business-documents/sql-query/execution-profiles", methods=["GET"])  # noqa: F821
@login_required
async def list_business_document_sql_execution_profiles():
    try:
        actor_id = str(current_user.id)
        result = await thread_pool_exec(
            BusinessDocumentSqlExecutionRegistryService.list_profiles,
            actor_id,
            _is_admin(),
        )
        return _success(result)
    except BusinessDocumentError as error:
        return _error(error)


@manager.route("/business-documents/sql-query/execution-profiles", methods=["POST"])  # noqa: F821
@login_required
async def create_business_document_sql_execution_profile():
    try:
        data = await get_request_json()
        if not isinstance(data, dict) or not data:
            raise BusinessDocumentError("INVALID_SQL_EXECUTION_PROFILE", "Request body must be a valid JSON object", 422)
        actor_id = str(current_user.id)
        result = await thread_pool_exec(
            BusinessDocumentSqlExecutionRegistryService.create_profile,
            actor_id,
            data,
            _is_admin(),
        )
        return _success(result, 201)
    except (AttributeError, TypeError, BadRequest):
        return _error(BusinessDocumentError("INVALID_SQL_EXECUTION_PROFILE", "Request body must be a valid JSON object", 422))
    except BusinessDocumentError as error:
        return _error(error)


@manager.route("/business-documents/sql-query/execution-profiles/<profile_id>", methods=["PUT"])  # noqa: F821
@login_required
async def update_business_document_sql_execution_profile(profile_id):
    try:
        data = await get_request_json()
        if not isinstance(data, dict) or not data:
            raise BusinessDocumentError("INVALID_SQL_EXECUTION_PROFILE", "Request body must be a valid JSON object", 422)
        actor_id = str(current_user.id)
        result = await thread_pool_exec(
            BusinessDocumentSqlExecutionRegistryService.update_profile,
            actor_id,
            profile_id,
            data,
            _is_admin(),
        )
        return _success(result)
    except (AttributeError, TypeError, BadRequest):
        return _error(BusinessDocumentError("INVALID_SQL_EXECUTION_PROFILE", "Request body must be a valid JSON object", 422))
    except BusinessDocumentError as error:
        return _error(error)


@manager.route("/business-documents/sql-query/catalog-bindings", methods=["GET"])  # noqa: F821
@login_required
async def list_business_document_sql_catalog_bindings():
    try:
        actor_id = str(current_user.id)
        result = await thread_pool_exec(
            BusinessDocumentSqlExecutionRegistryService.list_bindings,
            actor_id,
            _is_admin(),
        )
        return _success(result)
    except BusinessDocumentError as error:
        return _error(error)


@manager.route("/business-documents/sql-query/catalog-bindings", methods=["POST"])  # noqa: F821
@login_required
async def create_business_document_sql_catalog_binding():
    try:
        data = await get_request_json()
        if not isinstance(data, dict) or not data:
            raise BusinessDocumentError("INVALID_SQL_CATALOG_BINDING", "Request body must be a valid JSON object", 422)
        actor_id = str(current_user.id)
        result = await thread_pool_exec(
            BusinessDocumentSqlExecutionRegistryService.create_binding,
            actor_id,
            data,
            _is_admin(),
        )
        return _success(result, 201)
    except (AttributeError, TypeError, BadRequest):
        return _error(BusinessDocumentError("INVALID_SQL_CATALOG_BINDING", "Request body must be a valid JSON object", 422))
    except BusinessDocumentError as error:
        return _error(error)


@manager.route("/business-documents/sql-query/catalog-bindings/<binding_id>", methods=["PUT"])  # noqa: F821
@login_required
async def update_business_document_sql_catalog_binding(binding_id):
    try:
        data = await get_request_json()
        if not isinstance(data, dict) or not data:
            raise BusinessDocumentError("INVALID_SQL_CATALOG_BINDING", "Request body must be a valid JSON object", 422)
        actor_id = str(current_user.id)
        result = await thread_pool_exec(
            BusinessDocumentSqlExecutionRegistryService.update_binding,
            actor_id,
            binding_id,
            data,
            _is_admin(),
        )
        return _success(result)
    except (AttributeError, TypeError, BadRequest):
        return _error(BusinessDocumentError("INVALID_SQL_CATALOG_BINDING", "Request body must be a valid JSON object", 422))
    except BusinessDocumentError as error:
        return _error(error)


@manager.route("/business-documents/sql-query/execution-binding/resolve", methods=["POST"])  # noqa: F821
@login_required
async def resolve_business_document_sql_execution_binding():
    try:
        data = await get_request_json()
        if not isinstance(data, dict) or not data:
            raise BusinessDocumentError(
                "INVALID_SQL_EXECUTION_BINDING_REQUEST",
                "Request body must be a valid JSON object",
                422,
            )
        actor_id = str(current_user.id)
        result = await thread_pool_exec(
            BusinessDocumentSqlExecutionRegistryService.resolve,
            actor_id,
            data,
            _is_admin(),
            _access_role(),
        )
        return _success(result)
    except (AttributeError, TypeError, BadRequest):
        return _error(
            BusinessDocumentError(
                "INVALID_SQL_EXECUTION_BINDING_REQUEST",
                "Request body must be a valid JSON object",
                422,
            )
        )
    except BusinessDocumentError as error:
        return _error(error)


@manager.route("/business-documents/access/users", methods=["GET"])  # noqa: F821
@login_required
async def list_business_document_access_users():
    try:
        actor_id = current_user.id
        result = await thread_pool_exec(document_queries.list_access_users, actor_id, _is_admin(), _access_role())
        return _success(result)
    except BusinessDocumentError as error:
        return _error(error)


@manager.route("/business-documents/access/users/<user_id>", methods=["PATCH"])  # noqa: F821
@login_required
async def update_business_document_access_user(user_id):
    try:
        data = await get_request_json()
        if not data:
            raise BusinessDocumentError("INVALID_ACCESS_ROLE", "Request body must be a valid JSON object", 422)
        result = await thread_pool_exec(change_user_role.execute, user_id, data, _is_admin())
        return _success(result)
    except (AttributeError, TypeError, BadRequest):
        return _error(BusinessDocumentError("INVALID_ACCESS_ROLE", "Request body must be a valid JSON object", 422))
    except BusinessDocumentError as error:
        return _error(error)


@manager.route("/business-documents/<document_id>", methods=["GET"])  # noqa: F821
@login_required
async def get_business_document(document_id):
    try:
        tenant_id = current_user.id
        return _success(await thread_pool_exec(document_queries.get_document, document_id, tenant_id, _is_admin(), _access_role()))
    except BusinessDocumentError as error:
        return _error(error)


@manager.route("/business-documents/<document_id>", methods=["DELETE"])  # noqa: F821
@login_required
async def delete_business_document(document_id):
    try:
        actor_id = current_user.id
        result = await thread_pool_exec(document_deletion.execute, actor_id, document_id, _is_admin(), _access_role())
        return _success(result)
    except BusinessDocumentError as error:
        return _error(error)


@manager.route("/business-documents/<document_id>/owner", methods=["PUT"])  # noqa: F821
@login_required
async def assign_business_document_owner(document_id):
    try:
        data = await get_request_json()
        if not data:
            raise BusinessDocumentError("INVALID_DOCUMENT_ASSIGNMENT", "Request body must be a valid JSON object", 422)
        result = await thread_pool_exec(
            assign_business_document,
            current_user.id,
            document_id,
            data,
            _is_admin(),
            _access_role(),
        )
        return _success(result)
    except (AttributeError, TypeError, BadRequest):
        return _error(BusinessDocumentError("INVALID_DOCUMENT_ASSIGNMENT", "Request body must be a valid JSON object", 422))
    except BusinessDocumentError as error:
        return _error(error)


@manager.route("/business-documents/<document_id>/eva/pull", methods=["POST"])  # noqa: F821
@login_required
async def pull_business_document_from_eva(document_id):
    try:
        data = await get_request_json()
        actor_id = current_user.id
        result = await thread_pool_exec(eva_synchronization.pull_from_eva, actor_id, actor_id, document_id, data, _is_admin(), _access_role())
        return _success(result)
    except (AttributeError, TypeError, BadRequest):
        return _error(BusinessDocumentError("INVALID_EVA_SYNC", "Request body must be a valid JSON object", 422))
    except BusinessDocumentError as error:
        return _error(error)


@manager.route("/business-documents/<document_id>/eva/status", methods=["GET"])  # noqa: F821
@login_required
async def check_business_document_eva_update(document_id):
    try:
        actor_id = current_user.id
        result = await thread_pool_exec(
            eva_synchronization.check_eva_update,
            actor_id,
            actor_id,
            document_id,
            _is_admin(),
            _access_role(),
        )
        return _success(result)
    except BusinessDocumentError as error:
        return _error(error)


@manager.route("/business-documents/<document_id>/eva/rebind", methods=["POST"])  # noqa: F821
@login_required
async def rebind_business_document_to_eva(document_id):
    try:
        data = await get_request_json()
        actor_id = current_user.id
        result = await thread_pool_exec(eva_synchronization.rebind_eva, actor_id, actor_id, document_id, data, _is_admin(), _access_role())
        return _success(result)
    except (AttributeError, TypeError, BadRequest):
        return _error(BusinessDocumentError("INVALID_EVA_BINDING", "Request body must be a valid JSON object", 422))
    except BusinessDocumentError as error:
        return _error(error)


@manager.route("/business-documents/<document_id>/eva/changes", methods=["POST"])  # noqa: F821
@login_required
async def create_business_document_eva_change(document_id):
    try:
        data = await get_request_json()
        actor_id = current_user.id
        result = await thread_pool_exec(
            eva_synchronization.create_eva_change_from_revision,
            actor_id,
            actor_id,
            document_id,
            data,
            _is_admin(),
            _access_role(),
        )
        return _success(result, 201)
    except (AttributeError, TypeError, BadRequest):
        return _error(BusinessDocumentError("INVALID_EVA_SYNC", "Request body must be a valid JSON object", 422))
    except BusinessDocumentError as error:
        return _error(error)


@manager.route("/business-documents/<document_id>/commands", methods=["POST"])  # noqa: F821
@login_required
async def execute_business_document_command(document_id):
    try:
        data = await get_request_json()
        if not data:
            raise BusinessDocumentError("INVALID_COMMAND_REQUEST", "Request body must be a valid JSON object", 422)
        actor_id = current_user.id
        result = await thread_pool_exec(
            document_commands.execute,
            actor_id,
            actor_id,
            document_id,
            data,
            _is_admin(),
            _access_role(),
        )
        if result.get("job_id"):
            wake_business_document_worker()
        return _success(result, 202 if result.get("job_id") else 200)
    except (AttributeError, TypeError, BadRequest):
        return _error(BusinessDocumentError("INVALID_COMMAND_REQUEST", "Request body must be a valid JSON object", 422))
    except BusinessDocumentError as error:
        return _error(error)


@manager.route("/business-documents/<document_id>/revisions", methods=["GET"])  # noqa: F821
@login_required
async def list_business_document_revisions(document_id):
    try:
        return _success(await thread_pool_exec(document_queries.list_revisions, document_id))
    except BusinessDocumentError as error:
        return _error(error)


@manager.route("/business-documents/<document_id>/revisions/<revision_id>", methods=["GET"])  # noqa: F821
@login_required
async def get_business_document_revision(document_id, revision_id):
    try:
        return _success(await thread_pool_exec(document_queries.get_revision, document_id, revision_id))
    except BusinessDocumentError as error:
        return _error(error)


@manager.route("/business-documents/<document_id>/jobs", methods=["GET"])  # noqa: F821
@login_required
async def list_business_document_jobs(document_id):
    try:
        return _success(await thread_pool_exec(document_queries.list_jobs, document_id))
    except BusinessDocumentError as error:
        return _error(error)


def _stream_after() -> int:
    raw = request.args.get("after") or request.headers.get("Last-Event-ID") or "0"
    if not raw.isdecimal() or len(raw) > 12:
        raise BusinessDocumentError("INVALID_EVENT_CURSOR", "Event cursor must be a non-negative integer", 422)
    return int(raw)


@manager.route("/business-documents/<document_id>/jobs/<job_id>/events", methods=["GET"])  # noqa: F821
@login_required
async def list_business_document_job_events(document_id, job_id):
    try:
        actor_id = current_user.id
        data = await thread_pool_exec(document_queries.read_job_stream_events, actor_id, actor_id, document_id, job_id, _stream_after(), _is_admin(), _access_role())
        return _success(data)
    except BusinessDocumentError as error:
        return _error(error)


@manager.route("/business-documents/<document_id>/jobs/<job_id>/events/stream", methods=["GET"])  # noqa: F821
@login_required
async def stream_business_document_job_events(document_id, job_id):
    try:
        actor_id = current_user.id
        is_admin = _is_admin()
        access_role = _access_role()
        cursor = _stream_after()
        await thread_pool_exec(document_queries.read_job_stream_events, actor_id, actor_id, document_id, job_id, cursor, is_admin, access_role)
    except BusinessDocumentError as error:
        return _error(error)

    async def events():
        nonlocal cursor
        idle_polls = 0
        while True:
            try:
                batch = await thread_pool_exec(document_queries.read_job_stream_events, actor_id, actor_id, document_id, job_id, cursor, is_admin, access_role)
            except BusinessDocumentError:
                yield "event: access_revoked\ndata: {}\n\n"
                return
            for item in batch["events"]:
                cursor = item["id"]
                yield f"id: {cursor}\nevent: {item['type']}\ndata: {json.dumps(item, ensure_ascii=False)}\n\n"
            if batch["status"] in {"COMPLETED", "DEAD"} and len(batch["events"]) < 100:
                return
            idle_polls = idle_polls + 1 if not batch["events"] else 0
            if idle_polls >= 15:
                yield ": heartbeat\n\n"
                idle_polls = 0
            await asyncio.sleep(1)

    response = Response(events(), mimetype="text/event-stream")
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    response.timeout = None
    return response


@manager.route("/business-documents/<document_id>/change-previews/<job_id>", methods=["GET"])  # noqa: F821
@login_required
async def get_business_document_change_preview(document_id, job_id):
    try:
        return _success(await thread_pool_exec(document_queries.get_change_preview, document_id, job_id))
    except BusinessDocumentError as error:
        return _error(error)


@manager.route("/business-documents/<document_id>/exports", methods=["GET"])  # noqa: F821
@login_required
async def list_business_document_exports(document_id):
    try:
        actor_id = current_user.id
        return _success(await thread_pool_exec(BusinessDocumentExportService.list_artifacts, actor_id, actor_id, document_id, _is_admin()))
    except BusinessDocumentError as error:
        return _error(error)


@manager.route("/business-documents/<document_id>/exports/<artifact_id>/download", methods=["GET"])  # noqa: F821
@login_required
async def download_business_document_export(document_id, artifact_id):
    try:
        actor_id = current_user.id
        artifact, content = await thread_pool_exec(
            BusinessDocumentExportService.download,
            actor_id,
            actor_id,
            document_id,
            artifact_id,
            is_admin=_is_admin(),
        )
        response = Response(content, content_type=artifact["mime_type"])
        response.headers["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(artifact['filename'])}"
        response.headers["Content-Length"] = str(artifact["size"])
        response.headers["ETag"] = f'"{artifact["content_hash"].removeprefix("sha256:")}"'
        return response
    except BusinessDocumentError as error:
        return _error(error)
