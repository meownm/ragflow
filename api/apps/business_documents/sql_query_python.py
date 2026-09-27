"""Private Python sandbox adapter for a verified SQL result."""

from __future__ import annotations

import base64
import os
from typing import Any

import requests

from api import settings
from business_documents.application.errors import BusinessDocumentError


def execute_result_python(code: str, columns: list[str], rows: list[list[Any]]) -> dict[str, Any]:
    """Pass only a result copy to a disposable no-network sandbox."""

    try:
        base_url = f"http://{getattr(settings, 'SANDBOX_HOST', os.getenv('SANDBOX_HOST', 'sandbox'))}:9385"
        capability = requests.get(f"{base_url}/capabilities", timeout=5)
        capability.raise_for_status()
        if capability.json() != {"private_no_network": True, "private_content_logging": False, "private_runtime": "runsc"}:
            raise BusinessDocumentError("SQL_PYTHON_ISOLATION_UNAVAILABLE", "Private Python isolation is unavailable", 503)
        response = requests.post(
            f"{base_url}/run",
            json={
                "code_b64": base64.b64encode(code.encode("utf-8")).decode("ascii"),
                "language": "python",
                "arguments": {"columns": columns, "rows": rows},
                "private": True,
            },
            timeout=45,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise BusinessDocumentError("SQL_PYTHON_UNAVAILABLE", "Isolated Python service is unavailable", 503) from exc
    if payload.get("status") != "success" or payload.get("exit_code") != 0:
        raise BusinessDocumentError("SQL_PYTHON_FAILED", "Python transformation failed in the isolated sandbox", 422)
    result = payload.get("result")
    if not isinstance(result, dict) or result.get("present") is not True or not isinstance(result.get("value"), dict):
        raise BusinessDocumentError("SQL_PYTHON_RESULT_INVALID", "Python must return a table with columns and rows", 422)
    return result["value"]
