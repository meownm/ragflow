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

import json

from api.db.services.system_settings_service import SystemSettingsService


NAVIGATION_VISIBILITY_SETTING = "navigation.visible_sections"
NAVIGATION_VISIBILITY_VERSION = 7
NAVIGATION_SECTIONS = (
    "home",
    "dataset",
    "chat",
    "search",
    "agent",
    "memory",
    "catalog",
    "business_documents",
    "technical_specifications",
    "document_constructor",
    "sql_projects",
    "sql_assistant",
    "asr",
    "translator",
    "file_manager",
)


def validate_visible_sections(value: object) -> list[str]:
    if not isinstance(value, list):
        raise ValueError("visible_sections must be an array")
    if not all(isinstance(section, str) for section in value):
        raise ValueError("visible_sections must contain only strings")

    unknown_sections = sorted(set(value) - set(NAVIGATION_SECTIONS))
    if unknown_sections:
        raise ValueError(f"Unknown navigation sections: {', '.join(unknown_sections)}")
    if len(value) != len(set(value)):
        raise ValueError("visible_sections must not contain duplicates")

    selected = set(value)
    return [section for section in NAVIGATION_SECTIONS if section in selected]


def get_visible_sections() -> list[str]:
    settings = SystemSettingsService.get_by_name(NAVIGATION_VISIBILITY_SETTING)
    if not settings:
        return [section for section in NAVIGATION_SECTIONS if section != "sql_assistant"]
    if len(settings) != 1:
        return []

    try:
        stored = json.loads(settings[0].value)
        if isinstance(stored, dict):
            version = stored.get("version")
            if version not in (2, 3, 4, 5, 6, NAVIGATION_VISIBILITY_VERSION):
                raise ValueError("Unknown navigation visibility version")
            sections = stored.get("visible_sections")
            if version == 2 and isinstance(sections, list) and "business_documents" in sections:
                sections = [*sections, "document_constructor"]
            if version in (2, 3) and isinstance(sections, list) and "document_constructor" in sections:
                sections = [*sections, "sql_projects"]
            if version in (2, 3, 4) and isinstance(sections, list) and sections:
                sections = [*sections, "asr", "translator"]
            if version in (2, 3, 4, 5) and isinstance(sections, list) and "business_documents" in sections:
                sections = [*sections, "technical_specifications"]
            return validate_visible_sections(sections)

        # Settings written before the home toggle existed implicitly kept Home
        # visible. Preserve that behavior until the administrator saves a versioned setting.
        sections = ["home", *stored]
        if "business_documents" in sections:
            sections.append("technical_specifications")
            sections.append("document_constructor")
            sections.append("sql_projects")
        sections.extend(("asr", "translator"))
        return validate_visible_sections(sections)
    except (TypeError, ValueError, json.JSONDecodeError):
        return []


def serialize_visible_sections(value: object) -> str:
    return json.dumps(
        {
            "version": NAVIGATION_VISIBILITY_VERSION,
            "visible_sections": validate_visible_sections(value),
        }
    )
