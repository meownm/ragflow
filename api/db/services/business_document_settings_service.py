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

from api.db.db_models import Connector
from api.db.services.system_settings_service import SystemSettingsService
from common.data_source.config import DocumentSource


BUSINESS_DOCUMENTS_EVA_CONNECTOR_SETTING = "business_documents.eva_connector_id"


def get_business_documents_eva_connector_id() -> str | None:
    settings = SystemSettingsService.get_by_name(BUSINESS_DOCUMENTS_EVA_CONNECTOR_SETTING)
    if len(settings) != 1:
        return None
    connector_id = str(settings[0].value or "").strip()
    return connector_id or None


def validate_business_documents_eva_connector_id(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("eva_connector_id must be a string or null")

    connector_id = value.strip()
    if not connector_id:
        return None

    connector = Connector.get_or_none(Connector.id == connector_id)
    if connector is None or connector.source != DocumentSource.EVA_WIKI.value:
        raise ValueError("The selected EVA Wiki space does not exist")
    if not str((connector.config or {}).get("project_id") or "").strip():
        raise ValueError("The selected EVA Wiki connector has no configured space")
    return connector_id


def list_business_documents_eva_spaces() -> list[dict[str, str]]:
    rows = Connector.select().where(Connector.source == DocumentSource.EVA_WIKI.value).order_by(Connector.name.asc(), Connector.id.asc())
    spaces: list[dict[str, str]] = []
    for connector in rows:
        project_id = str((connector.config or {}).get("project_id") or "").strip()
        if not project_id:
            continue
        spaces.append(
            {
                "connector_id": connector.id,
                "connector_name": connector.name,
                "project_id": project_id,
            }
        )
    return spaces
