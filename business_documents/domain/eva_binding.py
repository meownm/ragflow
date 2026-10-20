"""Reconstruct a saved event-backed EVA binding without changing its history."""

from collections.abc import Iterable, Mapping
from typing import Any


def binding_from_events(events: Iterable[Mapping[str, Any]]) -> dict[str, Any] | None:
    created = resolution = pull = None
    for event in events:
        if event["event_type"] == "DocumentCreated" and created is None:
            created = event
        elif event["event_type"] == "EvaBindingResolved":
            resolution = event
        elif event["event_type"] == "EvaDocumentPulled":
            pull = event
    raw = created["payload"].get("eva_binding") if created and isinstance(created["payload"], dict) else None
    if resolution and isinstance(resolution["payload"], dict):
        resolved = resolution["payload"].get("eva_binding")
        if isinstance(resolved, dict) and resolved.get("page_url"):
            raw = resolved
    if not isinstance(raw, dict) or not raw.get("page_url"):
        return None
    binding = dict(raw)
    if pull and (resolution is None or pull["sequence"] > resolution["sequence"]):
        binding.update(
            remote_version=pull["payload"].get("remote_version"),
            remote_content_hash=pull["payload"].get("remote_content_hash"),
            last_pulled_content_hash=pull["payload"].get("remote_content_hash"),
            last_pulled_at=pull["create_time"],
            last_pull_event_id=pull["id"],
            last_pull_review_cycle=pull["payload"].get("review_cycle"),
        )
    return binding
