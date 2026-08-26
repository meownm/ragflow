#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#

from common.openmetadata_agents import OPENMETADATA_AGENT_ROLES, build_openmetadata_agent_dsl, managed_agent_id


def test_managed_agent_ids_are_stable_and_role_specific():
    first = managed_agent_id("owner", "catalog_copilot")
    assert first == managed_agent_id("owner", "catalog_copilot")
    assert first != managed_agent_id("owner", "discovery")
    assert len(first) == 32


def test_every_openmetadata_role_builds_an_executable_canvas():
    for role in OPENMETADATA_AGENT_ROLES:
        dsl = build_openmetadata_agent_dsl(role)
        component_id = f"OpenMetadata:{role['id'].replace('_', '')}"
        message_id = f"Message:{role['id']}"

        assert dsl["meta"] == {
            "managed_by": "openmetadata_copilot",
            "role_id": role["id"],
            "schema_version": 3,
        }
        assert dsl["components"]["begin"]["downstream"] == [component_id]
        assert dsl["components"][component_id]["obj"]["component_name"] == "OpenMetadata"
        assert dsl["components"][component_id]["downstream"] == [message_id]
        assert dsl["components"][message_id]["obj"]["params"]["content"] == [f"{{{component_id}@content}}"]
        assert dsl["globals"]["sys.openmetadata_context"] == []
        note = dsl["graph"]["nodes"][0]
        assert note["type"] == "noteNode"
        assert note["data"] == {
            "label": "Note",
            "name": "Примечания к агенту",
            "form": {"text": role["description"]},
        }
        assert dsl["components"]["begin"]["obj"]["params"]["prologue"] == role["prologue"]
        assert dsl["graph"]["nodes"][1]["data"]["form"]["enablePrologue"] is True
        assert dsl["graph"]["nodes"][1]["data"]["form"]["prologue"] == role["prologue"]
        assert dsl["graph"]["nodes"][2]["data"]["label"] == "OpenMetadata"
        assert len(dsl["graph"]["nodes"]) == 4
        assert len(dsl["graph"]["edges"]) == 2


def test_every_openmetadata_role_has_a_complete_user_note():
    required_sections = (
        "Что делает:",
        "Как работает:",
        "Для чего предназначен:",
        "Примеры вопросов:",
    )

    for role in OPENMETADATA_AGENT_ROLES:
        assert all(section in role["description"] for section in required_sections)
        assert role["description"].count("«") >= 3


def test_every_openmetadata_role_has_a_descriptive_greeting_with_real_examples():
    concrete_catalog_terms = (
        "docker_postgres_",
        "MeetingsScheduling",
        "Telephony",
        "ApplicationDocs",
        "weekly_rules",
    )

    for role in OPENMETADATA_AGENT_ROLES:
        prologue = role["prologue"]
        assert "Здравствуйте!" in prologue
        assert "Примеры вопрос" in prologue or "Примеры запрос" in prologue
        assert prologue.count("«") >= 3
        assert any(term in prologue for term in concrete_catalog_terms)
