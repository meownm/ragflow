"""Remove renewed browser credentials before persisting failure diagnostics."""

import os
import re


def redact_browser_secrets(text: str) -> str:
    for name in ("QA_ADMIN_PASSWORD", "SEEDED_USER_PASSWORD", "QA_STORAGE_PASSWORD", "QA_LIVE_TOKEN"):
        value = os.environ.get(name)
        if value:
            text = text.replace(value, "[REDACTED]")
    text = re.sub(r"(?i)(\bAuthorization[\"']?\s*:\s*[\"']?)(?:Bearer\s+)?[A-Za-z0-9_.~+/=-]+", r"\1[REDACTED]", text)
    return re.sub(r"(?i)(\b(?:Set-)?Cookie[\"']?\s*:\s*[\"']?)[^\r\n\"']+", r"\1[REDACTED]", text)
