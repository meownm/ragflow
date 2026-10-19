"""Validation shared by the chat, Canvas and dataset write paths."""


def validate_resource_name(value, *, limit: int, label: str, bmp_only: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string.")
    value = value.strip()
    if not value:
        raise ValueError(f"{label} cannot be empty.")
    if len(value) > limit:
        raise ValueError(f"{label} must be at most {limit} characters.")
    if any(char == "\x00" or 0xD800 <= ord(char) <= 0xDFFF or (bmp_only and ord(char) > 0xFFFF) for char in value):
        raise ValueError(f"{label} contains unsupported characters.")
    return value
