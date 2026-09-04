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
    AUTHOR = "AUTHOR"
    ADMIN = "ADMIN"


@dataclass(frozen=True)
class BusinessDocumentAccess:
    actor_id: str
    is_admin: bool = False

    @property
    def role(self) -> BusinessDocumentRole:
        return BusinessDocumentRole.ADMIN if self.is_admin else BusinessDocumentRole.AUTHOR

    def permissions(self) -> dict[str, bool]:
        return {
            "read": True,
            "edit": True,
            "delete": self.is_admin,
        }

    def require_delete(self) -> None:
        if not self.is_admin:
            raise PermissionDeniedError("Only an administrator can delete business documents")
