"""Accepted-query comparisons: changes stay possible and require explicit review."""

from copy import deepcopy

import pytest

from business_documents.sql_query.revisions import revision_review_changes, selected_group

SQL = "SELECT o.status AS status, COUNT(o.id) AS n, AVG(o.amount) AS mean FROM public.orders AS o WHERE o.customer_id = :customer GROUP BY o.status ORDER BY n DESC LIMIT :row_limit"
PARAMS = [{"name": "customer", "type": "integer", "value": 42}, {"name": "row_limit", "type": "integer", "value": 100}]
MONTHLY = SQL.replace("SELECT ", "SELECT DATE_TRUNC('month', o.created_at) AS month, ").replace("GROUP BY o.status", "GROUP BY o.status, DATE_TRUNC('month', o.created_at)")


def test_additive_month_group_filter_and_measures_keep_automatic_application():
    proposal = {"sql": MONTHLY.replace("o.customer_id = :customer", "o.status = :status AND (o.customer_id = :customer)"), "parameters": [*PARAMS, {"name": "status", "type": "text", "value": "paid"}]}
    before = deepcopy(proposal)
    assert revision_review_changes(SQL, PARAMS, proposal, selected_group(SQL, 0, "paid")) == []
    assert proposal == before


@pytest.mark.parametrize(
    ("old", "new", "change"),
    [
        ("WHERE o.customer_id = :customer ", "", "прежний отбор строк"),
        ("COUNT(o.id)", "COUNT(DISTINCT o.id)", "принятые показатели"),
        ("AVG(o.amount)", "SUM(o.amount)", "принятые показатели"),
        ("ORDER BY n DESC", "ORDER BY n ASC", "прежняя сортировка"),
        ("public.orders", "public.archive_orders", "источники или соединения"),
    ],
)
def test_impactful_changes_require_application_without_being_prohibited(old, new, change):
    assert change in revision_review_changes(SQL, PARAMS, {"sql": MONTHLY.replace(old, new), "parameters": PARAMS})


def test_changed_binding_requires_review_even_when_sql_is_identical():
    parameters = deepcopy(PARAMS)
    parameters[0]["value"] = 43
    assert revision_review_changes(SQL, PARAMS, {"sql": SQL, "parameters": parameters}) == ["прежний отбор строк"]


def test_missing_selected_cohort_cannot_auto_apply_a_valid_monthly_query():
    assert revision_review_changes(SQL, PARAMS, {"sql": MONTHLY, "parameters": PARAMS}, selected_group(SQL, 0, "paid")) == ["отбор выбранной группы"]


def test_unrequested_extra_filter_is_reviewed_instead_of_silently_narrowing_cohort():
    proposal = {"sql": MONTHLY.replace("WHERE ", "WHERE o.amount > 1000 AND "), "parameters": PARAMS}
    assert revision_review_changes(SQL, PARAMS, proposal) == ["прежний отбор строк"]


def test_null_selected_group_uses_is_null_and_constant_group_can_be_removed():
    proposal = {"sql": MONTHLY.replace("WHERE ", "WHERE o.status IS NULL AND ").replace("o.status, DATE_TRUNC", "DATE_TRUNC").replace("o.status AS status, ", ""), "parameters": PARAMS}
    assert revision_review_changes(SQL, PARAMS, proposal, selected_group(SQL, 0, None)) == []


def test_reordered_and_terms_and_commuted_equality_preserve_cohort():
    sql = MONTHLY.replace("o.customer_id = :customer", ":customer = o.customer_id")
    assert revision_review_changes(SQL, PARAMS, {"sql": sql, "parameters": PARAMS}) == []


def test_output_alias_rename_does_not_hide_changed_sort_direction():
    sql = MONTHLY.replace("AS n,", "AS count_rows,").replace("ORDER BY n DESC", "ORDER BY count_rows DESC")
    assert revision_review_changes(SQL, PARAMS, {"sql": sql, "parameters": PARAMS}) == []
    assert revision_review_changes(SQL, PARAMS, {"sql": sql.replace("count_rows DESC", "count_rows ASC"), "parameters": PARAMS}) == ["прежняя сортировка"]


def test_positional_order_tracks_expression_after_new_column_is_inserted():
    before = SQL.replace("ORDER BY n DESC", "ORDER BY 2 DESC")
    after = MONTHLY.replace("ORDER BY n DESC", "ORDER BY 2 DESC")
    assert revision_review_changes(before, PARAMS, {"sql": after, "parameters": PARAMS}) == ["прежняя сортировка"]


def test_added_having_cannot_silently_remove_monthly_buckets():
    sql = MONTHLY.replace("ORDER BY", "HAVING COUNT(o.id) > 1 ORDER BY")
    assert revision_review_changes(SQL, PARAMS, {"sql": sql, "parameters": PARAMS}) == ["отбор сгруппированных строк"]


def test_removed_nonaggregate_output_is_reviewed():
    sql = MONTHLY.replace("o.status AS status, ", "")
    assert revision_review_changes(SQL, PARAMS, {"sql": sql, "parameters": PARAMS}) == ["принятые показатели"]


def test_new_sort_tiebreaker_keeps_accepted_primary_order():
    sql = MONTHLY.replace("ORDER BY n DESC", "ORDER BY n DESC, month ASC")
    assert revision_review_changes(SQL, PARAMS, {"sql": sql, "parameters": PARAMS}) == []


def test_removed_constant_sort_key_does_not_hide_changed_remaining_order():
    before = SQL.replace("ORDER BY n DESC", "ORDER BY status ASC, n DESC")
    after = MONTHLY.replace("WHERE ", "WHERE o.status = :status AND ").replace("ORDER BY n DESC", "ORDER BY n ASC")
    proposal = {"sql": after, "parameters": [*PARAMS, {"name": "status", "type": "text", "value": "paid"}]}
    assert revision_review_changes(before, PARAMS, proposal, selected_group(SQL, 0, "paid")) == ["прежняя сортировка"]
