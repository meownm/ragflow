"""On-demand tenant-model proposal over a bounded verified SQL result."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from json_repair import repair_json

from api.apps.business_documents.ai import RAGFlowLLMAdapter
from api.apps.business_documents.assets import prompt_descriptor, prompt_text
from business_documents.application.errors import BusinessDocumentError
from business_documents.sql_query.conclusion import ConclusionValidationError, validate_conclusion


def propose_conclusion(
    tenant_id: str,
    question: str,
    columns: list[str],
    rows: list[list[Any]],
    row_count: int,
    completeness: str,
) -> dict[str, Any]:
    """Return a checked draft without persisting or logging result rows."""

    limited_columns = columns[:12]
    limited_rows = [row[: len(limited_columns)] for row in rows[:25]]
    context = {
        "question": question[:2000],
        "columns": limited_columns,
        "rows": limited_rows,
        "row_count": row_count,
        "completeness": completeness,
    }
    if len(json.dumps(context, ensure_ascii=False, default=str).encode("utf-8")) > 16_000:
        raise BusinessDocumentError("SQL_CONCLUSION_INPUT_TOO_LARGE", "Result preview is too large for the model", 422)
    try:
        raw = asyncio.run(asyncio.wait_for(
            RAGFlowLLMAdapter().async_generate(
                tenant_id,
                prompt_text("sql_conclusion"),
                {"prompt": prompt_descriptor("PROPOSE_SQL_CONCLUSION"),
                 "job_input": {"task_type": "PROPOSE_SQL_CONCLUSION"}, "context": context},
            ),
            timeout=60,
        ))
        proposal = raw if isinstance(raw, dict) else repair_json(raw, return_objects=True)
        if not isinstance(proposal, dict) or set(proposal) != {"text", "citations"}:
            raise ConclusionValidationError("Model response has an invalid structure")
        checked = validate_conclusion(proposal["text"], proposal["citations"], limited_columns, limited_rows, row_count)
    except ConclusionValidationError as exc:
        raise BusinessDocumentError("SQL_CONCLUSION_CHECK_FAILED", str(exc), 422) from exc
    except Exception as exc:
        raise BusinessDocumentError("SQL_CONCLUSION_UNAVAILABLE", "Conclusion model is unavailable", 503) from exc
    if completeness == "LIMITED" and not any(word in checked["text"].casefold() for word in ("непол", "огранич", "част")):
        raise BusinessDocumentError("SQL_CONCLUSION_CHECK_FAILED", "Conclusion must disclose that the result is limited", 422)
    return checked
