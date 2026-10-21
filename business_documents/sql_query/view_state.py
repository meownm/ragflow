"""Bounded per-user diagram preferences, separate from SQL revisions."""

from collections.abc import Mapping
import math


def validate_view(value: object) -> dict:
    if not isinstance(value, Mapping) or set(value) != {"positions", "viewport", "mode", "depth", "list_view"}:
        raise ValueError("Invalid diagram view")
    positions, viewport = value["positions"], value["viewport"]
    if not isinstance(positions, Mapping) or len(positions) > 100 or any(not isinstance(key, str) or not 1 <= len(key) <= 500 for key in positions):
        raise ValueError("Diagram has too many positions or an invalid node identity")

    def coordinates(raw, zoom=False):
        keys = {"x", "y", "zoom"} if zoom else {"x", "y"}
        if not isinstance(raw, Mapping) or set(raw) != keys or any(isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number) or abs(number) > 1_000_000 for number in raw.values()):
            raise ValueError("Invalid diagram coordinates")
        if zoom and not 0.15 <= raw["zoom"] <= 2:
            raise ValueError("Invalid diagram zoom")
        return dict(raw)

    if not isinstance(value["mode"], str) or value["mode"] not in {"all", "query"} or type(value["depth"]) is not int or not 1 <= value["depth"] <= 3 or type(value["list_view"]) is not bool:
        raise ValueError("Invalid diagram presentation")
    return {**value, "positions": {key: coordinates(position) for key, position in positions.items()}, "viewport": coordinates(viewport, True)}
