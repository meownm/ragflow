from __future__ import annotations

import pytest
import sys
from pathlib import Path
from types import ModuleType

if "api.apps" not in sys.modules:
    api_apps = ModuleType("api.apps")
    api_apps.__path__ = [str(Path(__file__).resolve().parents[5] / "api" / "apps")]
    sys.modules["api.apps"] = api_apps

from api.apps.business_documents import sql_query_conclusions

from business_documents.sql_query.conclusion import ConclusionValidationError, validate_conclusion


def test_conclusion_checks_numbers_against_cited_cells():
    result = validate_conclusion(
        "Получено 2 строки, значение 42.",
        [{"row_index": 0, "column": "value"}],
        ["value"], [[42], [43]], 2,
    )
    assert result["citations"] == [{"row_index": 0, "column": "value"}]
    with pytest.raises(ConclusionValidationError, match="Numeric claims"):
        validate_conclusion("Итого 100.", [{"row_index": 0, "column": "value"}], ["value"], [[42]], 1)


def test_conclusion_rejects_uncited_or_out_of_range_cell():
    with pytest.raises(ConclusionValidationError, match="must cite"):
        validate_conclusion("Есть результат", [], ["value"], [[42]], 1)
    with pytest.raises(ConclusionValidationError, match="outside"):
        validate_conclusion("Есть результат", [{"row_index": 2, "column": "value"}], ["value"], [[42]], 1)


def test_model_receives_a_bounded_result_and_uncited_numbers_fail(monkeypatch):
    seen = []

    class FakeLLM:
        async def async_generate(self, tenant_id, prompt, payload):
            seen.append(payload["context"])
            return {"text": "Итого 999.", "citations": [{"row_index": 0, "column": "value"}]}

    monkeypatch.setattr(sql_query_conclusions, "RAGFlowLLMAdapter", FakeLLM)
    with pytest.raises(Exception, match="Numeric claims"):
        sql_query_conclusions.propose_conclusion("tenant-1", "Продажи", ["value"], [[42]] * 30, 30, "FULL")
    assert len(seen[0]["rows"]) == 25
    assert seen[0]["columns"] == ["value"]
