"""Shared Eva page reads; connection policy and content decoding are injected.

No document lifecycle or local persistence belongs here. The connection context
owns credentials, error translation and resource lifetime for every read.
"""

from urllib.parse import unquote, urlparse

from .content import content_hash


class EvaPageError(Exception):
    def __init__(self, code, message, status=422):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


class EvaPages:
    def __init__(self, connection, decode_html, origin):
        self.connection, self.decode_html, self.origin = connection, decode_html, origin

    def search_sources(self, actor_id, query="", limit=20):
        if not isinstance(query, str) or type(limit) is not int or not 1 <= limit <= 100:
            raise EvaPageError("INVALID_EVA_SEARCH", "Укажите запрос до 500 символов и лимит от 1 до 100")
        if query.strip().casefold().startswith(("http://", "https://")):
            binding = self.resolve_page_url(actor_id, query.strip())
            return {
                "items": [
                    {**binding, "id": binding["document_id"], "name": binding["document_name"], "code": binding["document_code"], "version": binding["remote_version"], "web_url": binding["page_url"]}
                ],
                "connectors": [],
            }
        if len(query) > 500:
            raise EvaPageError("INVALID_EVA_SEARCH", "Поисковый запрос превышает 500 символов")
        with self.connection(actor_id) as (connector, client):
            documents = client.search_documents(query.strip(), limit)
            return {
                "items": [{**item, "connector_id": connector.id, "connector_name": connector.name, "eva_origin": self.origin(client.api_base_url)} for item in documents],
                "connectors": [{"connector_id": connector.id, "connector_name": connector.name}],
            }

    def find_title_matches(self, actor_id, title):
        with self.connection(actor_id) as (connector, client):
            return [{**item, "connector_id": connector.id, "connector_name": connector.name, "eva_origin": self.origin(client.api_base_url)} for item in client.find_documents_by_exact_title(title)]

    @staticmethod
    def link(page_url):
        if not isinstance(page_url, str) or not page_url.strip() or len(page_url) > 2048:
            raise EvaPageError("INVALID_EVA_PAGE_URL", "Укажите абсолютный HTTP(S) адрес страницы Eva")
        try:
            parsed = urlparse(page_url.strip())
            if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError("Invalid URL")
            _ = parsed.port
        except ValueError as error:
            raise EvaPageError("INVALID_EVA_PAGE_URL", "Укажите абсолютный HTTP(S) адрес страницы Eva") from error
        marker = "/project/Document/"
        index = parsed.path.casefold().find(marker.casefold())
        code = unquote(parsed.path[index + len(marker) :].strip("/")) if index >= 0 else ""
        return {
            "page_url": parsed._replace(query="", fragment="").geturl().rstrip("/"),
            "status": "LINK_ONLY",
            "capabilities": ["OPEN"],
            "connector_id": None,
            "project_id": None,
            "document_id": None,
            "document_code": code or None,
            "document_name": None,
            "remote_version": None,
            "remote_content_hash": None,
            "last_pulled_content_hash": None,
        }

    def resolve_page_url(self, actor_id, page_url):
        binding = self.link(page_url)
        with self.connection(actor_id) as (connector, client):
            origins = {self.origin(url) for url in (client.web_base_url, client.api_base_url) if url}
            if self.origin(page_url) not in origins:
                raise EvaPageError("EVA_PAGE_UNAVAILABLE", "Страница находится вне подключённого пространства Eva", 409)
            code = binding["document_code"]
            candidates = client.search_documents(code, 100) if code else []
            source = next(
                (
                    item
                    for item in candidates
                    if str(item.get("web_url") or "").rstrip("/").casefold() == binding["page_url"].casefold() or (code and str(item.get("code") or "").casefold() == code.casefold())
                ),
                None,
            )
            if source is None:
                raise EvaPageError("EVA_PAGE_UNAVAILABLE", "Страница Eva не найдена или недоступна", 409)
            remote = client.get_document_for_edit(str(source["id"]))
            return self._binding(connector.id, client, binding, remote, self.decode_html(str(remote.get("html") or "")))

    def read_connected_page(self, actor_id, binding):
        connector_id, document_id = binding.get("connector_id"), binding.get("document_id")
        if not isinstance(connector_id, str) or not connector_id or not isinstance(document_id, str) or not document_id:
            raise EvaPageError("EVA_PAGE_UNAVAILABLE", "Не удалось подтвердить страницу Eva", 409)
        with self.connection(actor_id, connector_id) as (_connector, client):
            remote = client.get_document_for_edit(document_id)
            markdown = self.decode_html(str(remote.get("html") or ""))
            return self._binding(connector_id, client, binding, remote, markdown), markdown

    def _binding(self, connector_id, client, binding, remote, markdown):
        return {
            **binding,
            "connector_id": connector_id,
            "eva_origin": self.origin(client.api_base_url),
            "page_url": str(remote.get("web_url") or binding.get("page_url") or ""),
            "status": "CONNECTED",
            "capabilities": ["OPEN", "PULL_FROM_EVA", "CREATE_EVA_CHANGE"],
            "project_id": str(remote.get("project_id") or binding.get("project_id") or "") or None,
            "document_id": str(remote.get("id") or binding.get("document_id") or ""),
            "document_code": str(remote.get("code") or binding.get("document_code") or "") or None,
            "document_name": str(remote.get("name") or binding.get("document_name") or "") or None,
            "remote_version": str(remote.get("version") or "") or None,
            "remote_content_hash": content_hash(markdown),
        }
