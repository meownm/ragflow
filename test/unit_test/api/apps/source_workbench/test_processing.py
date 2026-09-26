"""Model-context planning and safe visible streaming."""

import pytest

from common.token_utils import num_tokens_from_string
from api.source_workbench.processing import ModelBudget, ProcessingError, VisibleStreamFilter, split_text_to_fit


pytestmark = pytest.mark.p1


_tokenizer = {"count": num_tokens_from_string}


def make_budget(configured_limit):
    return ModelBudget.from_context(configured_limit, _tokenizer["count"])


def test_budget_uses_configured_context_and_reserves_output():
    budget = make_budget(8192)
    assert budget.context_tokens == 8192
    assert budget.assumed_context is False
    assert budget.output_tokens == 2048
    assert budget.input_tokens < 8192 - budget.output_tokens


def test_budget_rejects_output_near_generation_cap():
    budget = make_budget(2048)
    with pytest.raises(ProcessingError) as error:
        budget.require_complete_output("word " * 500)
    assert error.value.code == "MODEL_OUTPUT_LIMIT_REACHED"
    assert make_budget(0).assumed_context is True
    with pytest.raises(ProcessingError) as error:
        make_budget(1024)
    assert error.value.code == "MODEL_CONTEXT_TOO_SMALL"


def test_long_text_is_split_without_omission_and_each_request_fits():
    budget = make_budget(4096)
    original = "Первый абзац с фактами.\n\nВторой абзац с деталями.\n\n" * 900

    def make_payload(fragment):
        return {"task": "Составь обзор", "fragment": fragment.text}

    parts = split_text_to_fit(original, budget, "Извлеки факты", make_payload)
    assert len(parts) > 1
    assert "".join(part.text for part in parts) == original
    assert all(budget.fits("Извлеки факты", make_payload(part)) for part in parts)


def test_compressible_text_is_not_truncated_at_search_window(monkeypatch):
    monkeypatch.setitem(_tokenizer, "count", lambda value: len(value) // 16)
    budget = make_budget(4096)
    original = " " * (budget.input_tokens * 16)

    def make_payload(fragment):
        return {"fragment": fragment.text}

    parts = split_text_to_fit(original, budget, "Извлеки", make_payload)
    assert len(parts) >= 2
    assert "".join(part.text for part in parts) == original


def test_major_markdown_sections_stay_whole_when_they_fit(monkeypatch):
    monkeypatch.setitem(_tokenizer, "count", len)
    budget = make_budget(2048)
    first = "# Первая глава\n\n## Детали\n\n" + "А" * 550 + "\n\n"
    second = "# Вторая глава\n\n## Вывод\n\n" + "Б" * 550
    parts = split_text_to_fit(first + second, budget, "Извлеки", lambda fragment: {"fragment": fragment.text})
    assert [part.text for part in parts] == [first, second]


def test_oversized_section_splits_at_subheadings(monkeypatch):
    monkeypatch.setitem(_tokenizer, "count", len)
    budget = make_budget(2048)
    first = "# Глава\n\n## Часть А\n\n" + "А" * 550 + "\n\n"
    second = "## Часть Б\n\n" + "Б" * 550
    parts = split_text_to_fit(first + second, budget, "Извлеки", lambda fragment: {"fragment": fragment.text})
    assert [part.text for part in parts] == [first, second]
    assert parts[1].section_path == ("Глава", "Часть Б")


def test_setext_headings_are_section_boundaries(monkeypatch):
    monkeypatch.setitem(_tokenizer, "count", len)
    budget = make_budget(2048)
    first = "Первая глава\n=============\n\n" + "А" * 550 + "\n\n"
    second = "Вторая глава\n=============\n\n" + "Б" * 550
    parts = split_text_to_fit(first + second, budget, "Извлеки", lambda fragment: {"fragment": fragment.text})
    assert [part.text for part in parts] == [first, second]


def test_paragraph_list_table_and_fenced_code_are_kept_whole_when_possible(monkeypatch):
    monkeypatch.setitem(_tokenizer, "count", len)
    budget = make_budget(2048)
    blocks = [
        "Абзац " + "А" * 360 + "\n\n",
        "- Первый пункт " + "Б" * 120 + "\n  # Заголовок внутри пункта\n- Второй пункт " + "В" * 120 + "\n\n",
        "| Поле | Значение |\n| --- | --- |\n| А | " + "Г" * 220 + " |\n\n",
        "```text\n```python\n# это строка кода\n\n" + "Д" * 350 + "\n```\n",
    ]
    original = "".join(blocks)
    parts = split_text_to_fit(original, budget, "Извлеки", lambda fragment: {"fragment": fragment.text})
    assert len(parts) > 1
    assert "".join(part.text for part in parts) == original
    assert all(any(block in part.text for part in parts) for block in blocks)
    assert all(budget.fits("Извлеки", {"fragment": part.text}) for part in parts)


def test_table_and_list_without_blank_separators_stay_whole(monkeypatch):
    monkeypatch.setitem(_tokenizer, "count", len)
    budget = make_budget(2048)
    table = "| Поле | Значение |\n| --- | --- |\n| А | " + "Г" * 180 + " |\n"
    listed = "- Первый пункт " + "Б" * 100 + "\n- Второй пункт " + "В" * 100 + "\n"
    original = "Абзац " + "А" * 500 + "\n" + table + listed + "Завершение " + "Д" * 350
    parts = split_text_to_fit(original, budget, "Извлеки", lambda fragment: {"fragment": fragment.text})
    assert len(parts) > 1
    assert "".join(part.text for part in parts) == original
    assert any(table in part.text for part in parts)
    assert any(listed in part.text for part in parts)


def test_table_without_outer_pipes_stays_whole(monkeypatch):
    monkeypatch.setitem(_tokenizer, "count", len)
    budget = make_budget(2048)
    table = "Поле | Значение\n--- | ---\nА | " + "Г" * 180 + "\n"
    original = "Абзац " + "А" * 550 + "\n" + table + "Завершение " + "Д" * 350
    parts = split_text_to_fit(original, budget, "Извлеки", lambda fragment: {"fragment": fragment.text})
    assert "".join(part.text for part in parts) == original
    assert any(table in part.text for part in parts)


def test_html_table_and_code_fence_without_blank_separators_stay_whole(monkeypatch):
    monkeypatch.setitem(_tokenizer, "count", len)
    budget = make_budget(2048)
    table = "<table>\n<tr><td>" + "Т" * 200 + "</td></tr>\n\n</table>\n"
    code = "```text\n# внутри блока\n" + "К" * 200 + "\n```\n"
    original = "Введение " + "А" * 500 + "\n" + table + code + "Конец " + "Б" * 350
    parts = split_text_to_fit(original, budget, "Извлеки", lambda fragment: {"fragment": fragment.text})
    assert "".join(part.text for part in parts) == original
    assert any(table in part.text for part in parts)
    assert any(code in part.text for part in parts)


def test_oversized_table_splits_between_rows(monkeypatch):
    monkeypatch.setitem(_tokenizer, "count", len)
    budget = make_budget(2048)
    original = "| Поле | Значение |\n| --- | --- |\n" + "".join(f"| {index} | {'А' * 100} |\n" for index in range(12))

    def payload(fragment):
        return {"fragment": fragment.text, "table_columns": fragment.table_columns}

    parts = split_text_to_fit(original, budget, "Извлеки", payload)
    assert len(parts) > 1
    assert "".join(part.text for part in parts) == original
    assert all(part.text.endswith("\n") for part in parts[:-1])
    assert parts[1].table_columns == "| Поле | Значение |"
    assert all(budget.fits("Извлеки", payload(part)) for part in parts)


def test_compressible_table_row_is_not_cut_at_search_window(monkeypatch):
    monkeypatch.setitem(_tokenizer, "count", lambda value: len(value) // 16)
    budget = make_budget(2048)
    header = "| Key | Value |\n| --- | --- |\n"
    original = header + "| 1 | " + "А" * 9000 + " |\n" + "| 2 | " + "Б" * 9000 + " |\n"

    def payload(fragment):
        return {"fragment": fragment.text, "table_columns": fragment.table_columns}

    parts = split_text_to_fit(original, budget, "Извлеки", payload)
    assert len(parts) == 2
    assert "".join(part.text for part in parts) == original
    assert parts[0].text.endswith("\n")
    assert parts[1].text.startswith("| 2 |")
    assert parts[1].table_columns == "| Key | Value |"
    assert all(budget.fits("Извлеки", payload(part)) for part in parts)


def test_continuation_keeps_section_path_within_budget(monkeypatch):
    monkeypatch.setitem(_tokenizer, "count", len)
    budget = make_budget(2048)
    original = "# Финансы\n\n## Суммы в тысячах рублей\n\n" + ("Значение " + "1" * 300 + "\n\n") * 5

    def payload(fragment):
        return {"fragment": fragment.text, "section_path": fragment.section_path}

    parts = split_text_to_fit(original, budget, "Извлеки", payload)
    assert len(parts) > 1
    assert "".join(part.text for part in parts) == original
    assert parts[1].section_path == ("Финансы", "Суммы в тысячах рублей")
    assert all(budget.fits("Извлеки", payload(part)) for part in parts)


def test_html_table_continuation_keeps_column_names(monkeypatch):
    monkeypatch.setitem(_tokenizer, "count", len)
    budget = make_budget(2048)
    header = "<table>\n<tr><th>Name</th><th>Amount</th></tr>\n"
    original = header + "".join(f"<tr><td>{index}</td><td>{'А' * 120}</td></tr>\n" for index in range(12)) + "</table>\n"

    def payload(fragment):
        return {"fragment": fragment.text, "table_columns": fragment.table_columns}

    parts = split_text_to_fit(original, budget, "Извлеки", payload)
    assert len(parts) > 1
    assert "".join(part.text for part in parts) == original
    assert "Name" in parts[1].table_columns
    assert "Amount" in parts[1].table_columns
    assert all(budget.fits("Извлеки", payload(part)) for part in parts)


def test_html_table_prefers_row_boundary_over_sentence_inside_next_row(monkeypatch):
    monkeypatch.setitem(_tokenizer, "count", len)
    budget = make_budget(2048)
    header = "<table>\n<tr><th>Name</th><th>Amount</th></tr>\n"
    first = "<tr><td>First</td><td>" + "А" * 240 + "</td></tr>\n"
    second = "<tr><td>Second. " + "Б" * 450 + "</td><td>20</td></tr>\n"
    original = header + first + second + "</table>\n"
    parts = split_text_to_fit(original, budget, "Извлеки", lambda fragment: {"fragment": fragment.text, "table_columns": fragment.table_columns})
    assert len(parts) > 1
    assert "".join(part.text for part in parts) == original
    assert any(second in part.text for part in parts)


def test_stream_filter_hides_split_reasoning_tags():
    visible = VisibleStreamFilter()
    chunks = ["Начало <thi", "nk>скрыто", "</thi", "nk> ответ"]
    result = "".join(visible.feed(chunk) for chunk in chunks) + visible.finish()
    assert result == "Начало  ответ"
