import pytest

from types import SimpleNamespace

from api.db.services.access_group_service import (
    AccessGroupService,
    AccessGroupValidationError,
    sections_for_request,
    validate_group_payload,
)


def test_group_payload_normalizes_sections_in_navigation_order():
    payload = validate_group_payload({"name": " Legal ", "description": " Docs ", "user_ids": [], "dataset_ids": [], "sections": ["file_manager", "dataset"]})
    assert payload["name"] == "Legal"
    assert payload["description"] == "Docs"
    assert payload["sections"] == ["dataset", "file_manager"]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"name": "x", "description": "", "user_ids": ["u", "u"], "dataset_ids": [], "sections": []},
        {"name": "x", "description": "", "user_ids": [], "dataset_ids": [], "sections": ["unknown"]},
    ],
)
def test_group_payload_rejects_invalid_contract(payload):
    with pytest.raises(AccessGroupValidationError):
        validate_group_payload(payload)


@pytest.mark.parametrize("section", ["home", "document_constructor", "sql_projects"])
def test_group_payload_accepts_independent_sections(section):
    assert validate_group_payload({"name": "x", "sections": [section]})["sections"] == [section]


def test_section_mapping_covers_protected_application_entrypoints():
    assert sections_for_request("dataset_api", "/api/v1/datasets") == ("dataset",)
    assert sections_for_request("source_workbench_api", "/api/v1/source-workspaces") == ("search",)
    assert sections_for_request("business_document_api", "/api/v1/business-documents") == ("business_documents",)
    assert sections_for_request("system_api", "/api/v1/system/config") == ()
    assert sections_for_request("chat_api", "/api/v1/chat/mindmap") == ("chat", "search")
    assert sections_for_request("chat_api", "/api/v1/chat/recommendation") == ("chat", "search")
    assert sections_for_request("chat_api", "/api/v1/chat/completions") == ("chat",)
    assert sections_for_request("bot_api", "/api/v1/searchbots/mindmap") == ("search",)
    assert sections_for_request("bot_api", "/api/v1/agentbots/completions") == ("agent",)
    assert sections_for_request("document_api", "/api/v1/thumbnails") == ("dataset", "chat", "search", "agent")
    assert sections_for_request("document_api", "/api/v1/documents/upload") == ("dataset",)


def test_group_sections_are_unioned_and_superuser_bypasses(monkeypatch):
    user = SimpleNamespace(id="u1", is_superuser=False)
    monkeypatch.setattr(AccessGroupService, "effective_policy", classmethod(lambda cls, user_id: {"sections": {"dataset"}, "dataset_ids": set()}))
    assert AccessGroupService.has_section_access(user, ("dataset",)) is True
    assert AccessGroupService.has_section_access(user, ("chat",)) is False
    assert AccessGroupService.has_section_access(SimpleNamespace(id="admin", is_superuser=True), ("chat",)) is True


def test_article_dataset_choices_need_search_but_not_dataset_section(monkeypatch):
    user = SimpleNamespace(id="reader", is_superuser=False)
    monkeypatch.setattr(
        AccessGroupService,
        "effective_policy",
        classmethod(lambda cls, user_id: {"sections": {"search"}, "dataset_ids": {"kb-1"}}),
    )
    assert AccessGroupService.has_section_access(user, sections_for_request("source_workbench_api", "/api/v1/source-workspaces"))
    assert not AccessGroupService.has_section_access(user, sections_for_request("dataset_api", "/api/v1/datasets"))
    assert AccessGroupService.allowed_dataset_ids(user.id) == {"kb-1"}


@pytest.mark.parametrize("is_admin", [False, True])
@pytest.mark.parametrize("sections", [set(), {"home"}, {"search"}, {"business_documents"}, {"document_constructor"}, {"sql_projects"}])
def test_group_visibility_replaces_global_list_without_inheritance(monkeypatch, sections, is_admin):
    from api.db.services import access_group_service as service
    from api.db.services.navigation_visibility_service import NAVIGATION_SECTIONS

    monkeypatch.setattr(service, "get_visible_sections", lambda: ["chat", "business_documents", "document_constructor", "sql_projects"])
    monkeypatch.setattr(AccessGroupService, "effective_policy", staticmethod(lambda _id: {"sections": sections, "dataset_ids": set()}))
    user = SimpleNamespace(id="member", is_superuser=is_admin)
    assert AccessGroupService.visible_sections(user) == [section for section in NAVIGATION_SECTIONS if section in sections]


@pytest.mark.parametrize("user", [None, SimpleNamespace(id="ungrouped", is_superuser=False), SimpleNamespace(id="admin", is_superuser=True)])
def test_ungrouped_visibility_uses_global_settings(monkeypatch, user):
    from api.db.services import access_group_service as service

    monkeypatch.setattr(service, "get_visible_sections", lambda: ["chat"])
    monkeypatch.setattr(AccessGroupService, "effective_policy", staticmethod(lambda _id: None))
    assert AccessGroupService.visible_sections(user) == ["chat"]


@pytest.mark.parametrize("section", ["business_documents", "document_constructor", "sql_projects"])
@pytest.mark.parametrize(
    "route,allowed",
    [
        ("/business-documents/capabilities", {"business_documents", "document_constructor", "sql_projects"}),
        ("/business-documents", {"business_documents"}),
        ("/business-documents/<document_id>", {"business_documents"}),
        ("/business-documents/sql-query/projects", {"sql_projects"}),
        ("/business-documents/sql-query/projects/<project_id>/runs", {"sql_projects"}),
        ("/business-documents/sql-query/schema/resolve", {"document_constructor", "sql_projects"}),
        ("/business-documents/sql-query/schema/entities", {"document_constructor", "sql_projects"}),
        ("/business-documents/sql-query/compile", {"document_constructor", "sql_projects"}),
        ("/business-documents/sql-query/plan", {"document_constructor", "sql_projects"}),
    ],
)
def test_independent_document_surfaces_keep_only_their_api_access(monkeypatch, section, route, allowed):
    user = SimpleNamespace(id="member", is_superuser=False)
    monkeypatch.setattr(AccessGroupService, "effective_policy", staticmethod(lambda _id: {"sections": {section}, "dataset_ids": set()}))
    assert AccessGroupService.has_section_access(user, sections_for_request("business_document_api", "/api/v1" + route)) is (section in allowed)
