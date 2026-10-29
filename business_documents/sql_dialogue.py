"""Pure context identities and conservative discovery for the SQL dialogue."""

import re

from business_documents.sql_assistant import fingerprint


def context_identity(run_id, query_id, compilation_id, sql, parameters, timezone, view_revision):
    return {
        "run_id": run_id,
        "query_artifact_id": query_id,
        "compilation_id": compilation_id,
        "sql_hash": fingerprint(sql),
        "parameters_hash": fingerprint(parameters),
        "timezone": timezone,
        "view_revision": view_revision,
    }


def metric_matches(question, definitions):
    """Return candidates, never infer agreement from a lexical match."""
    stop = {"количество", "сумма", "показатель", "общий", "данные", "расчет", "расчёт", "по", "за", "для", "и"}

    def terms(value):
        return {word[:-2] if len(word) >= 6 else word for word in re.findall(r"[\w]+", value.casefold()) if len(word) >= 3 and word not in stop}

    words = terms(question)
    ranked = [(len(words & terms(item["title"])), item) for item in definitions if item["freshness"] == "CURRENT"]
    return [item for score, item in sorted(ranked, key=lambda pair: (-pair[0], pair[1]["title"], pair[1]["definition_id"])) if score][:10]


def reference_keys(raw):
    if not isinstance(raw, list) or len(raw) > 10:
        raise ValueError("Можно упомянуть не более десяти объектов")
    keys = []
    for item in raw:
        if not isinstance(item, dict) or item.get("kind") not in {"TABLE", "DEFINITION"}:
            raise ValueError("Неверное упоминание объекта")
        fields = {"kind", "id"} if item["kind"] == "TABLE" else {"kind", "id", "version", "content_hash"}
        if set(item) != fields or not isinstance(item["id"], str) or not item["id"] or len(item["id"]) > 128:
            raise ValueError("Неверное упоминание объекта")
        if item["kind"] == "DEFINITION" and (type(item["version"]) is not int or item["version"] < 1 or not isinstance(item["content_hash"], str)):
            raise ValueError("Неверная версия определения")
        if item in keys:
            raise ValueError("Объект упомянут повторно")
        keys.append(item)
    return keys
