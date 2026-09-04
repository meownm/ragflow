import json
from types import SimpleNamespace

import pytest

from api.db.services import navigation_visibility_service as service


def test_validate_visible_sections_returns_canonical_order():
    assert service.validate_visible_sections(["file_manager", "chat"]) == [
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


def test_get_visible_sections_reads_persisted_value(monkeypatch):
    monkeypatch.setattr(
        service.SystemSettingsService,
        "get_by_name",
        lambda _name: [SimpleNamespace(value=json.dumps(["memory", "dataset"]))],
    )

    assert service.get_visible_sections() == ["dataset", "memory"]


@pytest.mark.parametrize("records", [[], [SimpleNamespace(value="not-json")]])
def test_get_visible_sections_falls_back_to_all_sections(monkeypatch, records):
    monkeypatch.setattr(
        service.SystemSettingsService,
        "get_by_name",
        lambda _name: records,
    )

    assert service.get_visible_sections() == list(service.NAVIGATION_SECTIONS)
