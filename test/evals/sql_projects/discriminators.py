"""Independently authored second dataset rejects accidental fixture equivalence."""

from dataclasses import replace

from test.evals.sql_projects.corpus import SqlOracle

PACK_ID = "threshold-boundaries"
SETUP_SQL = """INSERT INTO orders VALUES
 (108,2,1,'2026-09-15T00:00:00Z','paid',50.00),
 (109,2,1,'2026-09-16T00:00:00Z','paid',50.00),
 (110,NULL,NULL,'2026-09-17T00:00:00Z','paid',150.00),
 (201,3,3,'2026-10-15T00:00:00Z','cancelled',75.00),
 (202,3,3,'2027-01-15T00:00:00Z','cancelled',80.00);"""

BASE_IDS = ((101,), (102,), (105,), (107,), (108,), (109,), (110,))
ROWS = {
    "baseline": BASE_IDS,
    "amount_min": ((101,), (102,), (108,), (109,), (110,)),
    "amount_max": ((105,), (107,)),
    "remove_status": ((101,), (102,), (104,), (105,), (107,), (108,), (109,), (110,)),
    "remove_period": ((101,), (102,), (103,), (105,), (106,), (107,), (108,), (109,), (110,)),
    "october": ((103,),),
    "empty_period": (),
    "exclusive_start": ((102,), (105,), (107,), (108,), (109,), (110,)),
    "inclusive_end": ((101,), (102,), (103,), (105,), (107,), (108,), (109,), (110,)),
    "cancelled": ((104,),),
    "list_include": ((101,), (105,)),
    "list_exclude": ((105,), (107,), (108,), (109,), (110,)),
    "customer_null": ((105,), (110,)),
    "customer_not_null": ((101,), (102,), (107,), (108,), (109,)),
    "region_north": ((101,), (102,)),
    "region_empty": ((107,),),
    "region_null": ((105,), (110,)),
    "nested_or": ((101,), (102,), (105,), (107,)),
    "nested_not": ((107,), (108,), (109,)),
    "left_projection": ((101, "North"), (102, "North"), (105, None), (107, ""), (108, "South"), (109, "South"), (110, None)),
    "inner_projection": ((101, "North"), (102, "North"), (107, ""), (108, "South"), (109, "South")),
    "payer_key": ((101,), (105,)),
    "two_roles": ((101, "North", "South"), (102, "North", "North"), (105, None, "South"), (107, "", ""), (108, "South", "North"), (109, "South", "North"), (110, None, None)),
    "order_count": ((7,),),
    "order_sum": (("490.00",),),
    "order_avg": (("70.0000000000000000",),),
    "group_regions": ((None, "190.00"), ("", "0.00"), ("North", "200.00"), ("South", "100.00")),
    "having_total": ((None, "190.00"), ("North", "200.00"), ("South", "100.00")),
    "where_before_group": ((None, "150.00"), ("North", "200.00"), ("South", "100.00")),
    "payments_exists": ((101,), (102,)),
    "payments_absent": ((105,), (107,), (108,), (109,), (110,)),
    "items_preaggregate": ((101, "100.00"), (102, "100.00"), (105, "40.00"), (107, "0.00"), (108, None), (109, None), (110, None)),
    "retail_path": ((101,), (102,), (107,)),
    "service_path": ((105,),),
    "item_grain": ((101, 1001), (101, 1002), (102, 1003), (105, 1006), (107, 1008)),
    "manager_self_join": ((101, "Анна", "Руководитель"), (102, "Анна", "Руководитель"), (105, None, None), (107, None, None), (108, "Борис", "Руководитель"), (109, "Борис", "Руководитель"), (110, None, None)),
    "descending": ((110,), (109,), (108,), (107,), (105,), (102,), (101,)),
    "limit_two": ((101,), (102,)),
    "offset_two": ((105,), (107,), (108,), (109,), (110,)),
    "window_rank": ((110, "150.00", 1), (101, "100.00", 2), (102, "100.00", 3), (108, "50.00", 4), (109, "50.00", 5), (105, "40.00", 6), (107, "0.00", 7)),
    "cte_orders": BASE_IDS,
}


def oracle_for(oracle: SqlOracle):
    return replace(oracle, rows=ROWS[oracle.id])
