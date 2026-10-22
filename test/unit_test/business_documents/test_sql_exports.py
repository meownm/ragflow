"""Export values preserve their stored type and precision without buffering rows."""

from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import json
import tracemalloc
import xml.etree.ElementTree as ET

import pytest

from business_documents.sql_query.exports import SqlExportError, graph_svg, json_chunks, parse_export_command, public_graph, result_json_chunks
from business_documents.sql_query.query_graph import project_query_graph, related_catalog, relationship_paths
from test.evals.sql_projects.golden import BASE
from test.evals.sql_projects.schema import accepted_schema


@pytest.mark.parametrize(
    "changes",
    [
        {"schema_version": "2"},
        {"kind": "DUMP"},
        {"format": "html"},
        {"password": "never-export"},
        {"compilation_id": ""},
        {"query_artifact_id": "../secret"},
        {"run_id": "not-a-query-field"},
    ],
)
def test_export_contract_is_closed_and_versioned(changes):
    raw = {"schema_version": "1", "kind": "QUERY", "format": "json", "compilation_id": "compilation", "query_artifact_id": "query", **changes}
    with pytest.raises(SqlExportError):
        parse_export_command(raw)


def test_graph_view_retains_mode_depth_and_bounded_positions():
    raw = {
        "schema_version": "1",
        "kind": "GRAPH",
        "format": "svg",
        "compilation_id": "compilation",
        "query_artifact_id": "query",
        "proposal_id": "proposal",
        "view": {"positions": {"orders": {"x": -10, "y": 20}}, "viewport": {"x": 0, "y": 1, "zoom": 0.5}, "mode": "query", "depth": 3, "list_view": False},
    }
    assert parse_export_command(raw).view == raw["view"]
    assert parse_export_command(raw).proposal_id == "proposal"
    raw["view"]["positions"]["orders"]["x"] = float("inf")
    with pytest.raises(SqlExportError):
        parse_export_command(raw)


def test_result_export_distinguishes_null_empty_zero_boolean_and_exact_numeric_timestamp_values():
    moment = datetime(2026, 10, 21, 12, 30, 0, 123456, timezone.utc)
    row = [None, "", 0, False, 9223372036854775807, Decimal("12345678901234567890.00100"), moment, "0.00100", "2026-10-21T12:30:00+00:00"]
    oids = [{"postgres_oid": oid} for oid in (25, 25, 23, 16, 20, 1700, 1184, 1700, 1184)]
    result = json.loads(b"".join(result_json_chunks({"format": "ragflow-sql-result-export"}, [str(i) for i in range(len(row))], [row], oids)))
    assert result["rows"][0] == [
        {"type": "null", "value": None},
        {"type": "string", "value": ""},
        {"type": "integer", "value": "0"},
        {"type": "boolean", "value": False},
        {"type": "integer", "value": "9223372036854775807"},
        {"type": "decimal", "value": "12345678901234567890.00100"},
        {"type": "timestamp_with_timezone", "value": moment.isoformat()},
        {"type": "decimal", "value": "0.00100"},
        {"type": "timestamp_with_timezone", "value": "2026-10-21T12:30:00+00:00"},
    ]
    assert result["postgres_types_known"] is True


def test_old_result_type_metadata_is_reported_unknown_instead_of_invented():
    result = json.loads(b"".join(result_json_chunks({}, ["value"], [["0.00100"]])))
    assert result["postgres_types_known"] is False
    assert result["columns"] == [{"name": "value", "postgres_oid": None}]
    assert result["rows"] == [[{"type": "string", "value": "0.00100"}]]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), Decimal("NaN"), {"nested": [float("nan")]}, object()])
def test_invalid_cells_fail_before_the_download_iterator_is_returned(value):
    with pytest.raises(SqlExportError):
        result_json_chunks({}, ["value"], [[value]])


def test_query_json_preserves_original_parameter_values_and_unicode():
    packet = {"sql": 'SELECT o.id AS "Заказ" FROM public.orders o LIMIT :row_limit', "parameters": [{"name": "row_limit", "type": "integer", "value": 9223372036854775807}]}
    before = deepcopy(packet)
    raw = b"".join(json_chunks(packet))
    assert json.loads(raw) == packet == before
    assert b"9223372036854775807" in raw


def test_large_result_is_serialized_incrementally_without_a_second_full_table(tmp_path):
    rows = [[number, f"{number:020d}" + "x" * 480] for number in range(20_000)]
    tracemalloc.start()
    size = sum(len(chunk) for chunk in result_json_chunks({}, ["id", "text"], rows))
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    metrics = {"rows": len(rows), "output_bytes": size, "peak_extra_bytes": peak}
    (tmp_path / "streaming-export-metrics.json").write_text(json.dumps(metrics), encoding="utf-8")
    assert size > 10_000_000
    assert peak < 1_000_000


def graph_fixture():
    tables = [item["selected_table"] for item in accepted_schema()["schema_snapshot"]["requirements"]]
    neighborhood = related_catalog(tables, ["orders"], depth=1)
    return {
        **project_query_graph(BASE.oracle.sql, neighborhood["tables"]),
        **neighborhood,
        **relationship_paths(neighborhood["relationships"], ["orders"], max_hops=1),
        "freshness": {"stale": False, "threshold_hours": 24},
        "warnings": [],
    }


def test_graph_exports_only_visible_mode_data_and_known_public_fields():
    raw = graph_fixture()
    raw["credentials"] = {"password": "never-export"}
    raw["nodes"][0]["columns"][0]["password"] = "never-export"
    raw["freshness"]["api_key"] = "never-export"
    if raw["relationships"]:
        raw["relationships"][0]["column_pairs"][0]["password"] = "never-export"
    graph = public_graph(raw, only_query=True)
    assert graph["nodes"] and all(node["membership"] == "QUERY" for node in graph["nodes"])
    assert "never-export" not in json.dumps(graph)
    visible = {node["id"] for node in graph["nodes"]}
    assert all(edge["from"] in visible and edge["to"] in visible for edge in graph["edges"])


def test_svg_is_standalone_safe_xml_with_membership_legend_sources_and_relationships():
    graph = public_graph(graph_fixture(), only_query=False)
    graph["nodes"][0]["label"] = '</text><script onload="alert(1)">bad</script>\x00'
    packet = {
        "source": {"project_id": "project", "query": {"id": "query", "revision": 4}, "compilation": {"id": "compilation"}, "schema": {"id": "schema"}},
        "view": {"positions": {}, "mode": "all", "depth": 1},
        "exported_at": "2026-10-21T00:00:00Z",
        "graphs": [{"label": "Проверенная версия", "graph": graph}, {"label": "Предложение — ещё не принято", "graph": graph}],
    }
    raw = graph_svg(packet)
    root = ET.fromstring(raw)
    ns = {"svg": "http://www.w3.org/2000/svg"}
    assert root.tag == "{http://www.w3.org/2000/svg}svg"
    assert not root.findall(".//svg:script", ns) and not root.findall(".//svg:foreignObject", ns)
    assert all(not key.lower().startswith("on") and "href" not in key for element in root.iter() for key in element.attrib)
    assert {node.attrib["data-membership"] for node in root.iter() if "data-membership" in node.attrib} == {"QUERY", "RELATED"}
    text = " ".join(root.itertext())
    assert "Проверенная версия" in text and "ещё не принято" in text and "Связи каталога" in text
    assert "compilation" in text and "Связанная таблица вне запроса" in text
    metadata = json.loads(root.find("svg:metadata", ns).text)
    assert metadata["source"] == packet["source"]
    assert metadata["graphs"][0]["graph"]["relationships"] == graph["relationships"]
