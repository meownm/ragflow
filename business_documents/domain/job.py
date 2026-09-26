"""Attribution retained by the immutable job snapshot."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SnapshotIndex:
    event_ids: frozenset[str]
    evidence_refs: frozenset[str]

    @classmethod
    def build(cls, job: Mapping[str, Any], execution: Mapping[str, Any]) -> "SnapshotIndex":
        payload = job["payload"] if isinstance(job["payload"], dict) else {}
        retrieval = execution.get("retrieval") if isinstance(execution, dict) else None
        return cls(
            frozenset(event["event_id"] for event in payload.get("source_events", []) if isinstance(event, dict) and isinstance(event.get("event_id"), str)),
            frozenset(retrieval.get("source_refs", [])) if isinstance(retrieval, dict) else frozenset(),
        )


def requested_by(job: Mapping[str, Any], fallback: str) -> str:
    actor_id = job["payload"].get("requested_by_actor_id") if isinstance(job["payload"], dict) else None
    return actor_id if isinstance(actor_id, str) and actor_id else fallback


def has_current_lease(job: Mapping[str, Any], worker_id: str, lease_token: str | None, now: int) -> bool:
    return bool(
        job["status"] == "RUNNING" and lease_token and job["lease_owner"] == worker_id and job["lease_token"] == lease_token and job["lease_expires_at"] is not None and job["lease_expires_at"] > now
    )


def prompt_audit(job: Mapping[str, Any]) -> dict[str, Any]:
    prompt = job["payload"].get("prompt") if isinstance(job["payload"], dict) else None
    if not isinstance(prompt, dict):
        return {}
    return {"prompt_name": prompt.get("name"), "prompt_version": prompt.get("version"), "prompt_hash": prompt.get("content_hash")}
