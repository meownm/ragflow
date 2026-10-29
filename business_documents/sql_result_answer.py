"""Bounded result context and answers whose cell values come from saved rows."""

from __future__ import annotations

import json
import re

from business_documents.sql_assistant import present_rows

MAX_ROWS = 100
MAX_COLUMNS = 40
MAX_CELL_CHARS = 512
MAX_CONTEXT_BYTES = 72_000
CELL = re.compile(r"\{\{(\d+):(out_[a-f0-9]+_\d+)\}\}")


def cell_text(value):
    if value is None:
        return "NULL"
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def answer_context(question, run_id, rows, columns, view, sql, parameters, truncated, *, metadata=None):
    """Use the saved view's order and stable raw-row indices, never a browser page."""
    by_id = {column["id"]: column for column in columns}
    hidden = set(view["hidden"])
    visible = [by_id[key] for key in view["column_order"] if key not in hidden]
    selected_columns = visible[:MAX_COLUMNS]
    positions = {column["id"]: index for index, column in enumerate(columns)}
    presented = present_rows(rows, columns, view)
    context = {
        "question": question,
        "run_id": run_id,
        "sql": sql[:16_000],
        "parameters": parameters,
        "columns": [{"id": column["id"], "name": column["name"], "label": view["labels"].get(column["id"], column["name"])} for column in selected_columns],
        "saved_row_count": len(rows),
        "view_row_count": len(presented),
        "view_first_row_index": presented[0][0] if presented else None,
        "output_limited": bool(truncated),
        "source_completeness": "UNKNOWN",
        "context_limited": len(visible) > MAX_COLUMNS or len(sql) > 16_000,
        "rows": [],
        **({"dialogue": metadata} if metadata else {}),
    }
    size = len(json.dumps(context, ensure_ascii=False).encode())
    if size > MAX_CONTEXT_BYTES:
        raise ValueError("Контекст результата слишком велик. Сократите вопрос или число колонок.")
    sampled = presented[:MAX_ROWS]
    selected = (metadata or {}).get("selected_row")
    if selected:
        target = next((item for item in presented if item[0] == selected["row_index"]), None)
        if target is None or selected["dimension"] not in {column["id"] for column in selected_columns}:
            raise ValueError("Выбранная строка или колонка отсутствует в доступном представлении")
        sampled = [target] + [item for item in presented[:MAX_ROWS] if item[0] != target[0]][: MAX_ROWS - 1]
    for index, row in sampled:
        cells = {}
        for column in selected_columns:
            value = cell_text(row[positions[column["id"]]])
            cells[column["id"]] = {"value": value[:MAX_CELL_CHARS], "clipped": len(value) > MAX_CELL_CHARS}
        if not context["context_limited"] and any(cell["clipped"] for cell in cells.values()):
            context["context_limited"] = True
            size -= 1  # JSON true is one byte shorter than false.
        item = {"index": index, "cells": cells}
        addition = len(json.dumps(item, ensure_ascii=False).encode()) + (2 if context["rows"] else 0)
        if size + addition > MAX_CONTEXT_BYTES:
            if selected and not context["rows"]:
                raise ValueError("Выбранная строка слишком велика. Сократите число видимых колонок.")
            break
        context["rows"].append(item)
        size += addition
    context["context_limited"] |= len(context["rows"]) < len(presented)
    return context


def validate_answer(plan, context):
    if not isinstance(plan, dict) or set(plan) != {"kind", "text"}:
        raise ValueError("Неверный формат ответа по результату")
    kind, text = plan["kind"], plan["text"]
    if kind not in {"ANSWER", "OVERVIEW", "NEEDS_QUERY", "CLARIFICATION"} or not isinstance(text, str) or len(text) > 4000:
        raise ValueError("Неверный формат ответа по результату")
    if kind in {"OVERVIEW", "NEEDS_QUERY"}:
        if text:
            raise ValueError("Для этого ответа текст формирует сервер")
        return dict(plan)
    references = CELL.findall(text)
    prose = CELL.sub("", text)
    if not text.strip() or any(char.isdecimal() for char in prose) or "{{" in prose or "}}" in prose or "```" in prose or re.search(r"\bSELECT\b.+\bFROM\b", prose, re.I | re.S):
        raise ValueError("Значения ответа должны ссылаться на ячейки сохранённого результата")
    if kind == "ANSWER" and not references:
        raise ValueError("Ответ должен содержать ссылку на результат")
    cells = {(str(row["index"]), key): value for row in context["rows"] for key, value in row["cells"].items()}
    for reference in references:
        if reference not in cells or cells[reference]["clipped"]:
            raise ValueError("Ячейка ответа отсутствует в доступном контексте или обрезана")
    return dict(plan)


def render_answer(plan, context):
    validate_answer(plan, context)
    cells = {(str(row["index"]), key): value for row in context["rows"] for key, value in row["cells"].items()}
    columns = {column["id"]: column["label"] for column in context["columns"]}
    citations = []

    def replace(match):
        row, key = match.groups()
        value = cells[(row, key)]["value"]
        citation = {"row_index": int(row), "column_id": key, "column": columns[key], "value": value}
        if citation not in citations:
            citations.append(citation)
        return value

    if plan["kind"] == "NEEDS_QUERY":
        text = "Для этого вопроса нужны другие данные или новый расчёт. Переключитесь на «Изменить запрос»: предложение SQL будет подготовлено отдельно. Текущий запрос не изменён."
    elif plan["kind"] == "OVERVIEW":
        text = f"В сохранённом результате {context['saved_row_count']} строк, после фильтров представления — {context['view_row_count']}. Колонки: " + ", ".join(columns.values()) + "."
        if not context["saved_row_count"]:
            text += " Пустой результат не означает нулевое значение показателя."
    else:
        text = CELL.sub(replace, plan["text"])
    return {
        "status": "READY",
        "kind": plan["kind"],
        "text": text,
        "citations": citations,
        "scope": "LIMITED" if context["output_limited"] else "SAVED_RESULT",
        "context_limited": context["context_limited"],
        "source_completeness": "UNKNOWN",
    }
