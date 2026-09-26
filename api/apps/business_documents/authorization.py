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


from business_documents.application.errors import PermissionDeniedError
from business_documents.domain.access import DocumentAccess, can_assign_document


__all__ = ["BusinessDocumentAccess"]


class BusinessDocumentAccess(DocumentAccess):
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
        if not can_assign_document(self.assigned_role, self.is_admin):
            raise PermissionDeniedError("Only an extended moderator or administrator can assign business documents")
