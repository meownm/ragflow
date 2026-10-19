"""HTTP entry points for reusable source workspaces."""

import asyncio
import json
import logging

from quart import Response, jsonify
from werkzeug.exceptions import BadRequest

from api.apps import current_user, login_required
from api.source_workbench.adapters import build_source_workspace_service
from api.source_workbench.service import SourceWorkspaceError
from api.source_workbench.processing import ProcessingError
from api.utils.api_utils import get_request_json


def _success(data, status=200):
    return jsonify({"code": 0, "data": data}), status


def _error(error: SourceWorkspaceError | ProcessingError):
    return jsonify({"code": error.status, "message": error.message, "data": {"error_code": error.code}}), error.status


async def _body():
    try:
        data = await get_request_json()
    except (AttributeError, TypeError, BadRequest) as error:
        raise SourceWorkspaceError("INVALID_INPUT", "Request body must be a valid JSON object") from error
    if not isinstance(data, dict):
        raise SourceWorkspaceError("INVALID_INPUT", "Request body must be a JSON object")
    return data


def _linked():
    from api.source_workbench.linked_runtime import build_linked_documents

    return build_linked_documents()


@manager.route("/source-workspaces/<workspace_id>/documents", methods=["GET", "POST"])  # noqa: F821
@login_required
async def linked_documents(workspace_id):
    from quart import request

    try:
        service = _linked()
        actor = str(current_user.id)
        if request.method == "POST":
            return _success(await service.create(actor, workspace_id, await _body()), 201)
        return _success(await service.list(actor, workspace_id))
    except (SourceWorkspaceError, ProcessingError) as error:
        return _error(error)


@manager.route("/source-workspaces/<workspace_id>/documents/import", methods=["POST"])  # noqa: F821
@login_required
async def import_linked_document(workspace_id):
    try:
        return _success(await _linked().import_source(str(current_user.id), workspace_id, await _body()), 201)
    except (SourceWorkspaceError, ProcessingError) as error:
        return _error(error)


@manager.route("/source-workspaces/<workspace_id>/documents/<document_id>", methods=["GET", "PUT"])  # noqa: F821
@login_required
async def linked_document(workspace_id, document_id):
    from quart import request

    try:
        service = _linked()
        actor = str(current_user.id)
        if request.method == "PUT":
            return _success(await service.edit(actor, workspace_id, document_id, await _body()))
        return _success(await service.get(actor, workspace_id, document_id))
    except (SourceWorkspaceError, ProcessingError) as error:
        return _error(error)


@manager.route("/source-workspaces/<workspace_id>/documents/<document_id>/revisions/<revision_id>", methods=["GET"])  # noqa: F821
@login_required
async def linked_revision(workspace_id, document_id, revision_id):
    try:
        return _success(await _linked().history(str(current_user.id), workspace_id, document_id, revision_id))
    except SourceWorkspaceError as error:
        return _error(error)


@manager.route("/source-workspaces/<workspace_id>/documents/<document_id>/dependencies", methods=["POST"])  # noqa: F821
@manager.route("/source-workspaces/<workspace_id>/documents/<document_id>/dependencies/<dependency_id>", methods=["PUT"])  # noqa: F821
@login_required
async def linked_dependency(workspace_id, document_id, dependency_id=None):
    try:
        return _success(await _linked().dependency(str(current_user.id), workspace_id, document_id, await _body(), dependency_id))
    except SourceWorkspaceError as error:
        return _error(error)


@manager.route("/source-workspaces/<workspace_id>/documents/<document_id>/dependencies/<dependency_id>", methods=["DELETE"])  # noqa: F821
@login_required
async def remove_linked_dependency(workspace_id, document_id, dependency_id):
    try:
        return _success(await _linked().remove_dependency(str(current_user.id), workspace_id, document_id, dependency_id, await _body()))
    except SourceWorkspaceError as error:
        return _error(error)


@manager.route("/source-workspaces/<workspace_id>/documents/<document_id>/refresh", methods=["POST"])  # noqa: F821
@login_required
async def refresh_linked_document(workspace_id, document_id):
    try:
        return _success(await _linked().refresh(str(current_user.id), workspace_id, document_id))
    except SourceWorkspaceError as error:
        return _error(error)


@manager.route("/source-workspaces/<workspace_id>/documents/<document_id>/prepare", methods=["POST"])  # noqa: F821
@login_required
async def prepare_linked_document(workspace_id, document_id):
    try:
        return _success(await _linked().prepare(str(current_user.id), workspace_id, document_id, await _body()), 202)
    except (SourceWorkspaceError, ProcessingError) as error:
        return _error(error)


@manager.route("/source-workspaces/<workspace_id>/documents/<document_id>/approve", methods=["POST"])  # noqa: F821
@login_required
async def approve_linked_document(workspace_id, document_id):
    try:
        return _success(await _linked().approve(str(current_user.id), workspace_id, document_id, await _body()))
    except SourceWorkspaceError as error:
        return _error(error)


@manager.route("/source-workspaces/<workspace_id>/documents/<document_id>/baseline", methods=["POST"])  # noqa: F821
@login_required
async def baseline_linked_document(workspace_id, document_id):
    try:
        return _success(await _linked().baseline(str(current_user.id), workspace_id, document_id, await _body()))
    except SourceWorkspaceError as error:
        return _error(error)


@manager.route("/source-workspaces/<workspace_id>/documents/<document_id>/changes/<change_id>/decision", methods=["POST"])  # noqa: F821
@login_required
async def decide_linked_change(workspace_id, document_id, change_id):
    try:
        return _success(await _linked().decide(str(current_user.id), workspace_id, document_id, change_id, await _body()))
    except SourceWorkspaceError as error:
        return _error(error)


@manager.route("/source-workspaces/<workspace_id>/documents/<document_id>/changes/<change_id>/selection", methods=["POST"])  # noqa: F821
@login_required
async def select_linked_changes(workspace_id, document_id, change_id):
    try:
        return _success(await _linked().prepare_selection(str(current_user.id), workspace_id, document_id, change_id, await _body()), 202)
    except SourceWorkspaceError as error:
        return _error(error)


@manager.route("/source-workspaces/<workspace_id>/documents/<document_id>/jobs/<job_id>/cancel", methods=["POST"])  # noqa: F821
@login_required
async def cancel_linked_job(workspace_id, document_id, job_id):
    try:
        return _success(await _linked().cancel(str(current_user.id), workspace_id, document_id, job_id))
    except SourceWorkspaceError as error:
        return _error(error)


@manager.route("/source-workspaces/<workspace_id>/members", methods=["GET", "PUT"])  # noqa: F821
@login_required
async def linked_workspace_members(workspace_id):
    from quart import request

    try:
        service = _linked()
        actor = str(current_user.id)
        if request.method == "PUT":
            return _success(await service.set_member(actor, workspace_id, await _body()))
        return _success(await service.members(actor, workspace_id))
    except SourceWorkspaceError as error:
        return _error(error)


@manager.route("/source-workspaces/<workspace_id>/datasets", methods=["PUT"])  # noqa: F821
@login_required
async def configure_linked_workspace_search(workspace_id):
    try:
        return _success(await _linked().configure_search(str(current_user.id), workspace_id, await _body()))
    except SourceWorkspaceError as error:
        return _error(error)


@manager.route("/source-workspaces/<workspace_id>/organization", methods=["GET", "PUT"])  # noqa: F821
@login_required
async def linked_workspace_organization(workspace_id):
    from quart import request

    try:
        return _success(await _linked().organization(str(current_user.id), workspace_id, await _body() if request.method == "PUT" else None))
    except SourceWorkspaceError as error:
        return _error(error)


@manager.route("/source-workspaces/<workspace_id>/document-overview", methods=["GET"])  # noqa: F821
@login_required
async def linked_document_overview(workspace_id):
    try:
        return _success(await _linked().overview(str(current_user.id), workspace_id))
    except SourceWorkspaceError as error:
        return _error(error)


@manager.route("/source-workspaces/<workspace_id>/business-document-sources", methods=["GET"])  # noqa: F821
@login_required
async def linked_business_sources(workspace_id):
    from quart import request

    try:
        return _success(await _linked().business_sources(str(current_user.id), workspace_id, request.args.get("query", ""), int(request.args.get("page", 1))))
    except ValueError:
        return _error(SourceWorkspaceError("INVALID_INPUT", "Некорректная страница"))
    except SourceWorkspaceError as error:
        return _error(error)


@manager.route("/source-workspaces/<workspace_id>/documents/<document_id>/implementation", methods=["POST"])  # noqa: F821
@login_required
async def confirm_linked_implementation(workspace_id, document_id):
    try:
        return _success(await _linked().implementation(str(current_user.id), workspace_id, document_id, await _body()))
    except SourceWorkspaceError as error:
        return _error(error)


@manager.route("/source-workspaces/<workspace_id>/documents/<document_id>/implementation/revoke", methods=["POST"])  # noqa: F821
@login_required
async def revoke_linked_implementation(workspace_id, document_id):
    try:
        return _success(await _linked().implementation(str(current_user.id), workspace_id, document_id, await _body(), revoke=True))
    except SourceWorkspaceError as error:
        return _error(error)


@manager.route("/source-workspaces", methods=["POST"])  # noqa: F821
@login_required
async def create_source_workspace():
    try:
        return _success(await build_source_workspace_service().create(str(current_user.id), await _body()), 201)
    except SourceWorkspaceError as error:
        return _error(error)


@manager.route("/source-workspaces", methods=["GET"])  # noqa: F821
@login_required
async def list_source_workspaces():
    return _success(await build_source_workspace_service().list(str(current_user.id)))


@manager.route("/source-workspaces/datasets", methods=["GET"])  # noqa: F821
@login_required
async def list_source_workspace_datasets():
    return _success(await build_source_workspace_service().list_datasets(str(current_user.id)))


@manager.route("/source-workspaces/<workspace_id>", methods=["GET"])  # noqa: F821
@login_required
async def get_source_workspace(workspace_id):
    try:
        return _success(await build_source_workspace_service().get(str(current_user.id), workspace_id))
    except SourceWorkspaceError as error:
        return _error(error)


@manager.route("/source-workspaces/<workspace_id>/search", methods=["POST"])  # noqa: F821
@login_required
async def search_source_workspace(workspace_id):
    try:
        data = await _body()
        return _success(await build_source_workspace_service().search(str(current_user.id), workspace_id, data.get("query"), data.get("page", 1)))
    except SourceWorkspaceError as error:
        return _error(error)


@manager.route("/source-workspaces/<workspace_id>/selection", methods=["PUT"])  # noqa: F821
@login_required
async def select_source_workspace_documents(workspace_id):
    try:
        return _success(await build_source_workspace_service().select(str(current_user.id), workspace_id, await _body()))
    except SourceWorkspaceError as error:
        return _error(error)


@manager.route("/source-workspaces/<workspace_id>/preview", methods=["POST"])  # noqa: F821
@login_required
async def preview_source_workspace_document(workspace_id):
    try:
        return _success(await build_source_workspace_service().preview_document(str(current_user.id), workspace_id, await _body()))
    except SourceWorkspaceError as error:
        return _error(error)


@manager.route("/source-workspaces/<workspace_id>/drafts", methods=["GET"])  # noqa: F821
@login_required
async def list_source_workspace_drafts(workspace_id):
    try:
        return _success(await build_source_workspace_service().list_drafts(str(current_user.id), workspace_id))
    except SourceWorkspaceError as error:
        return _error(error)


@manager.route("/source-workspaces/<workspace_id>/drafts", methods=["POST"])  # noqa: F821
@login_required
async def save_source_workspace_draft(workspace_id):
    try:
        return _success(await build_source_workspace_service().save_draft(str(current_user.id), workspace_id, await _body()), 201)
    except SourceWorkspaceError as error:
        return _error(error)


@manager.route("/source-workspaces/<workspace_id>/drafts/<draft_id>", methods=["GET"])  # noqa: F821
@login_required
async def get_source_workspace_draft(workspace_id, draft_id):
    try:
        return _success(await build_source_workspace_service().get_draft(str(current_user.id), workspace_id, draft_id))
    except SourceWorkspaceError as error:
        return _error(error)


@manager.route("/source-workspaces/<workspace_id>/drafts/<draft_id>", methods=["PUT"])  # noqa: F821
@login_required
async def update_source_workspace_draft(workspace_id, draft_id):
    try:
        return _success(await build_source_workspace_service().update_draft(str(current_user.id), workspace_id, draft_id, await _body()))
    except SourceWorkspaceError as error:
        return _error(error)


@manager.route("/source-workspaces/<workspace_id>/retrieve", methods=["POST"])  # noqa: F821
@login_required
async def retrieve_source_workspace_documents(workspace_id):
    try:
        data = await _body()
        return _success(await build_source_workspace_service().retrieve(str(current_user.id), workspace_id, data.get("query"), data.get("expected_version")))
    except SourceWorkspaceError as error:
        return _error(error)


@manager.route("/source-workspaces/<workspace_id>/chat", methods=["POST"])  # noqa: F821
@login_required
async def chat_with_source_workspace(workspace_id):
    try:
        return _success(await build_source_workspace_service().chat(str(current_user.id), workspace_id, await _body()))
    except (SourceWorkspaceError, ProcessingError) as error:
        return _error(error)
    except TimeoutError:
        return _error(SourceWorkspaceError("MODEL_TIMEOUT", "Модель не завершила ответ за 30 минут", 504))


@manager.route("/source-workspaces/<workspace_id>/process", methods=["POST"])  # noqa: F821
@login_required
async def process_source_workspace(workspace_id):
    try:
        events = await build_source_workspace_service().process_stream(str(current_user.id), workspace_id, await _body())
    except SourceWorkspaceError as error:
        return _error(error)
    except ProcessingError as error:
        return _error(SourceWorkspaceError(error.code, error.message, error.status))

    async def stream():
        iterator = events.__aiter__()
        pending = None
        deadline = asyncio.get_running_loop().time() + 1800
        try:
            while True:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    event = {"event": "error", "code": "WORKFLOW_TIMEOUT", "message": "Обработка превысила 30 минут. Последний завершённый шаг сохранён в браузере."}
                    yield f"event: error\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                    break
                if pending is None:
                    pending = asyncio.create_task(anext(iterator))
                done, _ = await asyncio.wait({pending}, timeout=min(15, remaining))
                if not done:
                    yield ": heartbeat\n\n"
                    continue
                try:
                    event = pending.result()
                except StopAsyncIteration:
                    break
                except (SourceWorkspaceError, ProcessingError) as error:
                    event = {"event": "error", "code": error.code, "message": error.message}
                    yield f"event: error\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                    break
                except TimeoutError:
                    event = {"event": "error", "code": "MODEL_TIMEOUT", "message": "Модель не завершила этап за 30 минут"}
                    yield f"event: error\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                    break
                except Exception:
                    logging.exception("Source workspace processing failed")
                    event = {"event": "error", "code": "PROCESS_FAILED", "message": "Обработка не завершена. Повторите запрос."}
                    yield f"event: error\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                    break
                pending = None
                yield f"event: {event['event']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
        finally:
            if pending is not None and not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
            await iterator.aclose()

    response = Response(stream(), mimetype="text/event-stream")
    response.headers["Cache-Control"] = "no-cache"
    response.headers["Connection"] = "keep-alive"
    response.headers["X-Accel-Buffering"] = "no"
    response.timeout = None
    return response
