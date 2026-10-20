"""Read-only external monitor contract; never schedules or retries quality work."""

import argparse
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from common.document_quality_schedule import NIGHTLY_MAX_DURATION, expected_night_slot

PRODUCTS = ("business_requirements", "technical_specification")
REASONS = {"NIGHTLY_MISSING", "NIGHTLY_INCOMPLETE", "NIGHTLY_OVERDUE"}
MAX_BODY = 64 * 1024


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, _request, _response, _code, _message, _headers, _url):
        return None


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate health field")
        result[key] = value
    return result


def check_nights(base_url, token, *, now=None):
    """Return explicit unavailable/stale states; exceptions and tokens stay private."""
    address = urlsplit(base_url)
    if (
        address.scheme not in ("http", "https")
        or not address.hostname
        or address.username
        or address.password
        or address.query
        or address.fragment
        or "\n" in base_url
        or "\r" in base_url
        or not isinstance(token, str)
        or not token
        or "\n" in token
        or "\r" in token
    ):
        raise ValueError("Configure an HTTP(S) service origin and an admin token")
    observed_now = now or datetime.now(UTC)
    if observed_now.tzinfo is None:
        raise ValueError("Monitor time must include a timezone")
    checks = []
    deadline = time.monotonic() + 20
    opener = build_opener(_NoRedirect())
    for product in PRODUCTS:
        path = base_url.rstrip("/") + "/api/v1/admin/document-quality/health?" + urlencode({"document_type": product})
        result = {"document_type": product, "status": "UNAVAILABLE", "reason_code": "HEALTH_UNAVAILABLE"}
        try:
            request = Request(path, headers={"Authorization": token, "Cache-Control": "no-cache", "Accept": "application/json"}, method="GET")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Monitor deadline")
            with opener.open(request, timeout=min(10, remaining)) as response:
                if response.status != 200:
                    raise ValueError("Unexpected health response")
                chunks, size = [], 0
                while True:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Monitor deadline")
                    chunk = response.read1(16384)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > MAX_BODY:
                        raise ValueError("Oversized health response")
                    chunks.append(chunk)
            envelope = json.loads(b"".join(chunks), object_pairs_hook=_unique_pairs)
            data = envelope.get("data") if isinstance(envelope, dict) and type(envelope.get("code")) is int and envelope["code"] == 0 else None
            if not isinstance(data, dict) or data.get("document_type") != product:
                raise ValueError("Wrong health product or envelope")
            observed = datetime.fromisoformat(data["observed_at"])
            slot = datetime.fromisoformat(data["slot"])
            if observed.tzinfo is None or slot.tzinfo is None:
                raise ValueError("Health timestamps must include timezones")
            age = (observed_now - observed).total_seconds()
            if not -60 <= age <= 300:
                result["reason_code"] = "HEALTH_STALE"
            elif slot != expected_night_slot(observed_now):
                result["reason_code"] = "HEALTH_SLOT_MISMATCH"
            elif data.get("status") == "ALERT" and data.get("reason_code") in REASONS:
                result.update(status="ALERT", reason_code=data["reason_code"])
            elif (
                data.get("status") == "OK"
                and data.get("reason_code") is None
                and isinstance(data.get("campaign_id"), str)
                and 1 <= len(data["campaign_id"]) <= 80
                and data.get("campaign_status") in {"PENDING", "RUNNING", "COMPLETE"}
            ):
                if data["campaign_status"] in {"PENDING", "RUNNING"} and observed_now > slot + NIGHTLY_MAX_DURATION:
                    result.update(status="ALERT", reason_code="NIGHTLY_OVERDUE")
                else:
                    result.update(status="OK", reason_code=None)
            else:
                raise ValueError("Inconsistent health status")
        except HTTPError as error:
            result["reason_code"] = "HEALTH_AUTH_FAILED" if error.code in (401, 403) else "HEALTH_HTTP_FAILED"
            error.close()
        except (OSError, URLError, ValueError, KeyError, TypeError):
            # Never echo a URL, provider text, response body, token or exception.
            pass
        checks.append(result)
    code = 2 if any(item["status"] == "UNAVAILABLE" for item in checks) else 1 if any(item["status"] == "ALERT" for item in checks) else 0
    return code, {"status": "UNAVAILABLE" if code == 2 else "ALERT" if code == 1 else "OK", "checks": checks}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--token-env", default="DOCUMENT_QUALITY_ADMIN_TOKEN")
    args = parser.parse_args()
    try:
        code, report = check_nights(args.base_url, os.environ.get(args.token_env))
    except ValueError:
        code, report = 3, {"status": "NOT_CONFIGURED", "reason_code": "HEALTH_MONITOR_CONFIGURATION_REQUIRED"}
    print(json.dumps(report, ensure_ascii=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
