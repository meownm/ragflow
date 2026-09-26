"""Pure role rules for the Business Documents extension."""

from __future__ import annotations

from enum import StrEnum
from dataclasses import dataclass


class BusinessDocumentRole(StrEnum):
    AUTHOR_CREATOR = "AUTHOR_CREATOR"
    AUTHOR_EDITOR = "AUTHOR_EDITOR"
    MODERATOR_CREATOR = "MODERATOR_CREATOR"
    EXTENDED_MODERATOR = "EXTENDED_MODERATOR"
    ADMIN = "ADMIN"


def normalize_role(
    value: object,
    is_admin: bool = False,
) -> BusinessDocumentRole:
    """Resolve the effective role without trusting a claimed administrator role."""

    if is_admin:
        return BusinessDocumentRole.ADMIN
    try:
        role = BusinessDocumentRole(value)
    except (TypeError, ValueError):
        return BusinessDocumentRole.AUTHOR_EDITOR
    if role == BusinessDocumentRole.ADMIN:
        return BusinessDocumentRole.AUTHOR_EDITOR
    return role


def can_assign_document(value: object, is_admin: bool = False) -> bool:
    """Return whether the effective role may assign a document owner."""

    return normalize_role(value, is_admin) in {
        BusinessDocumentRole.EXTENDED_MODERATOR,
        BusinessDocumentRole.ADMIN,
    }


@dataclass(frozen=True)
class DocumentAccess:
    actor_id: str
    assigned_role: BusinessDocumentRole | str = BusinessDocumentRole.AUTHOR_CREATOR
    is_admin: bool = False

    @property
    def role(self) -> BusinessDocumentRole:
        return normalize_role(self.assigned_role, self.is_admin)

    def capabilities(self) -> dict[str, bool]:
        role = self.role
        return {
            "read": True,
            "create": role in {BusinessDocumentRole.AUTHOR_CREATOR, BusinessDocumentRole.MODERATOR_CREATOR, BusinessDocumentRole.EXTENDED_MODERATOR, BusinessDocumentRole.ADMIN},
            "edit_own": True,
            "edit_all": role in {BusinessDocumentRole.MODERATOR_CREATOR, BusinessDocumentRole.EXTENDED_MODERATOR, BusinessDocumentRole.ADMIN},
            "delete": role in {BusinessDocumentRole.EXTENDED_MODERATOR, BusinessDocumentRole.ADMIN},
            "assign": can_assign_document(self.assigned_role, self.is_admin),
        }

    def permissions(self, owner_id: str) -> dict[str, bool]:
        capabilities = self.capabilities()
        return {
            "read": True,
            "edit": capabilities["edit_all"] or owner_id == self.actor_id,
            "delete": capabilities["delete"],
            "assign": capabilities["assign"],
        }
