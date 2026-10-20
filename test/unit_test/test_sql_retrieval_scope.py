import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from common.sql_scope import retrieval_source_sql, scope_retrieval_sql

CASES = json.loads((Path(__file__).parents[1] / "fixtures/sql_retrieval_scope.json").read_text())
ALLOWED = "11111111-1111-4111-8111-111111111111"
FOREIGN = "22222222-2222-4222-8222-222222222222"


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
def test_scope_executes_only_authorized_rows(case):
    args = (case["sql"], case.get("table", "ragflow_tenant"), case.get("engine", "es"), case.get("kb_ids", [ALLOWED]))
    if case.get("reject"):
        with pytest.raises(ValueError):
            scope_retrieval_sql(*args)
        return
    query = scope_retrieval_sql(*args)
    with closing(sqlite3.connect(":memory:")) as db:
        db.create_function("json_extract_string", 2, lambda data, _path: json.loads(data)["Name"])
        db.execute("CREATE TABLE ragflow_tenant(doc_id TEXT, kb_id TEXT, amount INT, docnm TEXT, chunk_data TEXT)")
        db.executemany("INSERT INTO ragflow_tenant VALUES (?,?,?,?,?)", [("allowed", ALLOWED, 1, "Desk", '{"Name":"Desk"}'), ("private", FOREIGN, 2, "Private", '{"Name":"Private"}')])
        db.execute("CREATE TABLE ragflow_tenant_11111111111141118111111111111111 AS SELECT * FROM ragflow_tenant WHERE amount=1")
        assert [str(row[0]) for row in db.execute(query)] == case["rows"]


@pytest.mark.parametrize("tail", ["", " WHERE t.amount=1 OR t.amount=2", " WHERE t.docnm_kwd='group by order by limit' OR t.amount=1 GROUP BY t.kb_id HAVING COUNT(*)>0"])
def test_citations_preserve_alias_and_whole_predicate(tail):
    query = retrieval_source_sql("SELECT COUNT(*) FROM ragflow_tenant AS t" + tail, "ragflow_tenant", "es", [ALLOWED])
    with closing(sqlite3.connect(":memory:")) as db:
        db.execute("CREATE TABLE ragflow_tenant(doc_id TEXT, docnm_kwd TEXT, kb_id TEXT, amount INT)")
        db.executemany("INSERT INTO ragflow_tenant VALUES (?,?,?,?)", [("allowed", "Desk", ALLOWED, 1), ("private", "Private", FOREIGN, 2)])
        assert db.execute(query).fetchall() == [("allowed", "Desk")]
