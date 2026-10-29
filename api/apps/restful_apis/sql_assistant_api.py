"""Authenticated HTTP boundary of the SQL research surface."""

from quart import jsonify, request
from werkzeug.exceptions import BadRequest

from api.apps import current_user, login_required
from api.apps.business_documents.worker import wake_business_document_worker
from api.apps.services.openmetadata_runtime_service import get_openmetadata_service, openmetadata_catalog_accessible
from api.apps.sql_assistant.service import SqlAssistantService
from api.db.db_models import DB
from business_documents.application.errors import BusinessDocumentError, PermissionDeniedError, ValidationError
from common.misc_utils import thread_pool_exec


@manager.after_request  # noqa: F821
async def no_cache(response):
    response.headers["Cache-Control"] = "no-store"
    return response


def _service():
    actor = str(current_user.id)
    return SqlAssistantService(actor, actor, bool(getattr(current_user, "is_superuser", False)), str(getattr(current_user, "business_document_role", "AUTHOR_CREATOR") or "AUTHOR_CREATOR"))


async def _respond(function, status=200):
    def invoke():
        try:
            return function()
        finally:
            DB.close()

    try:
        data = await thread_pool_exec(invoke)
        return jsonify({"code": 0, "data": data}), status
    except BusinessDocumentError as error:
        return _error(error)


def _error(error):
    if error.code in {"SQL_EXECUTION_UNKNOWN", "SQL_RESULT_UNAVAILABLE"}:
        return jsonify({"code": error.status, "message": error.message, "data": {"error_code": error.code, "recovery": "RECONCILE", "retryable": False}}), error.status
    recovery = (
        "RESELECT"
        if error.code in {"SQL_REFERENCE_UNAVAILABLE", "SQL_REFERENCE_STALE", "SQL_DEFINITION_STALE", "SQL_METRIC_SELECTION_REQUIRED"}
        else "REFRESH"
        if error.status == 409
        else "DENIED"
        if error.status in {401, 403, 404}
        else "RETRY_LATER"
        if error.status >= 500
        else "EDIT"
    )
    data = {"error_code": error.code, "recovery": recovery, "retryable": recovery == "RETRY_LATER"}
    if error.code == "SQL_METRIC_SELECTION_REQUIRED":
        data["metrics"] = (error.details or {}).get("metrics", [])
    return jsonify({"code": error.status, "message": error.message, "data": data}), error.status


async def _body():
    if request.content_length and request.content_length > 256 * 1024:
        raise ValidationError("INVALID_SQL_COMMAND", "Размер команды превышает допустимый")
    try:
        return await request.get_json(silent=True)
    except BadRequest as error:
        raise ValidationError("INVALID_SQL_COMMAND", "Требуется корректный JSON") from error


@manager.route("/sql-assistant/sessions", methods=["GET"])  # noqa: F821
@login_required
async def sessions():
    service = _service()
    return await _respond(service.list)


@manager.route("/sql-assistant/sessions", methods=["POST"])  # noqa: F821
@login_required
async def create():
    service = _service()
    try:
        data = await _body()
    except BusinessDocumentError as error:
        return _error(error)
    response = await _respond(lambda: service.create(data), 201)
    if response[1] == 201:
        wake_business_document_worker()
    return response


@manager.route("/sql-assistant/llm-log-count", methods=["POST"])  # noqa: F821
@login_required
async def create_llm_log_count():
    service = _service()
    try:
        data = await _body()
    except BusinessDocumentError as error:
        return _error(error)
    return await _respond(lambda: service.create_llm_count(data), 201)


@manager.route("/sql-assistant/sessions/<session_id>/runs/<run_id>/execution", methods=["GET"])  # noqa: F821
@login_required
async def execution(session_id, run_id):
    from api.apps.business_documents.sql_query_runs import BusinessDocumentSqlRunService

    service = _service()
    return await _respond(lambda: service.runtime.call(BusinessDocumentSqlRunService.execution_status, session_id, run_id))


@manager.route("/sql-assistant/sessions/<session_id>", methods=["GET"])  # noqa: F821
@login_required
async def session(session_id):
    service = _service()
    return await _respond(lambda: service.get(session_id))


@manager.route("/sql-assistant/sessions/<session_id>/definitions", methods=["GET"])  # noqa: F821
@login_required
async def definitions(session_id):
    service = _service()
    return await _respond(lambda: service.definitions(session_id))


@manager.route("/sql-assistant/sessions/<session_id>/commands", methods=["POST"])  # noqa: F821
@login_required
async def command(session_id):
    service = _service()
    try:
        data = await _body()
    except BusinessDocumentError as error:
        return _error(error)
    response = await _respond(lambda: service.command(session_id, data))
    if response[1] == 200:
        wake_business_document_worker()
    return response


@manager.route("/sql-assistant/sessions/<session_id>/dialogue-options", methods=["GET"])  # noqa: F821
@login_required
async def dialogue_options(session_id):
    service = _service()
    question = request.args.get("q", "")
    return await _respond(lambda: service.dialogue_options(session_id, question))


@manager.route("/sql-assistant/sessions/<session_id>/results/<run_id>", methods=["GET"])  # noqa: F821
@login_required
async def result(session_id, run_id):
    service = _service()
    try:
        offset = int(request.args.get("offset", "0"))
    except ValueError:
        return jsonify({"code": 422, "message": "Неверная страница результата"}), 422
    return await _respond(lambda: service.result(session_id, run_id, offset))


@manager.route("/sql-assistant/sessions/<session_id>/preflight", methods=["GET"])  # noqa: F821
@login_required
async def preflight(session_id):
    service = _service()
    return await _respond(lambda: service.preflight(session_id))


@manager.route("/sql-assistant/catalog", methods=["GET"])  # noqa: F821
@login_required
async def catalog():
    service = _service()
    query = request.args.get("q", "")

    def search():
        from api.apps.services.openmetadata_copilot_service import OpenMetadataError

        catalog = get_openmetadata_service()
        if not openmetadata_catalog_accessible(catalog, service.runtime.actor_id, service.runtime.is_admin):
            raise PermissionDeniedError("Каталог данных недоступен вашей учётной записи")
        try:
            return catalog.discovery.search(query, limit=20, user_id=service.runtime.actor_id, locale="ru")
        except ValueError as error:
            raise ValidationError("INVALID_CATALOG_SEARCH", str(error)) from error
        except OpenMetadataError as error:
            raise BusinessDocumentError("SQL_CATALOG_UNAVAILABLE", "Каталог временно недоступен. Вопрос сохранён; повторите поиск позже.", 503) from error

    return await _respond(search)
