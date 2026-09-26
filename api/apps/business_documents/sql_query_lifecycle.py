"""RAGFlow transport adapter for the owned SQL query compiler."""

from __future__ import annotations

from typing import Any

from api.apps.business_documents.authorization import BusinessDocumentAccess
from business_documents.application.errors import ValidationError
from business_documents.sql_query.query_specification import (
    QuerySpecificationValidationError,
    SqlGuardError,
    compile_query_payload,
)


class BusinessDocumentSqlQueryService:
    """Authorize the actor and invoke the pure query specification owner."""

    @classmethod
    def compile(
        cls,
        actor_id: str,
        payload: Any,
        is_admin: bool,
        access_role: str,
    ) -> dict[str, Any]:
        BusinessDocumentAccess(actor_id=actor_id, assigned_role=access_role, is_admin=is_admin).require_create()
        try:
            return compile_query_payload(payload)
        except QuerySpecificationValidationError as exc:
            raise ValidationError("INVALID_SQL_QUERY_SPECIFICATION", str(exc)) from exc
        except SqlGuardError as exc:
            raise ValidationError("SQL_QUERY_BLOCKED", "SQL не прошёл обязательную read-only проверку.", {"reason": str(exc)}) from exc
