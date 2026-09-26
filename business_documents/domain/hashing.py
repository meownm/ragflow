"""Stable hashes used by saved document commands, revisions and identities."""

import hashlib
import json


def text_hash(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def stable_hash(value: object) -> str:
    return text_hash(json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True))
