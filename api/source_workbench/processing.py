"""Token-aware prompt planning for source-workspace processing."""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
import json
import math
import re
from typing import Any, Callable


class ProcessingError(Exception):
    def __init__(self, code: str, message: str, status: int = 413):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


@dataclass(frozen=True)
class ModelBudget:
    context_tokens: int
    output_tokens: int
    input_tokens: int
    assumed_context: bool
    count_tokens: Callable[[str], int]

    @classmethod
    def from_context(cls, configured_limit: Any, count_tokens: Callable[[str], int]) -> ModelBudget:
        # A missing/zero model limit is unknown, not unlimited.
        assumed = type(configured_limit) is not int or configured_limit <= 0
        context = 8192 if assumed else configured_limit
        if context < 2048:
            raise ProcessingError("MODEL_CONTEXT_TOO_SMALL", "У выбранной модели слишком маленькое окно контекста")
        output = min(8192, context // 4)
        reserve = max(512, context // 8)
        return cls(context, output, context - output - reserve, assumed, count_tokens)

    def estimate(self, system: str, payload: dict[str, Any]) -> int:
        serialized = json.dumps(payload, ensure_ascii=False)
        counted = self.count_tokens(system + serialized)
        if counted <= 0 and system + serialized:
            raise ProcessingError("TOKENIZER_UNAVAILABLE", "Не удалось оценить размер запроса в токенах", 503)
        # cl100k_base is the repository tokenizer, not the exact tokenizer of every provider.
        return math.ceil(counted * 1.25) + 64

    def fits(self, system: str, payload: dict[str, Any]) -> bool:
        return self.estimate(system, payload) <= self.input_tokens

    def require_fit(self, system: str, payload: dict[str, Any], *, draft: bool = False) -> None:
        required = self.estimate(system, payload)
        if required <= self.input_tokens:
            return
        code = "DRAFT_TOO_LARGE_FOR_MODEL" if draft else "MODEL_CONTEXT_EXCEEDED"
        raise ProcessingError(
            code,
            f"Запрос требует примерно {required} входных токенов при доступных {self.input_tokens}. "
            + ("Выберите модель с большим контекстом или сократите исходный текст." if draft else "Используйте режим последовательной обработки или модель с большим контекстом."),
            413,
        )

    def require_output_room(self, draft: str) -> None:
        if not draft:
            return
        required = math.ceil(self.count_tokens(draft) * 1.25) + 256
        if required > self.output_tokens:
            raise ProcessingError(
                "DRAFT_TOO_LARGE_FOR_MODEL",
                f"Полный черновик требует примерно {required} выходных токенов при доступных {self.output_tokens}. Выберите модель с большим лимитом ответа или сократите черновик.",
            )

    def require_complete_output(self, result: str) -> None:
        # The upstream streaming API does not expose finish_reason. A response
        # near the cap cannot safely be treated as a complete replacement draft.
        if math.ceil(self.count_tokens(result) * 1.25) >= self.output_tokens * 0.9:
            raise ProcessingError("MODEL_OUTPUT_LIMIT_REACHED", "Ответ приблизился к лимиту модели и мог быть обрезан. Выберите модель с большим лимитом ответа.", 502)


_HEADING = re.compile(r"^ {0,3}(#{1,6})[ \t]+\S")
_SETEXT = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_LIST_ITEM = re.compile(r"(?m)^ {0,3}(?:[-*+]|\d+[.)])[ \t]+")
_SENTENCE_END = re.compile(r"[.!?。！？][\"'»)]*[ \t]+")


@dataclass(frozen=True)
class TextFragment:
    text: str
    section_path: tuple[str, ...] = ()
    table_columns: str = ""


@dataclass(frozen=True)
class _Structure:
    headings: tuple[tuple[int, int, str], ...]
    heading_positions: tuple[int, ...]
    section_paths: tuple[tuple[str, ...], ...]
    breaks: tuple[int, ...]
    tables: tuple[tuple[int, int, str], ...]
    table_starts: tuple[int, ...]

    def fragment(self, text: str, start: int, end: int) -> TextFragment:
        heading_index = bisect_right(self.heading_positions, start) - 1
        sections = self.section_paths[heading_index] if heading_index >= 0 else ()
        columns = ""
        table_index = bisect_right(self.table_starts, start) - 1
        if table_index >= 0:
            table_start, table_end, header = self.tables[table_index]
            if table_start < start < table_end:
                columns = header
        return TextFragment(text[start:end], sections, columns)


def _structure(text: str) -> _Structure:
    """Find section starts and blank-line breaks outside fenced code and HTML tables."""
    headings: list[tuple[int, int, str]] = []
    paragraphs: list[int] = []
    tables: list[tuple[int, int, str]] = []
    lines: list[tuple[int, int, str, bool]] = []
    offset = 0
    previous_line = ""
    previous_offset = 0
    fence = ""
    html_table = False
    html_table_start = 0
    for line in text.splitlines(keepends=True):
        stripped = line.rstrip("\r\n")
        marker = _FENCE.match(stripped)
        if marker and not html_table:
            delimiter = marker.group(1)
            if not fence:
                paragraphs.append(offset)
                fence = delimiter
            elif delimiter[0] == fence[0] and len(delimiter) >= len(fence) and not stripped[marker.end(1) :].strip():
                fence = ""
                paragraphs.append(offset + len(line))
        if not fence:
            if re.search(r"<table\b", stripped, re.IGNORECASE):
                paragraphs.append(offset)
                html_table = True
                html_table_start = offset
            if not html_table:
                heading = _HEADING.match(stripped)
                if heading:
                    title = re.sub(r"\s+#+\s*$", "", stripped[heading.end(1) :].strip())
                    headings.append((offset, len(heading.group(1)), title))
                else:
                    underline = _SETEXT.match(stripped)
                    if underline and previous_line.strip() and not previous_line.lstrip().startswith(("|", "-", "*", "+")):
                        headings.append((previous_offset, 1 if underline.group(1)[0] == "=" else 2, previous_line.strip()))
                if not stripped.strip():
                    paragraphs.append(offset + len(line))
            if re.search(r"</table\s*>", stripped, re.IGNORECASE):
                html_table = False
                paragraphs.append(offset + len(line))
                table_text = text[html_table_start : offset + len(line)]
                header = re.search(r"<thead\b[^>]*>(.*?)</thead\s*>", table_text, re.IGNORECASE | re.DOTALL)
                header_text = header.group(1) if header else ""
                if not header_text:
                    first_row = re.search(r"<tr\b[^>]*>.*?</tr\s*>", table_text, re.IGNORECASE | re.DOTALL)
                    if first_row and re.search(r"<th\b", first_row.group(0), re.IGNORECASE):
                        header_text = first_row.group(0)
                columns = re.sub(r"<[^>]+>", " ", header_text).strip()
                tables.append((html_table_start, offset + len(line), columns))
        previous_line = stripped
        previous_offset = offset
        lines.append((offset, offset + len(line), stripped, bool(fence or html_table)))
        offset += len(line)

    protected_lists: list[tuple[int, int]] = []
    index = 0
    while index < len(lines):
        start, _, content, protected = lines[index]
        if protected:
            index += 1
            continue
        if _LIST_ITEM.match(content):
            last = index + 1
            while last < len(lines):
                _, _, continuation, is_protected = lines[last]
                if is_protected:
                    break
                if _LIST_ITEM.match(continuation) or (continuation.startswith(("  ", "\t")) and continuation.strip()):
                    last += 1
                elif not continuation.strip() and last + 1 < len(lines) and (_LIST_ITEM.match(lines[last + 1][2]) or lines[last + 1][2].startswith(("  ", "\t"))):
                    last += 1
                else:
                    break
            end = lines[last - 1][1]
            paragraphs.extend((start, end))
            protected_lists.append((start, end))
            index = last
            continue
        if "|" in content:
            last = index + 1
            while last < len(lines) and not lines[last][3] and "|" in lines[last][2]:
                last += 1
            table_lines = [item[2] for item in lines[index:last]]
            if len(table_lines) >= 2 and any("---" in row and set(row.strip()) <= set("|:- \t") for row in table_lines):
                paragraphs.extend((start, lines[last - 1][1]))
                tables.append((start, lines[last - 1][1], table_lines[0].strip()))
                index = last
                continue
        index += 1
    protected_blocks = protected_lists + [(start, end) for start, end, _ in tables]
    headings = [heading for heading in headings if not any(start <= heading[0] < end for start, end in protected_blocks)]
    paragraphs = sorted({position for position in paragraphs if not any(start < position < end for start, end in protected_blocks)})
    ordered_headings = tuple(sorted(headings))
    active: list[tuple[int, str]] = []
    section_paths = []
    for _, level, title in ordered_headings:
        while active and active[-1][0] >= level:
            active.pop()
        active.append((level, title))
        section_paths.append(tuple(item[1] for item in active))
    ordered_tables = tuple(sorted(tables))
    return _Structure(
        ordered_headings,
        tuple(item[0] for item in ordered_headings),
        tuple(section_paths),
        tuple(paragraphs),
        ordered_tables,
        tuple(item[0] for item in ordered_tables),
    )


def _hard_split(
    text: str,
    start: int,
    end: int,
    fits: Callable[[int, int], bool],
    max_window: int,
) -> list[tuple[int, int]]:
    """Split an oversized single block at the safest available boundary."""
    parts: list[tuple[int, int]] = []
    position = start
    while position < end:
        probe = min(end, position + max_window)
        if fits(position, probe):
            best = probe
            while probe < end:
                probe = min(end, position + (probe - position) * 2)
                if not fits(position, probe):
                    break
                best = probe
            if best == end:
                parts.append((position, end))
                break
        else:
            best = position
        low, high = best + 1, probe - 1
        while low <= high:
            middle = (low + high) // 2
            if fits(position, middle):
                best = middle
                low = middle + 1
            else:
                high = middle - 1
        if best == position:
            raise ProcessingError("MODEL_CONTEXT_TOO_SMALL", "Промпт не оставляет места для текста статьи в окне модели", 413)
        prefix = text[position:best]
        item_starts = [match.start() for match in _LIST_ITEM.finditer(prefix) if match.start() > 0]
        if item_starts:
            best = position + item_starts[-1]
        else:
            table_line = "|" in prefix.split("\n", 1)[0] or "\t" in prefix.split("\n", 1)[0]
            row_end = prefix.rfind("\n") if table_line else -1
            if row_end >= 0:
                best = position + row_end + 1
            else:
                html_rows = [match.end() for match in re.finditer(r"</tr\s*>[ \t]*(?:\r?\n)?", prefix, re.IGNORECASE)]
                if html_rows:
                    best = position + html_rows[-1]
                else:
                    sentence_ends = [match.end() for match in _SENTENCE_END.finditer(prefix) if match.end() >= len(prefix) * 0.5]
                    if sentence_ends:
                        best = position + sentence_ends[-1]
                    else:
                        line_end = prefix.rfind("\n", int(len(prefix) * 0.5))
                        word_end = max(prefix.rfind(" ", int(len(prefix) * 0.75)), prefix.rfind("\t", int(len(prefix) * 0.75)))
                        best = position + (line_end + 1 if line_end >= 0 else word_end + 1 if word_end >= 0 else len(prefix))
        parts.append((position, best))
        position = best
    return parts


def split_text_to_fit(
    text: str,
    budget: ModelBudget,
    system: str,
    make_payload: Callable[[TextFragment], dict[str, Any]],
) -> list[TextFragment]:
    """Keep sections and blocks whole when possible, without dropping characters."""
    if not text:
        return []
    whole = TextFragment(text)
    if budget.fits(system, make_payload(whole)):
        return [whole]

    structure = _structure(text)

    def fits(start: int, end: int) -> bool:
        return budget.fits(system, make_payload(structure.fragment(text, start, end)))

    def divide(start: int, end: int) -> list[tuple[int, int]]:
        if fits(start, end):
            return [(start, end)]
        inside = [(position, level) for position, level, _ in structure.headings if start < position < end]
        if inside:
            level = min(item[1] for item in inside)
            breaks = [position for position, heading_level in inside if heading_level == level]
        else:
            breaks = [position for position in structure.breaks if start < position < end]
        if breaks:
            boundaries = [start, *breaks, end]
            return [part for left, right in zip(boundaries, boundaries[1:]) for part in divide(left, right)]
        return _hard_split(text, start, end, fits, budget.input_tokens * 8)

    units = divide(0, len(text))
    packed: list[tuple[int, int]] = []
    for start, end in units:
        if packed and fits(packed[-1][0], end):
            packed[-1] = (packed[-1][0], end)
        else:
            packed.append((start, end))
    return [structure.fragment(text, start, end) for start, end in packed]


class VisibleStreamFilter:
    """Hide model reasoning tags even when their delimiters span stream chunks."""

    def __init__(self):
        self.pending = ""
        self.in_think = False

    def feed(self, chunk: str) -> str:
        self.pending += chunk
        visible = []
        while self.pending:
            if self.in_think:
                end = self.pending.find("</think>")
                if end < 0:
                    self.pending = self.pending[-(len("</think>") - 1) :]
                    break
                self.pending = self.pending[end + len("</think>") :]
                self.in_think = False
            else:
                start = self.pending.find("<think>")
                if start >= 0:
                    visible.append(self.pending[:start])
                    self.pending = self.pending[start + len("<think>") :]
                    self.in_think = True
                else:
                    safe_length = max(0, len(self.pending) - (len("<think>") - 1))
                    visible.append(self.pending[:safe_length])
                    self.pending = self.pending[safe_length:]
                    break
        return "".join(visible)

    def finish(self) -> str:
        visible = "" if self.in_think else self.pending
        self.pending = ""
        return visible
