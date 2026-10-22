"""Model-output representation at the Markdown/HTML boundary, without source rewrites."""

import re
from html.parser import HTMLParser

import mistune

_MARKDOWN = mistune.create_markdown(escape=True, hard_wrap=True, plugins=["strikethrough"])
_TEXT_CONTAINERS = {"p", "div", "span", "li", "td", "th", "h3", "h4", "h5", "h6", "strong", "em", "b", "i", "a"}
_VOID = {"br", "hr", "img", "input", "wbr"}


def _without_markdown_code(source):
    def blank(value):
        return re.sub(r"[^\r\n]", " ", value)

    lines, fence = [], ""
    for line in source.splitlines(keepends=True):
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if fence:
            lines.append(blank(line))
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence) and not line[marker.end() :].strip():
                fence = ""
        elif marker:
            fence = marker[1]
            lines.append(blank(line))
        else:
            lines.append(blank(line) if line.startswith(("    ", "\t")) else line)
    return re.sub(r"(`+)(?!`)([\s\S]*?)(?<!`)\1(?!`)", lambda match: blank(match[0]), "".join(lines))


FORMAT_RULES = (
    "Формат sections смешанный Markdown/HTML; это точный сохранённый исходник. "
    "Не преобразуй исходник и before перед цитированием. В Markdown используй Markdown, "
    "внутри HTML-текста — HTML-разметку (strong/em/br); для нового блока замени целый исходный HTML-блок. "
    "Не вставляй Markdown-таблицы, списки или fenced-блоки внутрь HTML-абзаца. "
    "Диаграммы задавай кодом Mermaid/PlantUML: fenced-блок с языком в Markdown или pre/code с language-mermaid/plantuml в HTML. "
    "Не заменяй код диаграмм SVG, картинками или служебной разметкой интерфейса. "
    "Внутри pre/code сохраняй буквальный код, пробелы и переводы строк. "
    "Экранируй JSON ровно один раз: перевод строки в строке JSON — \\n, не буквальные символы \\\\n. "
)


class _HtmlText(HTMLParser):
    def __init__(self, source):
        super().__init__(convert_charrefs=False)
        self.offsets = [0, *(match.end() for match in re.finditer("\n", source))]
        self.stack, self.spans = [], []
        self.feed(_without_markdown_code(source))

    def handle_starttag(self, tag, attrs):
        if tag not in _VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        pass

    def handle_endtag(self, tag):
        if tag in self.stack:
            del self.stack[len(self.stack) - 1 - self.stack[::-1].index(tag) :]

    def handle_data(self, data):
        if not set(self.stack) & _TEXT_CONTAINERS or set(self.stack) & {"pre", "code", "script", "style"}:
            return
        line, column = self.getpos()
        start = self.offsets[line - 1] + column
        self.spans.append((start, start + len(data)))


def format_text_edits(source, edits):
    """Render inline model Markdown only inside one existing HTML text node.

    Literal edit validation happens first. Attributes, code, cross-node edits,
    explicit HTML and all text outside the replacement retain their spelling.
    """
    spans, result = {}, []
    for edit in edits:
        body, replacement = source[edit["section"]], edit["after"]
        start = body.find(edit["before"])
        if edit["section"] not in spans:
            spans[edit["section"]] = _HtmlText(body).spans
        inside_text = any(left <= start and start + len(edit["before"]) <= right for left, right in spans[edit["section"]])
        if inside_text and re.search(r"[\r\n*_`~\[!]", replacement) and not re.search(r"</?[a-zA-Z][^>]*>", replacement):
            tokens = _MARKDOWN.inline(replacement, {})
            replacement = _MARKDOWN.renderer(tokens, mistune.BlockState())
        result.append({**edit, "after": replacement})
    return result
