"""Frozen SQL counterexamples with independently authored result oracles."""

from dataclasses import dataclass
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation


@dataclass(frozen=True)
class SqlOracle:
    id: str
    sql: str
    columns: tuple[str, ...]
    rows: tuple[tuple, ...]
    types: tuple[str, ...] = ()


PARAMETERS = {"paid": "paid", "date_from": "2026-09-01T00:00:00Z", "date_to": "2026-10-01T00:00:00Z", "row_limit": 100}
PERIOD = "o.status = :paid AND o.created_at >= :date_from AND o.created_at < :date_to"

SQL_ORACLES = (
    SqlOracle("base_order_ids", f"SELECT o.id AS order_id FROM public.orders o WHERE {PERIOD} ORDER BY o.id LIMIT :row_limit", ("order_id",), ((101,), (102,), (105,), (107,))),
    SqlOracle("base_sum", f"SELECT SUM(o.amount) AS total FROM public.orders o WHERE {PERIOD} LIMIT :row_limit", ("total",), (("240.00",),)),
    SqlOracle("left_customer", f"SELECT o.id AS order_id FROM public.orders o LEFT JOIN public.customers c ON o.customer_id=c.id WHERE {PERIOD} ORDER BY o.id LIMIT :row_limit", ("order_id",), ((101,), (102,), (105,), (107,))),
    SqlOracle("inner_customer", f"SELECT o.id AS order_id FROM public.orders o JOIN public.customers c ON o.customer_id=c.id WHERE {PERIOD} ORDER BY o.id LIMIT :row_limit", ("order_id",), ((101,), (102,), (107,))),
    SqlOracle("no_payments_exists", f"SELECT o.id AS order_id FROM public.orders o WHERE {PERIOD} AND NOT EXISTS(SELECT 1 FROM public.payments p WHERE p.order_id=o.id) ORDER BY o.id LIMIT :row_limit", ("order_id",), ((105,), (107,))),
    SqlOracle("not_in_null_counterexample", f"SELECT o.id AS order_id FROM public.orders o WHERE {PERIOD} AND o.id NOT IN(SELECT p.order_id FROM public.payments p) ORDER BY o.id LIMIT :row_limit", ("order_id",), ()),
    SqlOracle("item_fanout_counterexample", f"SELECT SUM(o.amount) AS total FROM public.orders o LEFT JOIN public.order_items i ON i.order_id=o.id WHERE {PERIOD} LIMIT :row_limit", ("total",), (("340.00",),)),
    SqlOracle("distinct_sum_counterexample", f"SELECT SUM(DISTINCT o.amount) AS total FROM public.orders o WHERE {PERIOD} LIMIT :row_limit", ("total",), (("140.00",),)),
    SqlOracle("two_fanouts_counterexample", f"SELECT SUM(o.amount) AS total FROM public.orders o LEFT JOIN public.order_items i ON i.order_id=o.id LEFT JOIN public.payments p ON p.order_id=o.id WHERE {PERIOD} LIMIT :row_limit", ("total",), (("540.00",),)),
    SqlOracle("region_null_empty", f"SELECT c.region AS region, SUM(o.amount) AS total FROM public.orders o LEFT JOIN public.customers c ON c.id=o.customer_id WHERE {PERIOD} GROUP BY c.region ORDER BY c.region NULLS FIRST LIMIT :row_limit", ("region", "total"), ((None, "40.00"), ("", "0.00"), ("North", "200.00"))),
    SqlOracle("having", f"SELECT c.region AS region, SUM(o.amount) AS total FROM public.orders o LEFT JOIN public.customers c ON c.id=o.customer_id WHERE {PERIOD} GROUP BY c.region HAVING SUM(o.amount)>=150 ORDER BY c.region LIMIT :row_limit", ("region", "total"), (("North", "200.00"),)),
    SqlOracle("retail_preserves_order_grain", f"SELECT o.id AS order_id FROM public.orders o WHERE {PERIOD} AND EXISTS(SELECT 1 FROM public.order_items i JOIN public.products p ON p.id=i.product_id JOIN public.categories c ON c.id=p.category_id WHERE i.order_id=o.id AND c.name='Retail') ORDER BY o.id LIMIT :row_limit", ("order_id",), ((101,), (102,), (107,))),
)


def query_parameters(sql):
    # These are fixed trusted fixture queries. Production parsing remains owned
    # by the compiler/guard and executor, not this independent result oracle.
    return {name: value for name, value in PARAMETERS.items() if f":{name}" in sql}


def result_errors(result, oracle):
    if not isinstance(result, Mapping):
        return ["Result is not an object"]
    errors = []
    if not isinstance(result.get("columns"), (list, tuple)) or tuple(result["columns"]) != oracle.columns:
        errors.append("Output columns differ")
    rows = result.get("rows")
    if not isinstance(rows, (list, tuple)) or any(not isinstance(row, (list, tuple)) for row in rows) or len(rows) != len(oracle.rows) or any(
        len(actual) != len(expected) or any(not _equal_value(value, target, oracle.types[index] if oracle.types else "value") for index, (value, target) in enumerate(zip(actual, expected)))
        for actual, expected in zip(rows, oracle.rows)
    ):
        errors.append("Ordered result values or duplicate multiplicities differ")
    if type(result.get("row_count")) is not int or result["row_count"] != len(oracle.rows):
        errors.append("Row count differs")
    return errors


def _equal_value(value, expected, kind):
    if expected is None:
        return value is None
    if kind == "decimal":
        if not isinstance(value, (str, int, Decimal)) or isinstance(value, bool):
            return False
        try:
            actual, target = Decimal(value), Decimal(expected)
            return actual.is_finite() and actual == target
        except (InvalidOperation, TypeError, ValueError):
            return False
    return type(value) is type(expected) and value == expected
