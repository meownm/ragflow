"""Remove credentials and session tokens before CI output reaches a log sink."""

import re
import sys


def redact(value: str) -> str:
    value = re.sub(r"(?i)(--password\s+)(\S+)", r"\1[redacted]", value)
    value = re.sub(r"(?i)(bearer\s+)[\w.+/-]+", r"\1[redacted]", value)
    value = re.sub(r"(?i)(access_token|authorization|password|api_key)([\s\"':=|]+)[^\s|,}]+", r"\1\2[redacted]", value)
    return re.sub(r"\b[0-9a-fA-F]{32}\b", "[redacted-token]", value)


if __name__ == "__main__":
    for line in sys.stdin:
        print(redact(line), end="", flush=True)
