"""HTTP entry points for reusable source workspaces."""

from quart import jsonify
from werkzeug.exceptions import BadRequest

from api.apps import current_user, login_required
from api.source_workbench.adapters import build_source_workspace_service
from api.source_workbench.service import SourceWorkspaceError
from api.utils.api_utils import get_request_json


def _success(data, status=200):
    return jsonify({"code": 0, "data": data}), status


def _error(error: SourceWorkspaceError):
    return jsonify({"code": error.status, "message": error.message, "data": {"error_code": error.code}}), error.status


async def _body():
    try:
        data = await get_request_json()
    except (AttributeError, TypeError, BadRequest) as error:
        raise SourceWorkspaceError("INVALID_INPUT", "Request body must be a valid JSON object") from error
    if not isinstance(data, dict):
        raise SourceWorkspaceError("INVALID_INPUT", "Request body must be a JSON object")
    return data


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
    except SourceWorkspaceError as error:
        return _error(error)
