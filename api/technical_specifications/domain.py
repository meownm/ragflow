"""Value-only format, context and revision rules for technical specifications."""

import json
import re
from copy import deepcopy
from hashlib import sha256
from uuid import uuid4

from api.document_exchange.identity import binding_keys
from api.document_exchange.merge import merge_text

HEADINGS = (
    "Назначение и цель",
    "Общие сведения, термины, сокращения и ссылки",
    "Компоненты подлежащие доработки с описанием изменений",
    "Конфигурирование (настройки)",
    "Прототипы интерфейсов и сценарии использования",
    "Логика работы",
    "Модель данных",
    "Сигнатура взаимодействия",
    "Журналирование",
)
MAX_CONTENT = 100_000


class SpecificationError(Exception):
    def __init__(self, code, message, status=422, details=None):
        super().__init__(message)
        self.code, self.message, self.status, self.details = code, message, status, details


def fail(code, message, status=422, details=None):
    raise SpecificationError(code, message, status, details)


def scoped_chat_result(result, context):
    """Reject undeclared patches and turn an explicit scope mismatch into advice."""
    required, patches = result.get("required_sections"), result.get("patches", {})
    keys = {str(index) for index in range(1, len(HEADINGS) + 1)}
    if not isinstance(required, list) or len(required) > len(keys) or any(not isinstance(key, str) or key not in keys for key in required):
        fail("MODEL_INVALID_OUTPUT", "Помощник не указал необходимые разделы", 502)
    if not isinstance(patches, dict) or set(patches) - set(required):
        fail("MODEL_INVALID_OUTPUT", "Правки не совпадают с необходимыми разделами", 502)
    scope = context["scope"]
    allowed = keys if scope == "all" else {context["anchor"]["section"] if scope == "selection" else scope}
    if set(required) - allowed:
        return {"answer": "Правка требует изменений за пределами выбранного контекста. Выберите «Всё ТЗ» и отправьте запрос снова.", "patches": {}}
    return {"answer": result.get("answer"), "patches": patches}


def identifier():
    return uuid4().hex


def text(value, label, limit=MAX_CONTENT, empty=False):
    if not isinstance(value, str) or len(value) > limit or (not empty and not value.strip()):
        fail("INVALID_INPUT", f"Некорректное поле «{label}»")
    return value


def version(value):
    if type(value) is not int or value < 1:
        fail("VERSION_CONFLICT", "Нужна текущая версия", 409)
    return value


def digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def eva_identity(binding):
    origin = binding.get("eva_origin")
    if not isinstance(origin, (list, tuple)) or len(origin) != 3:
        fail("EVA_PAGE_UNAVAILABLE", "Не удалось подтвердить страницу в подключении Eva. Проверьте адрес и личный токен", 409)
    scheme, host, port = origin
    if scheme not in ("http", "https") or not isinstance(host, str) or not host or (port is not None and (type(port) is not int or not 0 < port < 65536)):
        fail("EVA_PAGE_UNAVAILABLE", "Не удалось подтвердить адрес источника Eva", 409)
    text(binding.get("project_id"), "Пространство Eva", 128)
    text(binding.get("document_id"), "Страница Eva", 128)
    return binding_keys(binding)[1]


def empty_sections():
    result = {str(i): "" for i in range(1, 10)}
    result["4"] = "### 4.1. Конфигурации сервисов (дополнительные)\n\n### 4.2. Права, ограничения и управление правами\n"
    return result


def sections(value):
    if not isinstance(value, dict) or set(value) != {str(i) for i in range(1, 10)}:
        fail("INVALID_FORMAT", "ТЗ должно содержать ровно девять разделов")
    for content in value.values():
        text(content, "Содержимое раздела", empty=True)
        fence = ""
        for line in content.splitlines():
            marker = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
            if marker:
                token = marker.group(1)
                if not fence:
                    fence = token
                elif token[0] == fence[0] and len(token) >= len(fence) and not line[marker.end() :].strip():
                    fence = ""
            elif not fence and re.match(r"^ {0,3}#{1,2}\s", line):
                fail("INVALID_FORMAT", "Внутри раздела используйте заголовки третьего уровня и ниже")
        if fence:
            fail("INVALID_FORMAT", "Закройте блок кода внутри раздела")
    if sum(map(len, value.values())) > MAX_CONTENT:
        fail("DOCUMENT_TOO_LARGE", "ТЗ превышает 100 000 символов")
    return deepcopy(value)


def markdown(value):
    return "\n\n".join(f"## {i}. {title}\n\n{value[str(i)]}" for i, title in enumerate(HEADINGS, 1))


def parse_markdown(value):
    """Import only the documented outline. Never silently discard a preamble."""
    text(value, "Текст Eva", MAX_CONTENT + 2000)
    result, current, fence = {}, None, ""
    for line in value.splitlines(keepends=True):
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if marker:
            token = marker.group(1)
            if not fence:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence) and not line[marker.end() :].strip():
                fence = ""
        heading = re.match(r"^ {0,3}#{1,2}\s+([1-9])[.)]\s+(.+?)\s*$", line) if not fence else None
        if heading:
            key = heading.group(1)
            if key in result:
                fail("INVALID_FORMAT", "В Eva повторяется номер раздела ТЗ")
            if key != str(len(result) + 1):
                fail("INVALID_FORMAT", "Нарушен порядок разделов ТЗ")
            # Headings may differ in punctuation, but not in their meaning.
            normalize = lambda s: re.sub(r"\W+", "", s.casefold())
            if normalize(heading.group(2)) != normalize(HEADINGS[int(key) - 1]):
                fail("INVALID_FORMAT", f"Проверьте название раздела {key}")
            result[key], current = "", key
        elif current:
            result[current] += line
        elif line.strip():
            fail("INVALID_FORMAT", "Перед первым разделом Eva есть текст: перенесите его в раздел ТЗ")
    # Remove only the outline's framing newlines, preserving body whitespace.
    for key, content in result.items():
        content = content.removeprefix("\n")
        if key != "9" and content.endswith("\n\n"):
            content = content[:-2]
        result[key] = content
    return sections(result)


def merge_sections(base, local, remote):
    for value in (base, local, remote):
        sections(value)
    candidate, conflicts = {}, []
    for key in base:
        result = merge_text(base[key], local[key], remote[key])
        if result["conflict"]:
            conflicts.append({"section": key, **result})
            candidate[key] = local[key]
        else:
            candidate[key] = result["text"]
    return {"sections": candidate, "conflicts": conflicts}


def context(value, document_id):
    if not isinstance(value, dict) or set(value) - {"document_id", "scope", "anchor", "revision"}:
        fail("INVALID_CONTEXT", "Некорректный контекст сообщения")
    if value.get("document_id") != document_id:
        fail("INVALID_CONTEXT", "Контекст относится к другому ТЗ")
    scope = value.get("scope")
    if not isinstance(scope, str) or scope not in {"all", "selection", *map(str, range(1, 10))}:
        fail("INVALID_CONTEXT", "Выберите контекст сообщения")
    version(value.get("revision"))
    anchor = value.get("anchor")
    if scope == "selection":
        if not isinstance(anchor, dict) or set(anchor) != {"section", "quote", "prefix", "suffix"}:
            fail("INVALID_CONTEXT", "Сохраните цитату вместе с окружением")
        if not isinstance(anchor["section"], str) or anchor["section"] not in set(map(str, range(1, 10))):
            fail("INVALID_CONTEXT", "Не найден раздел цитаты")
        text(anchor["quote"], "Цитата", 10_000)
        for key in ("prefix", "suffix"):
            text(anchor[key], "Окружение цитаты", 64, empty=True)
    elif anchor is not None:
        fail("INVALID_CONTEXT", "Цитата допустима только для выделенного фрагмента")
    return deepcopy(value)


def assert_context(value, current):
    if value["scope"] != "selection":
        return
    anchor = value["anchor"]
    content = current[anchor["section"]]
    full = anchor["prefix"] + anchor["quote"] + anchor["suffix"]
    first = content.find(full)
    if first < 0 or content.find(full, first + 1) >= 0:
        fail("STALE_CONTEXT", "Исходный фрагмент изменён или неоднозначен. Черновик сохранён; выберите контекст явно", 409)


def draft(value, document_id):
    if not isinstance(value, dict) or set(value) != {"text", "context", "message_id"}:
        fail("INVALID_INPUT", "Черновик должен содержать текст и полный контекст")
    return {"message_id": text(value["message_id"], "Идентификатор сообщения", 64), "text": text(value["text"], "Сообщение", 10_000, empty=True), "context": context(value["context"], document_id)}
