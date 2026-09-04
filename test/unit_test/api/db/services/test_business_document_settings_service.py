from types import SimpleNamespace

import pytest

from api.db.services import business_document_settings_service as service


def test_get_selected_eva_connector_id_normalizes_blank(monkeypatch):
    monkeypatch.setattr(
        service.SystemSettingsService,
        "get_by_name",
        staticmethod(lambda _name: [SimpleNamespace(value="   ")]),
    )

    assert service.get_business_documents_eva_connector_id() is None


def test_validate_selected_eva_connector_requires_eva_space(monkeypatch):
    connector = SimpleNamespace(source="eva_wiki", config={"project_id": "CmfProject:docs"})
    monkeypatch.setattr(
        service.Connector,
        "get_or_none",
        staticmethod(lambda *_args: connector),
    )

    assert service.validate_business_documents_eva_connector_id(" connector-1 ") == "connector-1"

    connector.config = {}
    with pytest.raises(ValueError, match="no configured space"):
        service.validate_business_documents_eva_connector_id("connector-1")


def test_validate_selected_eva_connector_rejects_non_eva_connector(monkeypatch):
    monkeypatch.setattr(
        service.Connector,
        "get_or_none",
        staticmethod(lambda *_args: SimpleNamespace(source="jira", config={"project_id": "project-1"})),
    )

    with pytest.raises(ValueError, match="does not exist"):
        service.validate_business_documents_eva_connector_id("connector-1")
