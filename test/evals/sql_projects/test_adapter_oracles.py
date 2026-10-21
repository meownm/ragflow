"""Known responses through the real parser/guard; not a model-quality run."""

from pathlib import Path
import sys
from types import ModuleType

import pytest

if "api.apps" not in sys.modules:
    api_apps = ModuleType("api.apps")
    api_apps.__path__ = [str(Path(__file__).resolve().parents[3] / "api/apps")]
    sys.modules["api.apps"] = api_apps

from api.apps.business_documents.sql_query_revision_ai import propose_revision
from test.evals.sql_projects.golden import BASE, PROPOSALS
from test.evals.sql_projects.schema import accepted_requirements, accepted_schema


@pytest.mark.asyncio
@pytest.mark.parametrize("item", PROPOSALS, ids=lambda item: item[0])
async def test_authored_oracle_is_accepted_by_actual_revision_contract(item):
    name, _, text, expected = item

    class ModelControl:
        async def async_generate(self, tenant_id, system, payload):
            assert payload["context"]["discussion"][0]["text"] == text
            return {"sql": expected.oracle.sql, "parameters": expected.bindings(), "summary": text}

    result = await propose_revision("synthetic-control", {"target": "QUERY", "locale": "ru", "sql": BASE.oracle.sql, "parameters": BASE.bindings(),
        "requirements": accepted_requirements(), "schema": accepted_schema(), "comments": [{"text": text}]}, llm=ModelControl())
    assert result["proposal"]["checks"]["status"] == "PASS", {"case": name, "checks": result["proposal"]["checks"]}
