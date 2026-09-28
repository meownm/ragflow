from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType

import pytest

if "api.apps" not in sys.modules:
    api_apps = ModuleType("api.apps")
    api_apps.__path__ = [str(Path(__file__).resolve().parents[5] / "api" / "apps")]
    sys.modules["api.apps"] = api_apps

from api.apps.business_documents import sql_query_python
from business_documents.application.errors import BusinessDocumentError


class Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def test_python_adapter_requires_private_runsc_capability(monkeypatch):
    monkeypatch.setattr(
        sql_query_python.requests,
        "get",
        lambda *args, **kwargs: Response(
            {
                "private_no_network": True,
                "private_content_logging": False,
                "private_runtime": "runc",
            }
        ),
    )
    monkeypatch.setattr(sql_query_python.requests, "post", lambda *args, **kwargs: pytest.fail("Sandbox must not receive private data"))
    with pytest.raises(BusinessDocumentError) as error:
        sql_query_python.execute_result_python("def main(columns, rows): return {}", ["value"], [[42]])
    assert error.value.code == "SQL_PYTHON_ISOLATION_UNAVAILABLE"


def test_python_adapter_sends_only_result_copy_and_private_flag(monkeypatch):
    monkeypatch.setattr(
        sql_query_python.requests,
        "get",
        lambda *args, **kwargs: Response(
            {
                "private_no_network": True,
                "private_content_logging": False,
                "private_runtime": "runsc",
            }
        ),
    )
    posted = []

    def post(url, *, json, timeout):
        posted.append(json)
        return Response(
            {
                "status": "success",
                "exit_code": 0,
                "result": {
                    "present": True,
                    "value": {"columns": ["value"], "rows": [[42]]},
                },
            }
        )

    monkeypatch.setattr(sql_query_python.requests, "post", post)
    result = sql_query_python.execute_result_python("def main(columns, rows): return {}", ["value"], [[42]])
    assert result["rows"] == [[42]]
    assert posted[0]["private"] is True
    assert posted[0]["arguments"] == {"columns": ["value"], "rows": [[42]]}
