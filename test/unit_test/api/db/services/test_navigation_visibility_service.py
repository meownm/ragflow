import json
from types import SimpleNamespace

import pytest

from api.db.services import navigation_visibility_service as service


def test_validate_visible_sections_returns_canonical_order():
    assert service.validate_visible_sections(["file_manager", "home", "chat"]) == [
        "home",
        "chat",
        "file_manager",
    ]


@pytest.mark.parametrize(
    "value",
    ["chat", ["chat", "unknown"], ["chat", "chat"], ["chat", 1]],
)
def test_validate_visible_sections_rejects_invalid_values(value):
    with pytest.raises((TypeError, ValueError)):
        service.validate_visible_sections(value)


def test_get_visible_sections_reads_versioned_persisted_value(monkeypatch):
    monkeypatch.setattr(
        service.SystemSettingsService,
        "get_by_name",
        lambda _name: [SimpleNamespace(value=json.dumps({"version": 3, "visible_sections": ["memory", "dataset"]}))],
    )

    assert service.get_visible_sections() == ["dataset", "memory", "asr", "translator"]


def test_existing_constructor_setting_keeps_sql_projects_visible(monkeypatch):
    monkeypatch.setattr(
        service.SystemSettingsService,
        "get_by_name",
        lambda _name: [SimpleNamespace(value=json.dumps({"version": 3, "visible_sections": ["document_constructor"]}))],
    )

    assert service.get_visible_sections() == ["document_constructor", "sql_projects", "asr", "translator"]


def test_existing_visibility_settings_expose_new_workspaces(monkeypatch):
    monkeypatch.setattr(
        service.SystemSettingsService,
        "get_by_name",
        lambda _name: [SimpleNamespace(value=json.dumps({"version": 4, "visible_sections": ["chat"]}))],
    )

    assert service.get_visible_sections() == ["chat", "asr", "translator"]


def test_get_visible_sections_keeps_home_for_legacy_setting(monkeypatch):
    monkeypatch.setattr(
        service.SystemSettingsService,
        "get_by_name",
        lambda _name: [SimpleNamespace(value=json.dumps(["memory", "dataset"]))],
    )

    assert service.get_visible_sections() == ["home", "dataset", "memory", "asr", "translator"]


@pytest.mark.parametrize(
    "stored",
    [
        {"version": 2, "visible_sections": ["business_documents", "chat"]},
        ["business_documents", "chat"],
    ],
)
def test_get_visible_sections_keeps_existing_constructor_visibility(monkeypatch, stored):
    monkeypatch.setattr(
        service.SystemSettingsService,
        "get_by_name",
        lambda _name: [SimpleNamespace(value=json.dumps(stored))],
    )

    assert "document_constructor" in service.get_visible_sections()
    assert "sql_projects" in service.get_visible_sections()


def test_get_visible_sections_can_hide_only_constructor(monkeypatch):
    stored = service.serialize_visible_sections(["business_documents"])
    monkeypatch.setattr(
        service.SystemSettingsService,
        "get_by_name",
        lambda _name: [SimpleNamespace(value=stored)],
    )

    assert service.get_visible_sections() == ["business_documents"]


def test_get_visible_sections_can_hide_sql_projects_independently(monkeypatch):
    stored = service.serialize_visible_sections(["document_constructor"])
    monkeypatch.setattr(
        service.SystemSettingsService,
        "get_by_name",
        lambda _name: [SimpleNamespace(value=stored)],
    )

    assert service.get_visible_sections() == ["document_constructor"]


def test_serialize_visible_sections_records_current_version():
    assert json.loads(service.serialize_visible_sections(["chat"])) == {
        "version": 7,
        "visible_sections": ["chat"],
    }


@pytest.mark.parametrize("version", [None, 2, 3, 4, 5])
@pytest.mark.parametrize("visible", [False, True])
def test_existing_settings_preserve_specifications_visibility(monkeypatch, version, visible):
    sections = ["business_documents"] if visible else []
    stored = sections if version is None else {"version": version, "visible_sections": sections}
    monkeypatch.setattr(
        service.SystemSettingsService,
        "get_by_name",
        lambda _name: [SimpleNamespace(value=json.dumps(stored))],
    )
    assert ("technical_specifications" in service.get_visible_sections()) is visible


@pytest.mark.parametrize("sections", [["technical_specifications"], ["business_documents"], []])
def test_saved_specifications_visibility_is_independent(monkeypatch, sections):
    stored = service.serialize_visible_sections(sections)
    monkeypatch.setattr(
        service.SystemSettingsService,
        "get_by_name",
        lambda _name: [SimpleNamespace(value=stored)],
    )
    assert service.get_visible_sections() == sections


def test_unconfigured_installation_hides_sql_assistant(monkeypatch):
    monkeypatch.setattr(
        service.SystemSettingsService,
        "get_by_name",
        lambda _name: [],
    )

    assert service.get_visible_sections() == [section for section in service.NAVIGATION_SECTIONS if section != "sql_assistant"]


@pytest.mark.parametrize("version", [2, 3, 4, 5, 6])
def test_existing_installation_does_not_expose_sql_assistant(monkeypatch, version):
    stored = {"version": version, "visible_sections": ["business_documents", "document_constructor", "sql_projects"]}
    monkeypatch.setattr(service.SystemSettingsService, "get_by_name", lambda _name: [SimpleNamespace(value=json.dumps(stored))])
    assert "sql_assistant" not in service.get_visible_sections()


def test_sql_assistant_can_be_enabled_without_enabling_old_sql_section(monkeypatch):
    stored = service.serialize_visible_sections(["sql_assistant"])
    monkeypatch.setattr(service.SystemSettingsService, "get_by_name", lambda _name: [SimpleNamespace(value=stored)])
    assert service.get_visible_sections() == ["sql_assistant"]


@pytest.mark.parametrize(
    "records",
    [
        [SimpleNamespace(value="not-json")],
        [SimpleNamespace(value='{"version":999,"visible_sections":["chat"]}')],
        [SimpleNamespace(value='{"version":5,"visible_sections":["unknown"]}')],
        [SimpleNamespace(value="[]"), SimpleNamespace(value="[]")],
    ],
)
def test_invalid_configured_policy_denies_sections(monkeypatch, records):
    monkeypatch.setattr(service.SystemSettingsService, "get_by_name", lambda _name: records)
    assert service.get_visible_sections() == []
