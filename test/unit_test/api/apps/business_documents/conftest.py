"""Synthetic AI unit suites isolate renderer I/O; repair tests inject diagnostics explicitly."""

import pytest

from business_documents.adapters.plantuml import PlantUmlRenderer
from business_documents.application.diagram_repair import DiagramCheck


@pytest.fixture(autouse=True)
def isolated_diagram_renderer(monkeypatch):
    monkeypatch.setattr(PlantUmlRenderer, "check", lambda self, source: DiagramCheck("valid"))
