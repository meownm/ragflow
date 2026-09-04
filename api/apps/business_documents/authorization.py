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

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from api.apps.business_documents.errors import PermissionDeniedError


class BusinessDocumentRole(StrEnum):
    AUTHOR_CREATOR = "AUTHOR_CREATOR"
    AUTHOR_EDITOR = "AUTHOR_EDITOR"
    MODERATOR_CREATOR = "MODERATOR_CREATOR"
    EXTENDED_MODERATOR = "EXTENDED_MODERATOR"
    ADMIN = "ADMIN"


@dataclass(frozen=True)
class BusinessDocumentAccess:
    actor_id: str
    assigned_role: BusinessDocumentRole | str = BusinessDocumentRole.AUTHOR_CREATOR
    is_admin: bool = False

    @property
    def role(self) -> BusinessDocumentRole:
        if self.is_admin:
            return BusinessDocumentRole.ADMIN
        try:
            role = BusinessDocumentRole(self.assigned_role)
        except ValueError:
            return BusinessDocumentRole.AUTHOR_EDITOR
        return BusinessDocumentRole.AUTHOR_EDITOR if role == BusinessDocumentRole.ADMIN else role

    def capabilities(self) -> dict[str, bool]:
        role = self.role
        return {
            "read": True,
            "create": role
            in {
                BusinessDocumentRole.AUTHOR_CREATOR,
                BusinessDocumentRole.MODERATOR_CREATOR,
                BusinessDocumentRole.EXTENDED_MODERATOR,
                BusinessDocumentRole.ADMIN,
            },
            "edit_own": True,
            "edit_all": role
            in {
                BusinessDocumentRole.MODERATOR_CREATOR,
                BusinessDocumentRole.EXTENDED_MODERATOR,
                BusinessDocumentRole.ADMIN,
            },
            "delete": role in {BusinessDocumentRole.EXTENDED_MODERATOR, BusinessDocumentRole.ADMIN},
            "assign": role in {BusinessDocumentRole.EXTENDED_MODERATOR, BusinessDocumentRole.ADMIN},
        }

    def permissions(self, owner_id: str) -> dict[str, bool]:
        capabilities = self.capabilities()
        return {
            "read": True,
            "edit": capabilities["edit_all"] or owner_id == self.actor_id,
            "delete": capabilities["delete"],
            "assign": capabilities["assign"],
        }

    def require_create(self) -> None:
        if not self.capabilities()["create"]:
            raise PermissionDeniedError("This role cannot create business documents")

    def require_edit(self, owner_id: str) -> None:
        if not self.permissions(owner_id)["edit"]:
            raise PermissionDeniedError("Only the document owner or a moderator can edit this business document")

    def require_delete(self) -> None:
        if not self.capabilities()["delete"]:
            raise PermissionDeniedError("Only an extended moderator or administrator can delete business documents")

    def require_assign(self) -> None:
        if not self.capabilities()["assign"]:
            raise PermissionDeniedError("Only an extended moderator or administrator can assign business documents")
