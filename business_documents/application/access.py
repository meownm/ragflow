"""Administrator-owned changes to a user's Business Documents role."""

from collections.abc import Mapping
from contextlib import AbstractContextManager
from typing import Any, Protocol

from business_documents.application.errors import BusinessDocumentError, ConflictError, PermissionDeniedError, ValidationError
from business_documents.domain.access import BusinessDocumentRole


class UserRoleWriter(Protocol):
    def transaction(self) -> AbstractContextManager: ...
    def lock_user(self, user_id: str) -> Mapping[str, Any] | None: ...
    def set_role(self, user_id: str, role: str) -> None: ...


class ChangeUserRole:
    def __init__(self, writer: UserRoleWriter):
        self._writer = writer

    def execute(self, user_id: str, raw: object, is_admin: bool = False) -> dict[str, Any]:
        if not is_admin:
            raise PermissionDeniedError("Only an administrator can change business document roles")
        if not isinstance(raw, dict):
            raise ValidationError("INVALID_ACCESS_ROLE", "Request body must be a JSON object")
        try:
            role = BusinessDocumentRole(raw.get("role"))
        except (TypeError, ValueError) as error:
            raise ValidationError("INVALID_ACCESS_ROLE", "Unknown business document role") from error
        if role == BusinessDocumentRole.ADMIN:
            raise ValidationError("INVALID_ACCESS_ROLE", "Administrator access is controlled by the superuser flag")
        with self._writer.transaction():
            user = self._writer.lock_user(user_id)
            if user is None:
                raise BusinessDocumentError("USER_NOT_FOUND", "User not found", 404)
            if user["is_superuser"]:
                raise ConflictError("ADMIN_ROLE_MANAGED_SEPARATELY", "A superuser always has administrator access")
            self._writer.set_role(user_id, role.value)
        return {"user_id": user_id, "nickname": user["nickname"], "role": role.value}
