import pytest

from api.technical_specifications.ai import SpecificationAI
from api.technical_specifications.domain import SpecificationError, empty_sections


@pytest.mark.parametrize(
    "after,expected",
    [
        ("**Новый**\nследующая строка", "<strong>Новый</strong><br />\nследующая строка"),
        ("*курсив* и `код`", "<em>курсив</em> и <code>код</code>"),
        ("Просто текст", "Просто текст"),
        ("<strong>Новый</strong><br>строка", "<strong>Новый</strong><br>строка"),
    ],
)
def test_inline_model_edits_in_html_keep_format_and_unaffected_source(after, expected):
    body = '<p style="color:red">Начало: Старый; конец.</p>\n<pre><code>keep</code></pre>'
    source = {**empty_sections(), "6": body}
    result = SpecificationAI._apply_edits(source, [{"section": "6", "before": "Старый", "after": after}])
    assert result["6"] == body.replace("Старый", expected)
    assert source["6"] == body


@pytest.mark.parametrize(
    "body", ["Старый", '<pre><code class="language-mermaid">Старый</code></pre>', '<p title="Старый">Текст</p>', "```html\n<p>Старый</p>\n```", "`<p>Старый</p>`", "    <p>Старый</p>"]
)
def test_markdown_code_and_attributes_are_never_interpreted_as_html_text(body):
    after = "**Новый**\nстрока"
    source = {**empty_sections(), "6": body}
    assert SpecificationAI._apply_edits(source, [{"section": "6", "before": "Старый", "after": after}])["6"] == body.replace("Старый", after)


def test_representation_handling_does_not_repair_missing_or_ambiguous_quotes():
    source = {**empty_sections(), "6": "<p>Старый Старый</p>"}
    with pytest.raises(SpecificationError, match="Цитата"):
        SpecificationAI._apply_edits(source, [{"section": "6", "before": "Старый", "after": "**Новый**"}])


def test_html_text_offsets_follow_lf_not_unicode_line_separators():
    body = "<p>Первый\u2028Старый</p>"
    assert SpecificationAI._apply_edits({**empty_sections(), "1": body}, [{"section": "1", "before": "Старый", "after": "__Новый__"}])["1"] == "<p>Первый\u2028<strong>Новый</strong></p>"


@pytest.mark.asyncio
async def test_chat_passes_exact_mixed_source_and_code_to_model_and_formats_html_reply(monkeypatch):
    source = {**empty_sections(), "6": '<p>Старый<br>строка</p>\n<pre><code class="language-mermaid">graph LR\nA--&gt;B\n</code></pre>'}
    ai = SpecificationAI()

    async def ask(actor, system, payload, **kwargs):
        import json

        assert json.loads(json.dumps(payload))["sections"] == source
        assert payload["sections"] == source
        assert "точный сохранённый исходник" in system
        assert "SVG" in system and "HTML" in system
        return {"required_sections": ["6"], "answer": "Предлагаю", "edits": [{"section": "6", "before": "Старый", "after": "**Новый**\nвторая строка"}]}

    monkeypatch.setattr(ai, "_ask", ask)
    result = await ai.chat("actor", {"revision": 1, "sections": source, "messages": [], "proposals": []}, {"message_id": "m", "text": "Обнови", "context": {"scope": "all", "revision": 1}})
    assert result["patches"]["6"] == source["6"].replace("Старый", "<strong>Новый</strong><br />\nвторая строка")
