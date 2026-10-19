"""Text normalization shared by document exchange consumers."""

import hashlib


def canonical_markdown(value: str) -> str:
    lines = str(value or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def content_hash(markdown: str) -> str:
    return "sha256:" + hashlib.sha256(canonical_markdown(markdown).encode("utf-8")).hexdigest()
