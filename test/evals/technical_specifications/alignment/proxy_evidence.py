"""Read only this run's known proxy transactions; never list shared traffic."""

import re
import time
from urllib.request import urlopen

from api.technical_specifications.alignment import fingerprint, materialize_report, report_format, strict_json
from api.technical_specifications.domain import SpecificationError

MAX_WIRE_BYTES = 4_000_000


def collect(report, base_url, fetch=None):
    deadline = time.monotonic() + 60

    def read(path):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Proxy evidence deadline")
        with urlopen(base_url.rstrip("/") + path, timeout=min(10, remaining)) as response:
            parts, size = [], 0
            while True:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Proxy evidence deadline")
                chunk = response.read1(16384)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_WIRE_BYTES:
                    raise ValueError("Proxy evidence exceeds the bounded size")
                parts.append(chunk)
        return strict_json(b"".join(parts).decode("utf-8"))

    fetch = fetch or read
    checks = []
    for case in report.get("cases", []):
        result = case.get("result", {})
        if (
            case.get("case_id") == "ADMISSION-CONTEXT-LOW"
            and case.get("evidence_kind") == "DETERMINISTIC"
            and type(case.get("inference_calls")) is int
            and case["inference_calls"] == 0
            and result.get("reason_code") == "ALIGNMENT_CONTEXT_EXCEEDED"
        ):
            checks.append({"case_id": case["case_id"], "status": "NOT_APPLICABLE", "reason_code": "NO_INFERENCE_EXPECTED"})
            continue
        audit = result.get("audit", {})
        request_id = audit.get("request_id")
        if not isinstance(request_id, str) or not re.fullmatch(r"[a-f0-9]{32}", request_id):
            checks.append({"case_id": case["case_id"], "status": "INCOMPLETE", "reason_code": "PROXY_REQUEST_ID_MISSING"})
            continue
        try:
            path = "/_proxy/admin/transactions/" + request_id
            transaction = fetch(path)
            request = fetch(path + "/request-body")
            response = fetch(path + "/response-body")
            matches = (
                transaction.get("request_id") == request_id
                and transaction.get("status") == "done"
                and transaction.get("dedup_hit") is False
                and transaction.get("path") == "/api/chat"
                and transaction.get("method") == "POST"
                and transaction.get("response_status_code") == 200
                and fingerprint(request) == audit.get("wire_sha256")
                and type(response.get("prompt_eval_count")) is int
                and type(response.get("eval_count")) is int
                and response.get("model") == audit.get("model")
                and response.get("done") is True
                and response.get("done_reason") == audit.get("finish_reason")
                and response.get("prompt_eval_count") == audit.get("prompt_tokens")
                and response.get("eval_count") == audit.get("completion_tokens")
            )
            if result.get("report_status") == "COMPLETE":
                model_report = strict_json(response["message"]["content"])
                payload = strict_json(request["messages"][1]["content"])
                matches = (
                    matches and request.get("format") == report_format(payload) and model_report == result.get("model_report") and materialize_report(model_report, payload) == result.get("report")
                )
            checks.append(
                {
                    "case_id": case["case_id"],
                    "request_id": request_id,
                    "status": "VERIFIED" if matches else "INCOMPLETE",
                    "reason_code": None if matches else "PROXY_EVIDENCE_MISMATCH",
                    "dedup_hit": transaction.get("dedup_hit"),
                    "recorded_input_sha256": fingerprint(request),
                }
            )
        except (SpecificationError, OSError, ValueError, KeyError, TypeError, AttributeError):
            # Admin-visible projections must not contain URL, headers, body or
            # provider exception text. Missing proof is never a successful check.
            checks.append({"case_id": case["case_id"], "request_id": request_id, "status": "INCOMPLETE", "reason_code": "PROXY_EVIDENCE_UNAVAILABLE"})
    return {
        "status": "VERIFIED" if any(item["status"] == "VERIFIED" for item in checks) and all(item["status"] in {"VERIFIED", "NOT_APPLICABLE"} for item in checks) else "INCOMPLETE",
        "cases": checks,
        "boundary": "PROXY_WIRE_ONLY_NOT_BACKEND_CONTEXT_OR_CANCELLATION",
    }
