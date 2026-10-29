"""Exact cells, bounded context and no computation from incomplete query output."""

import json

import pytest

from business_documents.sql_assistant import default_view, output_columns
from business_documents.sql_result_answer import answer_context, render_answer, validate_answer


def context(rows=None):
    columns = output_columns("SELECT name AS name, amount AS amount FROM public.orders", ["name", "amount"])
    return answer_context(
        "Кто первый?",
        "run",
        rows if rows is not None else [["Лидер", "1234567890.00100"], ["Ноль", 0], ["Нет", None]],
        columns,
        default_view(columns),
        "SELECT name AS name, amount AS amount FROM public.orders",
        {},
        True,
    )


def test_answer_inserts_exact_strings_zero_and_null_from_verified_cells():
    data = context()
    name, amount = [column["id"] for column in data["columns"]]
    answer = render_answer({"kind": "ANSWER", "text": f"Лидер — {{{{0:{name}}}}}, значение — {{{{0:{amount}}}}}. Ещё: {{{{1:{amount}}}}} и {{{{2:{amount}}}}}."}, data)
    assert answer["text"] == "Лидер — Лидер, значение — 1234567890.00100. Ещё: 0 и NULL."
    assert answer["scope"] == "LIMITED"
    assert len(answer["citations"]) == 4


@pytest.mark.parametrize(
    "plan",
    [
        {"kind": "ANSWER", "text": "Всего 42"},
        {"kind": "ANSWER", "text": "Лидер определён"},
        {"kind": "ANSWER", "text": "{{999:out_ab_1}}"},
        {"kind": "ANSWER", "text": "{{0:out_bad_1}}"},
        {"kind": "ANSWER", "text": "SELECT a FROM b"},
        {"kind": "ANSWER", "text": "{{0:out_ab_1}}", "sql": "SELECT 1"},
        {"kind": "NEEDS_QUERY", "text": "Новый расчёт 42"},
    ],
)
def test_invalid_or_ungrounded_model_answers_are_rejected(plan):
    with pytest.raises(ValueError):
        validate_answer(plan, context())


def test_saved_view_controls_first_row_and_hidden_columns_never_reach_model():
    columns = output_columns("SELECT a AS a, b AS b FROM t", ["a", "b"])
    view = default_view(columns)
    view["hidden"] = [columns[1]["id"]]
    view["sort"] = {"column_id": columns[0]["id"], "direction": "DESC"}
    view["filters"] = [{"column_id": columns[0]["id"], "operator": "gte", "value": 100}]
    data = answer_context("Первый", "run", [[i, "secret"] for i in range(250)], columns, view, "SELECT a AS a, b AS b FROM t", {}, False)
    assert data["rows"][0]["index"] == 249
    assert data["view_row_count"] == 150
    assert len(data["rows"]) == 100
    assert data["context_limited"] is True
    assert "secret" not in str(data)


def test_clipped_cell_is_not_used_as_an_exact_answer():
    data = context([["x" * 1000, 1]])
    key = data["columns"][0]["id"]
    with pytest.raises(ValueError, match="обрезана"):
        validate_answer({"kind": "ANSWER", "text": f"{{{{0:{key}}}}}"}, data)


def test_empty_and_new_calculation_are_distinct_from_zero():
    answer = render_answer({"kind": "OVERVIEW", "text": ""}, context([]))
    assert "Пустой результат не означает нулевое" in answer["text"]
    answer = render_answer({"kind": "NEEDS_QUERY", "text": ""}, context())
    assert "Изменить запрос" in answer["text"]
    assert answer["citations"] == []


def test_context_budget_counts_utf8_rows_and_marks_a_partial_prefix():
    columns = output_columns("SELECT name AS name, amount AS amount FROM t", ["name", "amount"])
    rows = [["я" * 500, index] for index in range(100)]
    data = answer_context("Покажи первую строку", "run", rows, columns, default_view(columns), "SELECT name AS name, amount AS amount FROM t", {}, False)
    assert len(json.dumps(data, ensure_ascii=False).encode()) <= 72_000
    assert 0 < len(data["rows"]) < 100
    assert [row["index"] for row in data["rows"]] == list(range(len(data["rows"])))
    assert data["context_limited"] is True
    assert data["rows"][0]["cells"][columns[0]["id"]]["value"] == "я" * 500


def test_oversized_metadata_is_rejected_even_with_no_rows():
    columns = output_columns("SELECT amount AS amount FROM t", ["amount"])
    with pytest.raises(ValueError, match="Контекст результата слишком велик"):
        answer_context("Вопрос", "run", [], columns, default_view(columns), "SELECT amount AS amount FROM t", {"value": "я" * 40_000}, False)
