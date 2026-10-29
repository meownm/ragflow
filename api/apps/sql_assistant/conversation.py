"""Durable conversation-job integration with the existing SQL assistant."""

from api.apps.sql_assistant.answer import plan_conversation
from api.apps.sql_assistant.service import SqlAssistantService
from copy import deepcopy
from datetime import datetime

from api.db.db_models import BusinessDocumentJob, SqlAssistantSession, User
from business_documents.application.errors import BusinessDocumentError, ConflictError
from business_documents.sql_assistant import fingerprint
from business_documents.sql_conversation import automatic_schema_payload, automatic_sources
from common.time_utils import current_timestamp


def plan_job(job, actor_id, is_admin, access_role):
    """Load authorized rows only in memory, after the worker rechecks the actor."""
    service = SqlAssistantService(job.tenant_id, actor_id, is_admin, access_role)
    request = job.payload["request"]
    context = service.conversation_context(job.document_id, request["payload"])
    session = SqlAssistantSession.get_or_none((SqlAssistantSession.id == job.document_id) & (SqlAssistantSession.tenant_id == job.tenant_id))
    turn = (session.state if session else {}).get("conversation") or {}
    if turn.get("job_id") != job.id or turn.get("message_id") != request["message_id"] or fingerprint(context) != request["context_hash"]:
        raise ConflictError("SQL_DIALOGUE_CONTEXT_CHANGED", "Контекст диалога изменился до подготовки ответа")
    message = next((item for item in session.state["messages"] if item["id"] == request["message_id"]), None)
    if message is None or message["text"] != request["question"]:
        raise ConflictError("SQL_DIALOGUE_CONTEXT_CHANGED", "Исходная реплика изменилась")
    return plan_conversation(job.tenant_id, request["question"], context, session.state["messages"])


async def enrich_schema(job, resolution, resolver, actor_id, is_admin, locale):
    """Load full schemas in the worker, before the completion transaction."""
    from business_documents.sql_query.schema_resolution import LoadSchemaEntitiesCommand, SchemaEntityDetailsScenario

    session = SqlAssistantSession.get_or_none((SqlAssistantSession.id == job.document_id) & (SqlAssistantSession.tenant_id == job.tenant_id))
    if session is None or (session.state.get("conversation") or {}).get("job_id") != job.id:
        return resolution
    identities = automatic_sources(resolution)
    if not identities:
        return resolution
    details = await SchemaEntityDetailsScenario(resolver).run(LoadSchemaEntitiesCommand(actor_id=actor_id, entity_ids=tuple(identities), locale=locale, is_admin=is_admin))
    schema = automatic_schema_payload(resolution, details, job.payload["request"]["requirements"])
    return {**resolution, "conversation_schema": schema}


def advance(job):
    """Called within the transaction completing a SQL job, never from GET/UI."""
    stored = BusinessDocumentJob.get_by_id(job.id)
    session = SqlAssistantSession.get_or_none((SqlAssistantSession.id == job.document_id) & (SqlAssistantSession.tenant_id == job.tenant_id))
    if session is None or (session.state.get("conversation") or {}).get("job_id") != job.id:
        return
    if stored.status != "COMPLETED":
        return
    try:
        # A savepoint keeps the completed result durable even when a subsequent
        # permission/context gate rejects progression. No model call is made.
        with SqlAssistantSession._meta.database.atomic():
            user = User.get_or_none(User.id == (job.payload or {}).get("actor_id"))
            if user is None or str(user.status) != "1" or str(user.is_active) != "1":
                raise ConflictError("SQL_DIALOGUE_ACCESS_CHANGED", "Учётная запись недоступна")
            service = SqlAssistantService(job.tenant_id, user.id, bool(user.is_superuser), str(user.business_document_role or "AUTHOR_EDITOR"))
            if stored.job_type == "SQL_AGENT_DIALOGUE":
                service.complete_dialogue_job(stored)
            else:
                service.advance_preparation(stored)
    except (BusinessDocumentError, ValueError, KeyError, TypeError) as error:
        current = SqlAssistantSession.get_by_id(session.id)
        state = deepcopy(current.state)
        if (state.get("conversation") or {}).get("job_id") != job.id:
            return
        state["conversation"].update(
            phase="ERROR",
            job_id=None,
            error={
                "code": error.code if isinstance(error, BusinessDocumentError) else "SQL_CONVERSATION_INVALID",
                "message": "Не удалось продолжить исследование. Принятый результат сохранён; проверьте доступ и условия перед повтором.",
            },
        )
        changed = (
            SqlAssistantSession.update(state=state, revision=current.revision + 1, update_time=current_timestamp(), update_date=datetime.now())
            .where((SqlAssistantSession.id == current.id) & (SqlAssistantSession.revision == current.revision))
            .execute()
        )
        if changed != 1:
            raise ConflictError("SQL_SESSION_VERSION_CONFLICT", "Исследование изменилось во время завершения ответа")
