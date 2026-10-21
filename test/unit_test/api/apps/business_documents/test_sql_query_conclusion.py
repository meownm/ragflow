from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import sys
from pathlib import Path
from types import ModuleType

import pytest

if "api.apps" not in sys.modules:
    api_apps = ModuleType("api.apps")
    api_apps.__path__ = [str(Path(__file__).resolve().parents[5] / "api" / "apps")]
    sys.modules["api.apps"] = api_apps

from api.apps.business_documents import sql_query_conclusions
from business_documents.sql_query.conclusion import ConclusionValidationError, validate_conclusion
from business_documents.application.errors import BusinessDocumentError


def test_conclusion_checks_numbers_against_cited_cells():
    result = validate_conclusion(
        "Получено 2 строки, значение 42.",
        [{"row_index": 0, "column": "value"}],
        ["value"],
        [[42], [43]],
        2,
    )
    assert result["citations"] == [{"row_index": 0, "column": "value"}]
    with pytest.raises(ConclusionValidationError, match="Numeric claims"):
        validate_conclusion("Итого 100.", [{"row_index": 0, "column": "value"}], ["value"], [[42]], 1)


def test_conclusion_rejects_uncited_or_out_of_range_cell():
    with pytest.raises(ConclusionValidationError, match="must cite"):
        validate_conclusion("Есть результат", [], ["value"], [[42]], 1)
    with pytest.raises(ConclusionValidationError, match="outside"):
        validate_conclusion("Есть результат", [{"row_index": 2, "column": "value"}], ["value"], [[42]], 1)
    with pytest.raises(ConclusionValidationError, match="at most 20"):
        validate_conclusion("Есть результат", [{"row_index": 0, "column": "value"}] * 21, ["value"], [[42]], 1)


def test_model_receives_a_bounded_result_and_uncited_numbers_fail(monkeypatch):
    seen = []
    loops = []

    class FakeLLM:
        async def async_generate(self, tenant_id, prompt, payload):
            loops.append(asyncio.get_running_loop())
            seen.append(payload["context"])
            return {"text": "Итого 999.", "citations": [{"row_index": 0, "column": "value"}]}

    monkeypatch.setattr(sql_query_conclusions, "RAGFlowLLMAdapter", FakeLLM)
    with ThreadPoolExecutor(max_workers=1) as executor:
        with pytest.raises(Exception, match="Numeric claims"):
            executor.submit(sql_query_conclusions.propose_conclusion, "tenant-1", "Продажи", ["value"], [[42]] * 30, 30, "FULL").result()
    assert len(seen[0]["rows"]) == 25
    assert seen[0]["columns"] == ["value"]
    assert len(loops) == 2 and all(loop.is_closed() for loop in loops)


def test_model_repairs_missing_range_citation_without_changing_evidence(monkeypatch):
    payloads = []
    responses = iter(
        [
            {"text": "Результат ограничен: от 4 до 40 снимков.", "citations": [{"row_index": 0, "column": "snapshots"}]},
            {"text": "Результат ограничен: от 4 до 40 снимков.", "citations": [{"row_index": 0, "column": "snapshots"}, {"row_index": 1, "column": "snapshots"}]},
        ]
    )

    class FakeLLM:
        async def async_generate(self, tenant_id, prompt, payload):
            payloads.append(payload)
            return next(responses)

    monkeypatch.setattr(sql_query_conclusions, "RAGFlowLLMAdapter", FakeLLM)
    result = sql_query_conclusions.propose_conclusion("tenant-1", "Снимки", ["snapshots"], [[4], [40]], 2, "LIMITED")
    assert len(result["citations"]) == 2
    assert len(payloads) == 2
    assert "format_repair" not in payloads[0]
    assert "Numeric claims" in payloads[1]["format_repair"]["error"]
    assert payloads[0]["context"] == payloads[1]["context"]


@pytest.mark.parametrize(
    "first",
    [
        {"text": "Получено значение 42.", "citations": [{"row_index": 0, "column": "value"}]},
        {"text": "Результат ограничен", "citations": [{"row_index": 9, "column": "value"}]},
        {"unexpected": "shape"},
    ],
)
def test_invalid_model_draft_gets_only_one_checked_repair(monkeypatch, first):
    calls = []

    class FakeLLM:
        async def async_generate(self, tenant_id, prompt, payload):
            calls.append(payload)
            return first

    monkeypatch.setattr(sql_query_conclusions, "RAGFlowLLMAdapter", FakeLLM)
    with pytest.raises(BusinessDocumentError) as error:
        sql_query_conclusions.propose_conclusion("tenant-1", "Продажи", ["value"], [[42]], 1, "LIMITED")
    assert error.value.code == "SQL_CONCLUSION_CHECK_FAILED"
    assert error.value.status == 422
    assert len(calls) == 2


def test_valid_draft_and_provider_failure_do_not_retry(monkeypatch):
    calls = []

    class FakeLLM:
        async def async_generate(self, tenant_id, prompt, payload):
            calls.append(payload)
            if len(calls) > 1:
                raise OSError("provider failed")
            return {"text": "Получено 42.", "citations": [{"row_index": 0, "column": "value"}]}

    monkeypatch.setattr(sql_query_conclusions, "RAGFlowLLMAdapter", FakeLLM)
    assert sql_query_conclusions.propose_conclusion("tenant-1", "Продажи", ["value"], [[42]], 1, "FULL")["text"] == "Получено 42."
    assert len(calls) == 1
    with pytest.raises(BusinessDocumentError) as error:
        sql_query_conclusions.propose_conclusion("tenant-1", "Продажи", ["value"], [[42]], 1, "FULL")
    assert error.value.code == "SQL_CONCLUSION_UNAVAILABLE"
    assert len(calls) == 2
