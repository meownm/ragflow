from types import SimpleNamespace
from unittest.mock import MagicMock

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


def test_list_eva_spaces_returns_project_names_instead_of_ids(monkeypatch):
    connector = SimpleNamespace(
        id="connector-1",
        name="EVA Wiki connector",
        config={"project_id": "CmfProject:project-1"},
    )
    query = MagicMock()
    query.where.return_value.order_by.return_value = [connector]
    monkeypatch.setattr(service.Connector, "select", staticmethod(lambda: query))
    monkeypatch.setattr(service, "_eva_project_name", lambda _connector: "Operations")

    assert service.list_business_documents_eva_spaces() == [
        {
            "connector_id": "connector-1",
            "connector_name": "EVA Wiki connector",
            "project_name": "Operations",
        }
    ]
