"""Synthetic formatting contracts; expected HTML is independent of the converters."""

from dataclasses import dataclass
from html import escape


@dataclass(frozen=True)
class FormattingCase:
    id: str
    markdown: str | None
    html: str

    @property
    def eva_html(self) -> str:
        """EVA fills the container for corpus tables without authored widths."""
        return self.html.replace("<table>", '<table style="width:100%">')


CASES = [
    *(FormattingCase(f"heading-h{n}", "#" * n + " Заголовок", f"<h{n}>Заголовок</h{n}>") for n in range(1, 7)),
    FormattingCase("heading-setext-h1", "Заголовок\n===", "<h1>Заголовок</h1>"),
    FormattingCase("heading-setext-h2", "Заголовок\n---", "<h2>Заголовок</h2>"),
    FormattingCase("heading-inline", "## **Важно** и *новое*", "<h2><strong>Важно</strong> и <em>новое</em></h2>"),
    FormattingCase("paragraphs", "Первый.\n\nВторой.", "<p>Первый.</p><p>Второй.</p>"),
    FormattingCase("soft-break", "Первая\nВторая", "<p>Первая<br>Вторая</p>"),
    FormattingCase("hard-break-spaces", "Первая  \nВторая", "<p>Первая<br>Вторая</p>"),
    FormattingCase("hard-break-backslash", "Первая\\\nВторая", "<p>Первая<br>Вторая</p>"),
    FormattingCase("crlf", "Первая\r\nВторая\r\n\r\nТретья", "<p>Первая<br>Вторая</p><p>Третья</p>"),
    FormattingCase("cr", "Первая\rВторая", "<p>Первая<br>Вторая</p>"),
    FormattingCase("bold-stars", "**Жирный**", "<p><strong>Жирный</strong></p>"),
    FormattingCase("bold-underscores", "__Жирный__", "<p><strong>Жирный</strong></p>"),
    FormattingCase("italic-stars", "*Курсив*", "<p><em>Курсив</em></p>"),
    FormattingCase("italic-underscores", "_Курсив_", "<p><em>Курсив</em></p>"),
    FormattingCase("bold-italic", "***Оба***", "<p><em><strong>Оба</strong></em></p>"),
    FormattingCase("strike", "~~Удалено~~", "<p><del>Удалено</del></p>"),
    FormattingCase("nested-inline", "**Жирный *курсив* и ~~старое~~**", "<p><strong>Жирный <em>курсив</em> и <del>старое</del></strong></p>"),
    FormattingCase("adjacent-inline", "**А** *Б* ~~В~~ `Г`", "<p><strong>А</strong> <em>Б</em> <del>В</del> <code>Г</code></p>"),
    FormattingCase("escaped-markers", r"\*нет\* \_нет\_ \[текст\]", "<p>*нет* _нет_ [текст]</p>"),
    FormattingCase("entities", "&lt;tag&gt; &amp; &quot;текст&quot;", "<p>&lt;tag&gt; &amp; &quot;текст&quot;</p>"),
    FormattingCase("unicode-rtl", "Русский 中文 العربية עברית 👩‍💻 é", "<p>Русский 中文 العربية עברית 👩‍💻 é</p>"),
    FormattingCase("nbsp", "А&nbsp;Б", "<p>А&nbsp;Б</p>"),
    *(FormattingCase(f"unordered-{marker}", f"{marker} Один\n{marker} Два", "<ul><li>Один</li><li>Два</li></ul>") for marker in ("-", "+", "*")),
    *(FormattingCase(f"ordered-start-{n}", f"{n}. Один\n{n + 1}. Два", f'<ol start="{n}"><li>Один</li><li>Два</li></ol>') for n in (0, 1, 3)),
    FormattingCase("list-loose", "- Один\n\n- Два", "<ul><li><p>Один</p></li><li><p>Два</p></li></ul>"),
    FormattingCase("list-multiple-paragraphs", "- Один\n\n  Ещё абзац\n\n- Два", "<ul><li><p>Один</p><p>Ещё абзац</p></li><li>Два</li></ul>"),
    FormattingCase("list-nested-mixed", "- Один\n  1. Вложенный\n     - Глубже\n- Два", "<ul><li>Один<ol><li>Вложенный<ul><li>Глубже</li></ul></li></ol></li><li>Два</li></ul>"),
    FormattingCase("list-inline", "- **Важно** и [источник](https://example.test)", '<ul><li><strong>Важно</strong> и <a href="https://example.test">источник</a></li></ul>'),
    FormattingCase("task-unchecked", "- [ ] Сделать", '<ul><li><input type="checkbox" disabled>Сделать</li></ul>'),
    FormattingCase("task-checked", "- [x] Готово", '<ul><li><input type="checkbox" checked disabled>Готово</li></ul>'),
    FormattingCase(
        "task-mixed", "- [x] Готово\n- Обычный\n- [ ] Сделать", '<ul><li><input type="checkbox" checked disabled>Готово</li><li>Обычный</li><li><input type="checkbox" disabled>Сделать</li></ul>'
    ),
    FormattingCase("task-nested", "- [ ] Внешний\n  - [x] Внутренний", '<ul><li><input type="checkbox" disabled>Внешний<ul><li><input type="checkbox" checked disabled>Внутренний</li></ul></li></ul>'),
    FormattingCase("quote", "> Цитата", "<blockquote><p>Цитата</p></blockquote>"),
    FormattingCase("quote-nested", "> Внешняя\n>\n>> Внутренняя", "<blockquote><p>Внешняя</p><blockquote><p>Внутренняя</p></blockquote></blockquote>"),
    FormattingCase("quote-list", "> - Один\n> - Два", "<blockquote><ul><li>Один</li><li>Два</li></ul></blockquote>"),
    FormattingCase("quote-code", "> ```\n> a < b\n> ```", "<blockquote><pre><code>a &lt; b\n</code></pre></blockquote>"),
    FormattingCase("table", "| А | Б |\n| --- | --- |\n| 1 | 2 |\n| 3 | 4 |", "<table><tr><th>А</th><th>Б</th></tr><tr><td>1</td><td>2</td></tr><tr><td>3</td><td>4</td></tr></table>"),
    FormattingCase("table-empty-cell", "| А | Б |\n| --- | --- |\n| | 2 |", "<table><tr><th>А</th><th>Б</th></tr><tr><td></td><td>2</td></tr></table>"),
    FormattingCase("table-escaped-pipe", "| А |\n| --- |\n| а \\| б |", "<table><tr><th>А</th></tr><tr><td>а | б</td></tr></table>"),
    FormattingCase("table-inline", "| **А** |\n| --- |\n| *Б* ~~В~~ `Г` |", "<table><tr><th><strong>А</strong></th></tr><tr><td><em>Б</em> <del>В</del> <code>Г</code></td></tr></table>"),
    *(
        FormattingCase(f"table-align-{align}", f"| А |\n| {separator} |\n| Б |", f'<table><tr><th style="text-align:{align}">А</th></tr><tr><td style="text-align:{align}">Б</td></tr></table>')
        for align, separator in (("left", ":---"), ("center", ":---:"), ("right", "---:"))
    ),
    FormattingCase("table-in-quote", "> | А |\n> | --- |\n> | Б |", "<blockquote><table><tr><th>А</th></tr><tr><td>Б</td></tr></table></blockquote>"),
    FormattingCase("table-in-list", "- Один\n\n  | А |\n  | --- |\n  | Б |", "<ul><li><p>Один</p><table><tr><th>А</th></tr><tr><td>Б</td></tr></table></li></ul>"),
    FormattingCase("code-inline", "`a < b && x`", "<p><code>a &lt; b &amp;&amp; x</code></p>"),
    FormattingCase("code-inline-backtick", "``a ` b``", "<p><code>a ` b</code></p>"),
    FormattingCase("code-fenced", "```\na < b && x\n```", "<pre><code>a &lt; b &amp;&amp; x\n</code></pre>"),
    FormattingCase("code-indented", "    a < b\n    next", "<pre><code>a &lt; b\nnext</code></pre>"),
    *(
        FormattingCase(f"code-language-{lang}", f"```{lang}\n{source}\n```", f'<pre><code class="language-{lang}">{escape(source)}\n</code></pre>')
        for lang, source in (
            ("json", '{"ok": true}'),
            ("python", "print('Привет')"),
            ("sql", "SELECT 1;"),
            ("mermaid", "graph TD\nA --> B"),
            ("plantuml", "@startuml\nA -> B: Данные\n@enduml"),
            ("bpmn", '<definitions id="x"/>'),
        )
    ),
    FormattingCase("code-trailing-spaces", "```\na  \n  b \n```", "<pre><code>a  \n  b \n</code></pre>"),
    FormattingCase("code-edge-blank-lines", "```\n\nfirst\n\n```", "<pre><code>\nfirst\n\n</code></pre>"),
    FormattingCase("code-embedded-fence", "````\nfirst\n```\nlast\n````", "<pre><code>first\n```\nlast\n</code></pre>"),
    *(
        FormattingCase(f"link-{name}", f"[Источник]({url})", f'<p><a href="{url}">Источник</a></p>')
        for name, url in (
            ("http", "http://example.test/a"),
            ("https", "https://example.test/a?x=1&amp;y=2"),
            ("mailto", "mailto:qa@example.test"),
            ("relative", "/project/Document/42"),
            ("anchor", "#section"),
        )
    ),
    FormattingCase("link-title", '[Источник](https://example.test "Подсказка")', '<p><a href="https://example.test" title="Подсказка">Источник</a></p>'),
    FormattingCase("link-reference", '[Источник][id]\n\n[id]: https://example.test "Подсказка"', '<p><a href="https://example.test" title="Подсказка">Источник</a></p>'),
    FormattingCase("autolink", "<https://example.test>", '<p><a href="https://example.test">https://example.test</a></p>'),
    FormattingCase("bare-url", "https://example.test", '<p><a href="https://example.test">https://example.test</a></p>'),
    FormattingCase("image", '![Схема](https://example.test/image.png "Описание")', '<p><img src="https://example.test/image.png" alt="Схема" title="Описание"></p>'),
    FormattingCase("image-link", "[![Схема](http://example.test/image.png)](https://example.test)", '<p><a href="https://example.test"><img src="http://example.test/image.png" alt="Схема"></a></p>'),
    *(FormattingCase(f"rule-{n}", f"До\n\n{marker}\n\nПосле", "<p>До</p><hr><p>После</p>") for n, marker in enumerate(("---", "***", "___"))),
]

NATIVE_CASES = [
    FormattingCase("eva-bold-b", None, "<p><b>Жирный</b></p>"),
    FormattingCase("eva-italic-i", None, "<p><i>Курсив</i></p>"),
    FormattingCase("eva-strike-s", None, "<p><s>Удалено</s></p>"),
    FormattingCase("eva-strike-strike", None, "<p><strike>Удалено</strike></p>"),
    *(FormattingCase(f"eva-{tag}", None, f"<p><{tag}>Текст</{tag}></p>") for tag in ("u", "sub", "sup", "mark", "kbd", "abbr")),
    *(
        FormattingCase(f"eva-css-{name}", None, f'<p><span style="{css}">Текст</span></p>')
        for name, css in (
            ("color", "color:#ff0000"),
            ("highlight", "background-color:#ffff00"),
            ("font", "font-family:Arial"),
            ("size", "font-size:24px"),
            ("bold", "font-weight:700"),
            ("italic", "font-style:italic"),
            ("underline", "text-decoration:underline"),
        )
    ),
    *(FormattingCase(f"eva-paragraph-{align}", None, f'<p style="text-align:{align}">Текст</p>') for align in ("left", "center", "right", "justify")),
    FormattingCase("eva-indent", None, '<p style="margin-left:40px">Текст</p>'),
    FormattingCase("eva-empty-paragraph", None, "<p>До</p><p><br></p><p>После</p>"),
    FormattingCase("eva-table-colspan", None, '<table><tr><th colspan="2">А</th></tr><tr><td>Б</td><td>В</td></tr></table>'),
    FormattingCase("eva-table-rowspan", None, '<table><tr><th>А</th><th>Б</th></tr><tr><td rowspan="2">В</td><td>Г</td></tr><tr><td>Д</td></tr></table>'),
    FormattingCase("eva-table-no-header", None, "<table><tr><td>А</td><td>Б</td></tr></table>"),
    FormattingCase("eva-table-break", None, "<table><tr><th>А</th></tr><tr><td>Первая<br>Вторая</td></tr></table>"),
    FormattingCase("eva-ordered-reversed", None, '<ol reversed start="3"><li>Три</li><li>Два</li></ol>'),
    FormattingCase("eva-ordered-item-value", None, '<ol><li value="5">Пять</li><li>Шесть</li></ol>'),
    FormattingCase("eva-definition-list", None, "<dl><dt>Термин</dt><dd>Определение</dd></dl>"),
    FormattingCase("eva-details", None, "<details><summary>Заголовок</summary><p>Содержимое</p></details>"),
    FormattingCase("eva-font-tag", None, '<p><font face="Arial" size="5" color="#ff0000">Текст</font></p>'),
    FormattingCase("eva-image-size", None, '<p><img src="https://example.test/image.png" alt="Схема" width="320" height="200"></p>'),
    FormattingCase("eva-ordered-roman", None, '<ol type="I"><li>Первый</li><li>Второй</li></ol>'),
    FormattingCase("eva-ordered-alpha", None, '<ol type="a"><li>Первый</li><li>Второй</li></ol>'),
    FormattingCase("eva-table-nested", None, "<table><tr><th>А</th></tr><tr><td><table><tr><th>Б</th></tr><tr><td>В</td></tr></table></td></tr></table>"),
    FormattingCase("eva-cell-background", None, '<table><tr><th>А</th></tr><tr><td style="background-color:#ffff00">Б</td></tr></table>'),
    FormattingCase("eva-checklist", None, '<ul class="tox-checklist"><li>Нет</li><li class="tox-checklist--checked">Да</li><li class="tox-checklist--hidden">Обычный</li></ul>'),
    FormattingCase("eva-reversed-default", None, "<ol reversed><li>Два</li><li>Один</li></ol>"),
    FormattingCase("eva-reversed-reset", None, '<ol reversed start="5"><li>Пять</li><li value="2">Два</li><li>Один</li></ol>'),
    FormattingCase("eva-anchor-target", None, '<h2 id="section">Цель</h2><p><a href="#section">Ссылка</a></p>'),
    FormattingCase("eva-anchor-unicode", None, '<h2 id="раздел">Цель</h2><p><a href="#раздел">Ссылка</a></p>'),
    *(FormattingCase(f"eva-font-size-{size}", None, f'<p><font size="{size}">Размер</font></p>') for size in ("1", "3", "7", "+2", "-1")),
]

MARKDOWN_CASES = tuple(CASES)
ALL_CASES = (*MARKDOWN_CASES, *NATIVE_CASES)
