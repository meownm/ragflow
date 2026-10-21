"""Corpus/scorer checks are not evidence that a real model passed the corpus."""

from copy import deepcopy
from dataclasses import replace

import pytest
from sqlglot.errors import OptimizeError

from test.evals.sql_projects.golden import GOLDEN_CASES, MULTI_CASES, PROPOSALS, SINGLE_CASES, Turn
from test.evals.sql_projects.scoring import ordered_output, physical_entities, score_turn as actual_score_turn
from test.evals.sql_projects.discriminators import PACK_ID, oracle_for


def response_for(expected):
    return {"proposal": {"sql": expected.oracle.sql, "parameters": expected.bindings(), "summary": "Synthetic scorer control", "checks": {"status": "PASS"}}}


def result_for(expected):
    return {"columns": list(expected.oracle.columns), "rows": [list(row) for row in expected.oracle.rows], "row_count": len(expected.oracle.rows)}


def score_turn(turn, response, *, result=None):
    controls = None
    if turn.query:
        oracle = oracle_for(turn.query.oracle)
        controls = {PACK_ID: {"columns": oracle.columns, "rows": oracle.rows, "row_count": len(oracle.rows)}}
    return actual_score_turn(turn, response, result=result, discriminator_results=controls)


def test_corpus_has_eighty_unique_cases_and_twenty_four_actual_dialogues():
    assert len(GOLDEN_CASES) == len({case.id for case in GOLDEN_CASES}) == 80
    assert len(SINGLE_CASES) == 56
    assert len(MULTI_CASES) == 24
    assert all(len(case.turns) >= 2 for case in MULTI_CASES)
    assert all(turn.text.strip() for case in GOLDEN_CASES for turn in case.turns)
    assert all(turn.review for case in GOLDEN_CASES for turn in case.turns if turn.kind != "PROPOSAL")


@pytest.mark.parametrize("case", GOLDEN_CASES, ids=lambda case: case.id)
def test_each_authored_turn_has_valid_independent_expectations(case):
    for turn in case.turns:
        if turn.kind != "PROPOSAL":
            assert turn.query is None
            continue
        assert physical_entities(turn.query.oracle.sql) == turn.query.entities
        assert score_turn(turn, response_for(turn.query), result=result_for(turn.query)) == []


@pytest.mark.parametrize("axis", ["kind", "guard", "summary", "parameters", "types", "duplicate_bind", "source", "rows", "columns", "row_count", "missing_result"])
def test_scorer_rejects_each_broken_acceptance_axis(axis):
    expected = PROPOSALS[0][3]
    turn = Turn("Порог50", "PROPOSAL", expected)
    response = deepcopy(response_for(expected))
    result = deepcopy(result_for(expected))
    if axis == "kind":
        response = {"response_kind": "EXPLANATION", "answer": "No edit"}
    elif axis == "guard":
        response["proposal"]["checks"]["status"] = "BLOCKED"
    elif axis == "summary":
        response["proposal"]["summary"] = " "
    elif axis == "parameters":
        response["proposal"]["parameters"][0]["value"] = "cancelled"
    elif axis == "types":
        response["proposal"]["parameters"][-1]["type"] = "text"
    elif axis == "duplicate_bind":
        response["proposal"]["parameters"].append(response["proposal"]["parameters"][0])
    elif axis == "source":
        response["proposal"]["sql"] = expected.oracle.sql.replace("public.orders", "public.payments")
    elif axis == "rows":
        result["rows"] = [[101], [101]]
    elif axis == "columns":
        result["columns"] = ["wrong"]
    elif axis == "row_count":
        result["row_count"] = True
    elif axis == "missing_result":
        result = None
    assert score_turn(turn, response, result=result)


@pytest.mark.parametrize("kind", ["EXPLANATION", "CLARIFICATION"])
def test_dialogue_cannot_hide_sql_mutation_or_empty_content(kind):
    turn = Turn("Объясни", kind, review="Human semantic review")
    response = {"response_kind": kind, "answer": "Valid", **({"questions": [{"id": "x", "text": "When?", "options": []}]} if kind == "CLARIFICATION" else {})}
    assert score_turn(turn, response) == []
    assert score_turn(turn, {**response, "sql": "DELETE FROM orders"})
    assert score_turn(turn, {**response, "answer": " "})


def test_equivalent_sql_spelling_and_new_bind_name_are_not_false_failures():
    expected = PROPOSALS[0][3]
    response = response_for(expected)
    response["proposal"]["sql"] = expected.oracle.sql.replace(":minimum", ":min_amount")
    response["proposal"]["parameters"][-1]["name"] = "min_amount"
    assert score_turn(Turn("Порог50", "PROPOSAL", expected), response, result=result_for(expected)) == []
    altered = replace(expected, entities=frozenset({"orders", "customers"}))
    assert score_turn(Turn("Порог50", "PROPOSAL", altered), response, result=result_for(expected))


def test_equivalent_bound_decimal_and_timezone_representation_do_not_fail_semantic_scoring():
    expected = PROPOSALS[0][3]
    response = response_for(expected)
    response["proposal"]["parameters"][-1]["value"] = "50.000"
    response["proposal"]["parameters"][1]["value"] = "2026-09-01T03:00:00+03:00"
    assert score_turn(Turn("Порог50", "PROPOSAL", expected), response, result=result_for(expected)) == []
    response["proposal"]["parameters"][1]["value"] = "2026-09-01T03:00:00"
    assert score_turn(Turn("Порог50", "PROPOSAL", expected), response, result=result_for(expected))


def test_equal_decimal_results_are_compared_numerically_without_rounding_or_float_conversion():
    expected = next(item[3] for item in PROPOSALS if item[0] == "order_sum")
    result = result_for(expected)
    result["rows"] = [["240.0000"]]
    turn = Turn("Сумма", "PROPOSAL", expected)
    assert score_turn(turn, response_for(expected), result=result) == []
    result["rows"] = [["240.00000000000000001"]]
    assert score_turn(turn, response_for(expected), result=result)


def test_missing_boundary_evidence_is_not_a_pass_even_with_matching_base_rows():
    expected = PROPOSALS[0][3]
    assert actual_score_turn(Turn("Порог", "PROPOSAL", expected), response_for(expected), result=result_for(expected)) == ["Independent boundary dataset result is missing"]


@pytest.mark.parametrize("case_id,before,after", [
    ("amount_min", "ORDER BY o.id", ""),
    ("amount_min", "ORDER BY o.id", "ORDER BY o.id DESC"),
    ("group_regions", "NULLS FIRST", "NULLS LAST"),
    ("window_rank", "ORDER BY o.amount DESC,o.id LIMIT", "ORDER BY o.amount DESC LIMIT"),
    ("item_grain", "ORDER BY o.id,i.id", "ORDER BY i.id,o.id"),
])
def test_identical_fixture_rows_do_not_hide_a_lost_order_contract(case_id, before, after):
    expected = next(item[3] for item in PROPOSALS if item[0] == case_id)
    response = response_for(expected)
    assert before in response["proposal"]["sql"]
    response["proposal"]["sql"] = response["proposal"]["sql"].replace(before, after)
    assert "Explicit output ordering differs" in score_turn(Turn("Сохрани порядок", "PROPOSAL", expected), response, result=result_for(expected))


@pytest.mark.parametrize("order", ["order_id ASC NULLS LAST", "1", "renamed.id", "id", "ID"])
def test_order_contract_accepts_equivalent_alias_ordinal_and_default_spelling(order):
    expected = PROPOSALS[0][3]
    response = response_for(expected)
    response["proposal"]["sql"] = expected.oracle.sql.replace("o.", "renamed.").replace("orders o ", "orders renamed ").replace("ORDER BY renamed.id", f"ORDER BY {order}")
    assert score_turn(Turn("Сохрани порядок", "PROPOSAL", expected), response, result=result_for(expected)) == []


def test_postgres_unquoted_case_is_normalized_but_quoted_identifiers_remain_distinct():
    expected = PROPOSALS[0][3]
    response = response_for(expected)
    response["proposal"]["sql"] = expected.oracle.sql.replace("public.orders", "PUBLIC.ORDERS").replace("order_id", "ORDER_ID").replace("ORDER BY o.id", "ORDER BY ORDER_ID")
    assert score_turn(Turn("Порог50", "PROPOSAL", expected), response, result=result_for(expected)) == []
    assert physical_entities('SELECT * FROM public."Orders"') == frozenset({"Orders"})


def test_output_alias_has_precedence_over_an_unqualified_source_column_in_order_by():
    assert ordered_output("SELECT o.id AS order_id, o.amount AS id FROM public.orders o ORDER BY id") == ((1, False, False),)
    assert ordered_output("SELECT o.id AS order_id, o.amount AS id FROM public.orders o ORDER BY o.id") == ((0, False, False),)


def test_ambiguous_unqualified_source_order_is_rejected_instead_of_guessed():
    with pytest.raises(OptimizeError, match="could not be resolved"):
        ordered_output("SELECT o.id AS order_id FROM public.orders o JOIN public.customers c ON c.id=o.customer_id ORDER BY id")


@pytest.mark.parametrize("order,descending,nulls_first", [
    ("c.region IS NULL DESC,c.region ASC", False, True),
    ("c.region IS NOT NULL ASC,c.region ASC", False, True),
    ("c.region IS NULL ASC,c.region DESC", True, False),
    ("c.region IS NOT NULL DESC,c.region DESC", True, False),
    ("c.region IS NULL DESC,region ASC", False, True),
    ("region IS NOT NULL ASC,region ASC", False, True),
])
def test_boolean_null_sort_prefix_is_equivalent_to_explicit_nulls_order(order, descending, nulls_first):
    sql = f"SELECT c.region AS region FROM public.customers c ORDER BY {order} LIMIT :row_limit"
    assert ordered_output(sql) == ((0, descending, nulls_first),)


def test_semantically_equivalent_null_prefix_passes_without_relaxing_null_direction():
    expected = next(item[3] for item in PROPOSALS if item[0] == "group_regions")
    response = response_for(expected)
    sql = response["proposal"]["sql"]
    response["proposal"]["sql"] = sql.replace("c.region NULLS FIRST", "c.region IS NULL DESC,c.region ASC")
    turn = Turn("NULL first", "PROPOSAL", expected)
    assert score_turn(turn, response, result=result_for(expected)) == []
    response["proposal"]["sql"] = sql.replace("c.region NULLS FIRST", "c.region IS NULL ASC,c.region ASC")
    assert "Explicit output ordering differs" in score_turn(turn, response, result=result_for(expected))


@pytest.mark.parametrize("order", ["c.region IS NULL DESC", "c.region IS NULL DESC,c.id"])
def test_unpaired_or_different_null_sort_key_is_not_treated_as_region_order(order):
    with pytest.raises(ValueError, match="ORDER BY"):
        ordered_output(f"SELECT c.region AS region FROM public.customers c ORDER BY {order} LIMIT :row_limit")


def test_projected_boolean_sort_expression_remains_a_valid_output_order():
    assert ordered_output("SELECT c.region IS NULL AS missing FROM public.customers c ORDER BY c.region IS NULL DESC") == ((0, True, True),)
