"""Authenticated working-TЗ HTTP scenarios."""

import asyncio
from functools import wraps

from quart import jsonify, request
from werkzeug.exceptions import BadRequest, UnsupportedMediaType

from api.apps import current_user, login_required
from api.document_exchange.pages import EvaPageError
from api.source_workbench.errors import SourceWorkspaceError
from api.source_workbench.processing import ProcessingError
from api.technical_specifications.domain import SpecificationError
from api.technical_specifications.runtime import build_specifications
from api.utils.api_utils import get_request_json
from business_documents.application.errors import BusinessDocumentError


def scenario(method):
    @wraps(method)
    @login_required
    async def invoke(*args, **kwargs):
        try:
            data = {}
            if request.method in {"POST", "PUT"}:
                try:
                    data = await get_request_json()
                except (BadRequest, UnsupportedMediaType, TypeError) as error:
                    raise SpecificationError("INVALID_INPUT", "Некорректный JSON") from error
                if not isinstance(data, dict):
                    raise SpecificationError("INVALID_INPUT", "Нужен объект JSON")
            result = await method(build_specifications(), str(current_user.id), data, *args, **kwargs)
            return jsonify({"code": 0, "data": result})
        except (SpecificationError, SourceWorkspaceError, ProcessingError, BusinessDocumentError, EvaPageError) as error:
            return jsonify({"code": error.status, "message": error.message, "data": {"error_code": error.code, "details": getattr(error, "details", None)}}), error.status
        except TimeoutError:
            return jsonify({"code": 504, "message": "Помощник не успел ответить. Проверьте состояние сообщения в чате", "data": {"error_code": "MODEL_TIMEOUT"}}), 504

    return invoke


@manager.route("/source-workspaces/<workspace>/technical-specifications", methods=["GET", "POST"])  # noqa: F821
@scenario
async def collection(service, actor, data, workspace):
    return await (service.create(actor, workspace, data) if request.method == "POST" else service.list(actor, workspace))


@manager.route("/source-workspaces/<workspace>/technical-specifications/import-eva", methods=["POST"])  # noqa: F821
@scenario
async def import_eva(service, actor, data, workspace):
    return await service.import_eva(actor, workspace, data)


@manager.route("/source-workspaces/<workspace>/technical-specifications/eva-pages", methods=["GET"])  # noqa: F821
@scenario
async def eva_pages(service, actor, data, workspace):
    return await service.eva_pages(actor, workspace, request.args.get("query", ""))


@manager.route("/source-workspaces/<workspace>/technical-specifications/<document_id>", methods=["GET", "PUT"])  # noqa: F821
@scenario
async def document(service, actor, data, workspace, document_id):
    return await (service.edit(actor, workspace, document_id, data) if request.method == "PUT" else service.get(actor, workspace, document_id))


@manager.route("/source-workspaces/<workspace>/technical-specifications/<document_id>/draft", methods=["GET", "PUT"])  # noqa: F821
@scenario
async def chat_draft(service, actor, data, workspace, document_id):
    return await (service.save_draft(actor, workspace, document_id, data) if request.method == "PUT" else service.get_draft(actor, workspace, document_id))


@manager.route("/source-workspaces/<workspace>/technical-specifications/<document_id>/revisions", methods=["GET"])  # noqa: F821
@manager.route("/source-workspaces/<workspace>/technical-specifications/<document_id>/revisions/<int:number>", methods=["GET"])  # noqa: F821
@scenario
async def history(service, actor, data, workspace, document_id, number=None):
    return await service.history(actor, workspace, document_id, number)


@manager.route("/source-workspaces/<workspace>/technical-specifications/<document_id>/<action>", methods=["POST"])  # noqa: F821
@scenario
async def action(service, actor, data, workspace, document_id, action):
    operations = {"chat": service.chat, "comments": service.comment, "apply": service.apply, "restore": service.restore, "pull": service.pull, "resolve-eva": service.resolve_eva}
    if action not in operations:
        raise SpecificationError("NOT_FOUND", "Неизвестное действие", 404)
    # Cancellation before a completed model request cannot save a partial result.
    async with asyncio.timeout(330):
        return await operations[action](actor, workspace, document_id, data)
