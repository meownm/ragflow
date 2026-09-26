"""Extract complete change operations from a streamed JSON response.

An operation is only a preliminary candidate. The full change plan still has
to pass the normal validation and transaction checks before it is applied.
"""

from __future__ import annotations

import json
from typing import Any


class ChangeOperationStream:
    """Incrementally read objects from the first ``operations`` JSON array."""

    def __init__(self, *, max_response_chars: int = 1_000_000) -> None:
        self._text = ""
        self._offset = 0
        self._stack: list[str] = []
        self._in_string = False
        self._escaped = False
        self._string_start = 0
        self._operation_key = False
        self._awaiting_array = False
        self._array_depth: int | None = None
        self._operation_start: int | None = None
        self._max_response_chars = max_response_chars

    def feed(self, chunk: str) -> list[dict[str, Any]]:
        if not isinstance(chunk, str):
            raise TypeError("A streamed change-plan chunk must be text")
        self._text += chunk
        if len(self._text) > self._max_response_chars:
            raise ValueError("Streamed change plan exceeds the response limit")
        operations: list[dict[str, Any]] = []
        while self._offset < len(self._text):
            index = self._offset
            char = self._text[index]
            self._offset += 1
            if self._in_string:
                if self._escaped:
                    self._escaped = False
                elif char == "\\":
                    self._escaped = True
                elif char == '"':
                    self._in_string = False
                    if self._stack and self._stack[-1] == "{":
                        self._operation_key = self._text[self._string_start + 1 : index] == "operations"
                continue
            if char == '"':
                self._in_string = True
                self._string_start = index
                continue
            if self._operation_key:
                if char.isspace():
                    continue
                self._awaiting_array = char == ":"
                self._operation_key = False
                if self._awaiting_array:
                    continue
            if self._awaiting_array and not char.isspace():
                if char == "[" and self._array_depth is None:
                    self._array_depth = len(self._stack) + 1
                self._awaiting_array = False
            if char in "{[":
                if char == "{" and self._array_depth == len(self._stack) and self._operation_start is None:
                    self._operation_start = index
                self._stack.append(char)
            elif char in "}]":
                if not self._stack:
                    continue
                opening = self._stack.pop()
                if (opening, char) not in {("{", "}"), ("[", "]")}:
                    self._array_depth = None
                    self._operation_start = None
                    continue
                if char == "}" and self._operation_start is not None and len(self._stack) == self._array_depth:
                    value = json.loads(self._text[self._operation_start : self._offset])
                    if isinstance(value, dict):
                        operations.append(value)
                    self._operation_start = None
                elif char == "]" and len(self._stack) + 1 == self._array_depth:
                    self._array_depth = None
        return operations
