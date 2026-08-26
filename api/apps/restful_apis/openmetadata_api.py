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

"""Authenticated REST facade for the OpenMetadata catalog agents."""

from __future__ import annotations

import logging
import threading

from quart import request

from api.apps import current_user, login_required
from api.apps.services.openmetadata_copilot_service import (
    OpenMetadataAuthenticationError,
    OpenMetadataConfigurationError,
    OpenMetadataConflictError,
    OpenMetadataCopilotService,
    OpenMetadataError,
    OpenMetadataNotFoundError,
    OpenMetadataPermissionError,
)
from api.utils.api_utils import get_json_result, get_request_json
from common.constants import RetCode
from common.misc_utils import thread_pool_exec


LOGGER = logging.getLogger(__name__)
_SERVICE: OpenMetadataCopilotService | None = None
_SERVICE_LOCK = threading.Lock()


def _service() -> OpenMetadataCopilotService:
    global _SERVICE
    if _SERVICE is not None:
        return _SERVICE
    with _SERVICE_LOCK:
        if _SERVICE is None:
            _SERVICE = OpenMetadataCopilotService()
        return _SERVICE


def _error_response(exc: Exception):
    if isinstance(exc, (ValueError, OpenMetadataConfigurationError)):
        code = RetCode.ARGUMENT_ERROR
    elif isinstance(exc, OpenMetadataAuthenticationError):
        code = RetCode.AUTHENTICATION_ERROR
    elif isinstance(exc, OpenMetadataPermissionError):
        code = RetCode.PERMISSION_ERROR
    elif isinstance(exc, OpenMetadataNotFoundError):
        code = RetCode.NOT_FOUND
    elif isinstance(exc, OpenMetadataConflictError):
        code = RetCode.CONFLICT
    else:
        code = RetCode.CONNECTION_ERROR
    if not isinstance(exc, (ValueError, OpenMetadataError)):
        LOGGER.exception("Unexpected OpenMetadata Copilot error")
        message = "Внутренняя ошибка OpenMetadata Copilot"
    else:
        message = str(exc)
    return get_json_result(code=code, message=message)


async def _require_catalog_access() -> None:
    """Authorize the shared OpenMetadata catalog through its RAGFlow Dataset ACL."""
    dataset_id = str(getattr(_service().config, "dataset_id", "") or "").strip()
    if dataset_id:
        from api.db.services.knowledgebase_service import KnowledgebaseService

        allowed = await thread_pool_exec(
            KnowledgebaseService.accessible,
            dataset_id,
            str(current_user.id),
        )
        if allowed:
            return
    elif bool(getattr(current_user, "is_superuser", False)):
        return
    raise OpenMetadataPermissionError("OpenMetadata Catalog недоступен: запросите доступ к связанному Dataset RAGFlow")


async def _dataset_hits(question: str, user_id: str) -> tuple[list[dict] | None, str | None]:
    from api.apps.services.openmetadata_dataset_retrieval import retrieve_openmetadata_dataset_hits

    return await retrieve_openmetadata_dataset_hits(_service(), question, user_id)


@manager.route("/openmetadata/status", methods=["GET"])  # noqa: F821
@login_required
async def status():
    try:
        await _require_catalog_access()
        force = request.args.get("refresh", "").strip().lower() in {"1", "true", "yes"}
        data = await thread_pool_exec(
            _service().status,
            force=force,
            user_id=str(current_user.id),
        )
        data["governance_allowed"] = bool(data.get("write_enabled") and getattr(current_user, "is_superuser", False))
        return get_json_result(data=data)
    except Exception as exc:
        return _error_response(exc)


@manager.route("/openmetadata/starter-questions", methods=["GET"])  # noqa: F821
@login_required
async def starter_questions():
    try:
        await _require_catalog_access()
        user_id = str(current_user.id)
        locale = request.args.get("locale", "ru")
        data = await thread_pool_exec(_service().starter_questions.generate, user_id=user_id, locale=locale)
        return get_json_result(data=data)
    except Exception as exc:
        return _error_response(exc)


@manager.route("/openmetadata/agents/provision", methods=["POST"])  # noqa: F821
@login_required
async def provision_agents():
    try:
        await _require_catalog_access()
        _require_governance_role()
        from api.apps.services.openmetadata_agent_service import provision_openmetadata_agents

        data = await thread_pool_exec(provision_openmetadata_agents, str(current_user.id))
        return get_json_result(data=data)
    except Exception as exc:
        return _error_response(exc)


@manager.route("/openmetadata/query", methods=["POST"])  # noqa: F821
@login_required
async def query_catalog():
    try:
        await _require_catalog_access()
        payload = await get_request_json()
        if not isinstance(payload, dict):
            raise ValueError("Тело запроса должно быть JSON-объектом")
        question = payload.get("question")
        filters = payload.get("filters")
        if filters is not None and not isinstance(filters, dict):
            raise ValueError("filters должен быть объектом")
        context = payload.get("context")
        if context is not None and not isinstance(context, list):
            raise ValueError("context должен быть массивом")
        action = payload.get("action")
        if action is not None and not isinstance(action, dict):
            raise ValueError("action должен быть объектом")
        try:
            depth = int(payload.get("depth", 2))
        except (TypeError, ValueError) as exc:
            raise ValueError("depth должен быть целым числом от 1 до 3") from exc
        user_id = str(current_user.id)
        dataset_hits, dataset_warning = (None, None)
        intent = _service().catalog.classify(str(question or "")) if not action else ""
        if not action and intent in {"discovery", "governance"}:
            dataset_hits, dataset_warning = await _dataset_hits(str(question or ""), user_id)
        run_kwargs = {
            "user_id": user_id,
            "filters": filters,
            "depth": depth,
            "context": context,
            "selected_entity_id": str(payload.get("selected_entity_id") or ""),
            "action": action,
            "locale": str(payload.get("locale") or "ru"),
        }
        if dataset_hits is not None:
            run_kwargs["dataset_hits"] = dataset_hits
        if dataset_warning:
            run_kwargs["dataset_warning"] = dataset_warning
        data = await thread_pool_exec(
            _service().catalog.run,
            question,
            **run_kwargs,
        )
        return get_json_result(data=data)
    except Exception as exc:
        return _error_response(exc)


@manager.route("/openmetadata/entities", methods=["GET"])  # noqa: F821
@login_required
async def search_entities():
    try:
        await _require_catalog_access()
        query = request.args.get("q", "")
        filters = {key: request.args.get(key) for key in ("owner", "domain", "service", "tag") if request.args.get(key)}
        if "has_description" in request.args:
            filters["has_description"] = request.args.get("has_description", "").lower() in {"1", "true", "yes"}
        try:
            limit = int(request.args.get("limit", _service().config.max_results))
        except (TypeError, ValueError) as exc:
            raise ValueError("limit должен быть целым числом") from exc
        try:
            offset = int(request.args.get("offset", 0))
        except (TypeError, ValueError) as exc:
            raise ValueError("offset должен быть целым числом") from exc
        user_id = str(current_user.id)
        search_kwargs = {
            "filters": filters,
            "limit": limit,
            "offset": offset,
            "sort": request.args.get("sort", "relevance"),
            "user_id": user_id,
            "locale": request.args.get("locale", "ru"),
        }
        data = await thread_pool_exec(
            _service().discovery.search,
            query,
            **search_kwargs,
        )
        return get_json_result(data=data)
    except Exception as exc:
        return _error_response(exc)


@manager.route("/openmetadata/entities/<entity_id>/relationships", methods=["GET"])  # noqa: F821
@login_required
async def entity_relationships(entity_id: str):
    try:
        await _require_catalog_access()
        try:
            depth = max(1, min(3, int(request.args.get("depth", 2))))
        except (TypeError, ValueError) as exc:
            raise ValueError("depth должен быть целым числом от 1 до 3") from exc
        user_id = str(current_user.id)
        entity = await thread_pool_exec(
            _service().get_visible_entity,
            entity_id,
            user_id=user_id,
        )
        if not entity:
            raise OpenMetadataNotFoundError("Таблица недоступна в текущей области каталога")
        data = await thread_pool_exec(
            _service().impact_quality.impact,
            entity.get("fullyQualifiedName") or entity.get("name") or entity_id,
            depth=depth,
            user_id=user_id,
            entity=entity,
        )
        return get_json_result(data=data)
    except Exception as exc:
        return _error_response(exc)


def _require_governance_role() -> None:
    if not bool(getattr(current_user, "is_superuser", False)):
        raise OpenMetadataPermissionError("Governance доступен только администратору RAGFlow")


@manager.route("/openmetadata/governance/preview", methods=["POST"])  # noqa: F821
@login_required
async def governance_preview():
    try:
        await _require_catalog_access()
        _require_governance_role()
        payload = await get_request_json()
        if not isinstance(payload, dict):
            raise ValueError("Тело запроса должно быть JSON-объектом")
        data = await thread_pool_exec(
            _service().governance.preview,
            user_id=str(current_user.id),
            entity_id=str(payload.get("entity_id") or ""),
            changes=payload.get("changes"),
        )
        return get_json_result(data=data)
    except Exception as exc:
        return _error_response(exc)


@manager.route("/openmetadata/governance/confirm", methods=["POST"])  # noqa: F821
@login_required
async def governance_confirm():
    try:
        await _require_catalog_access()
        _require_governance_role()
        payload = await get_request_json()
        if not isinstance(payload, dict):
            raise ValueError("Тело запроса должно быть JSON-объектом")
        token = str(payload.get("confirmation_token") or "")
        if not token:
            raise ValueError("confirmation_token обязателен")
        data = await thread_pool_exec(
            _service().governance.confirm,
            user_id=str(current_user.id),
            confirmation_token=token,
        )
        return get_json_result(data=data)
    except Exception as exc:
        return _error_response(exc)
