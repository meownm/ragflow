"""Syntax-only patches whose supported diagram structure can be compared exactly."""

import re
from typing import Any

from business_documents.domain.errors import RuleViolation

MAX_SOURCE_CHARS = 50_000


def _unsafe(message: str) -> RuleViolation:
    return RuleViolation("UNSAFE_DIAGRAM_REPAIR", message)


def _text(value: str) -> str:
    return " ".join(value.split())


def _editable(line: str) -> bool:
    return line.strip() in {"{", "}"} or bool(re.match(r"^\s*(?:if|else|endif|switch|case|endswitch|partition|while|endwhile|repeat|actor|component|note|end note)\b", line))


class _Activity:
    def __init__(self, source: str):
        self.lines = [(n, line.strip()) for n, line in enumerate(source.splitlines(), 1) if line.strip()]
        self.index = 0
        self.groups: list[dict[str, Any]] = []

    def sequence(self, stops: tuple[str, ...] = (), depth: int = 0) -> tuple:
        if depth > 64:
            raise _unsafe("Слишком глубокая вложенность схемы")
        result = []
        while self.index < len(self.lines):
            number, line = self.lines[self.index]
            if any(line == stop or line.startswith(stop + " ") or line.startswith(stop + "(") for stop in stops):
                break
            self.index += 1
            if match := re.fullmatch(r'partition\s+"([^"]+)"\s*\{', line):
                body = self.sequence(("}",), depth + 1)
                self.close("}")
                result.append(("partition", match[1], body))
            elif match := re.fullmatch(r"if\s*\((.*)\)\s*then\s*\((.*)\)", line):
                question, label = match.groups()
                arms = [(label, self.sequence(("else", "endif"), depth + 1))]
                branch_lines = []
                while self.index < len(self.lines) and (branch := re.fullmatch(r"else\s*\((.*)\)", self.lines[self.index][1])):
                    branch_lines.append(self.lines[self.index][0])
                    self.index += 1
                    arms.append((branch[1], self.sequence(("else", "endif"), depth + 1)))
                end = self.close("endif")
                if len(arms) > 2:
                    self.groups.append({"opening_line": number, "branch_lines": branch_lines, "closing_line": end, "question": question, "labels": [arm[0] for arm in arms]})
                result.append(("select" if len(arms) > 2 else "if", _text(question), tuple((_text(label), body) for label, body in arms)))
            elif match := re.fullmatch(r"switch\s*\((.*)\)", line):
                arms = []
                while self.index < len(self.lines) and (branch := re.fullmatch(r"case\s*\((.*)\)", self.lines[self.index][1])):
                    self.index += 1
                    arms.append((_text(branch[1]), self.sequence(("case", "endswitch"), depth + 1)))
                self.close("endswitch")
                if not arms:
                    raise _unsafe("Пустая развилка")
                result.append(("select", _text(match[1]), tuple(arms)))
            elif match := re.fullmatch(r"while\s*\((.*)\)(?:\s+is\s*\((.*)\))?", line):
                body = self.sequence(("endwhile",), depth + 1)
                if self.index >= len(self.lines):
                    raise _unsafe("Не определена граница цикла")
                end = re.fullmatch(r"endwhile(?:\s*\((.*)\))?", self.lines[self.index][1])
                if not end:
                    raise _unsafe("Не определён выход из цикла")
                self.index += 1
                result.append(("while", _text(match[1]), match[2], body, end[1]))
            elif match := re.fullmatch(r"note\s+(right|left)(?::\s*(.*))?", line):
                body = match[2]
                if body is None:
                    parts = []
                    while self.index < len(self.lines) and self.lines[self.index][1] != "end note":
                        parts.append(self.lines[self.index][1])
                        self.index += 1
                    self.close("end note")
                    body = " ".join(parts)
                result.append(("note", match[1], _text(body)))
            elif line.startswith(":") and line.endswith(";"):
                result.append(("action", _text(line[1:-1])))
            elif line in {"@startuml", "@enduml", "start", "stop", "end", "kill", "detach"} or line.startswith(("'", "skinparam ", "title ")):
                result.append(("literal", line))
            else:
                raise _unsafe(f"Структура строки {number} требует ручной проверки")
        return tuple(result)

    def close(self, expected: str) -> int:
        if self.index >= len(self.lines) or self.lines[self.index][1] != expected:
            raise _unsafe("Неоднозначная граница конструкции")
        number = self.lines[self.index][0]
        self.index += 1
        return number


def _component(source: str) -> tuple:
    result = []
    for line in source.splitlines():
        line = line.strip()
        if not line:
            continue
        # A missing display-name quote still leaves an unambiguous ASCII alias.
        if match := re.fullmatch(r'(actor|component)\s+"?([^"\n]+?)"?\s+as\s+([A-Za-z][A-Za-z0-9_]*)', line):
            result.append((match[1], _text(match[2]), match[3]))
        elif re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*\s+(?:[-.]+>|<[-.]+)\s+[A-Za-z][A-Za-z0-9_]*\s*:\s*.+", line):
            result.append(("link", _text(line)))
        elif line in {"@startuml", "@enduml"} or line.startswith(("'", "skinparam ", "title ")):
            result.append(("literal", line))
        else:
            raise _unsafe("Структура компонентной схемы требует ручной проверки")
    return tuple(result)


def syntax_context(source: str, kind: str) -> dict[str, Any]:
    if kind not in {"activity", "component"} or not isinstance(source, str) or len(source) > MAX_SOURCE_CHARS:
        raise _unsafe("Схема превышает предел проверки")
    lines = source.splitlines()
    if (
        not lines
        or lines[0].strip() != "@startuml"
        or lines[-1].strip() != "@enduml"
        or sum(line.strip() == "@startuml" for line in lines) != 1
        or sum(line.strip() == "@enduml" for line in lines) != 1
    ):
        raise _unsafe("Ожидалась одна полная схема")
    parser = _Activity(source) if kind == "activity" else None
    structure = parser.sequence() if parser else _component(source)
    return {"structure": structure, "multiway_groups": parser.groups if parser else [], "editable_lines": [n for n, line in enumerate(lines, 1) if _editable(line)]}


def apply_syntax_edits(source: str, response: object, kind: str) -> str:
    before = syntax_context(source, kind)
    if not isinstance(response, dict) or set(response) != {"edits"} or not isinstance(response["edits"], list) or not 1 <= len(response["edits"]) <= 40:
        raise _unsafe("Ожидался непустой список адресных правок")
    lines = source.split("\n")
    edits = {}
    for edit in response["edits"]:
        if not isinstance(edit, dict) or set(edit) != {"line", "replacement"}:
            raise _unsafe("Неверный формат правки")
        line, replacement = edit["line"], edit["replacement"]
        if type(line) is not int or line not in before["editable_lines"] or line in edits or not isinstance(replacement, str) or len(replacement) > 2000:
            raise _unsafe("Недопустимый адрес правки")
        if not replacement.strip() or len(replacement.splitlines()) > 4 or not all(_editable(part) for part in replacement.splitlines() if part.strip()):
            raise _unsafe("Правка затрагивает содержимое схемы")
        edits[line] = replacement
    fixed = "\n".join(edits.get(n, line) for n, line in enumerate(lines, 1))
    if fixed == source or syntax_context(fixed, kind)["structure"] != before["structure"]:
        raise _unsafe("Изменились действия, условия, ветки, роли или подписи схемы")
    return fixed
