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

"""Idempotent provisioning for managed OpenMetadata RAGFlow Agent Apps."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from common.openmetadata_agents import (
    MANAGED_BY,
    OPENMETADATA_AGENT_ROLES,
    build_openmetadata_agent_dsl,
    managed_agent_id,
)


def provision_openmetadata_agents(owner_id: str) -> dict[str, Any]:
    from api.apps.services.canvas_replica_service import CanvasReplicaService
    from api.db import CanvasCategory, TenantPermission
    from api.db.services.canvas_service import UserCanvasService
    from api.db.services.user_canvas_version import UserCanvasVersionService

    created: list[str] = []
    updated: list[str] = []
    agents: list[dict[str, str]] = []
    owner_id = str(owner_id)

    for role in OPENMETADATA_AGENT_ROLES:
        agent_id = managed_agent_id(owner_id, role["id"])
        dsl = CanvasReplicaService.normalize_dsl(build_openmetadata_agent_dsl(role))
        exists, canvas = UserCanvasService.get_by_id(agent_id)
        if exists:
            current_dsl = canvas.dsl if isinstance(canvas.dsl, dict) else {}
            marker = current_dsl.get("meta") or {}
            if canvas.user_id != owner_id or marker.get("managed_by") != MANAGED_BY:
                raise RuntimeError(f"Managed OpenMetadata Agent id collision: {agent_id}")
            UserCanvasService.update_by_id(
                agent_id,
                {
                    "title": role["title"],
                    "description": role["description"],
                    "permission": TenantPermission.ME.value,
                    "release": False,
                    "canvas_type": "openmetadata",
                    "canvas_category": CanvasCategory.Agent,
                    "tags": "openmetadata,managed",
                    "dsl": deepcopy(dsl),
                },
            )
            updated.append(agent_id)
        else:
            UserCanvasService.save(
                id=agent_id,
                user_id=owner_id,
                title=role["title"],
                description=role["description"],
                permission=TenantPermission.ME.value,
                release=False,
                canvas_type="openmetadata",
                canvas_category=CanvasCategory.Agent,
                tags="openmetadata,managed",
                dsl=deepcopy(dsl),
            )
            created.append(agent_id)

        UserCanvasVersionService.save_or_replace_latest(
            user_canvas_id=agent_id,
            title=f"OpenMetadata_{role['id']}",
            description=role["description"],
            dsl=dsl,
            release=False,
        )
        replica_ok = CanvasReplicaService.replace_for_set(
            canvas_id=agent_id,
            tenant_id=owner_id,
            runtime_user_id=owner_id,
            dsl=dsl,
            canvas_category=CanvasCategory.Agent,
            title=role["title"],
        )
        if not replica_ok:
            raise RuntimeError(f"Failed to sync OpenMetadata Agent replica: {role['id']}")
        agents.append(
            {
                "id": agent_id,
                "role_id": role["id"],
                "title": role["title"],
                "url": f"/agent/{agent_id}/explore",
            }
        )

    return {
        "managed_by": MANAGED_BY,
        "created": created,
        "updated": updated,
        "count": len(agents),
        "agents": agents,
    }
