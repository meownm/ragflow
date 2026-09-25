"""User-owned source sets backed by RAGFlow's document search."""

from __future__ import annotations

import asyncio
from typing import Any, Protocol


MAX_DATASETS = 20
MAX_DOCUMENTS = 100
MAX_QUERY_LENGTH = 500


class SourceWorkspaceError(Exception):
    def __init__(self, code: str, message: str, status: int = 422):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _nonempty_string(value: Any, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not (cleaned := value.strip()) or len(cleaned) > maximum:
        raise SourceWorkspaceError("INVALID_INPUT", f"{field} must contain 1 to {maximum} characters")
    return cleaned


def _dataset_ids(value: Any) -> list[str]:
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_DATASETS:
        raise SourceWorkspaceError("INVALID_DATASETS", f"Select 1 to {MAX_DATASETS} datasets")
    ids = [_nonempty_string(item, "dataset_id", 256) for item in value]
    if len(ids) != len(set(ids)):
        raise SourceWorkspaceError("INVALID_DATASETS", "Dataset IDs must be unique")
    return ids


def _selected_documents(value: Any, allowed_datasets: list[str]) -> list[dict[str, str]]:
    if not isinstance(value, list) or len(value) > MAX_DOCUMENTS:
        raise SourceWorkspaceError("INVALID_DOCUMENTS", f"Select at most {MAX_DOCUMENTS} documents")
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in value:
        if not isinstance(item, dict) or not {"dataset_id", "document_id"}.issubset(item) or set(item) - {"dataset_id", "document_id", "revision"}:
            raise SourceWorkspaceError("INVALID_DOCUMENTS", "Each selection needs dataset_id and document_id")
        dataset_id = _nonempty_string(item["dataset_id"], "dataset_id", 256)
        document_id = _nonempty_string(item["document_id"], "document_id", 32)
        if dataset_id not in allowed_datasets or (dataset_id, document_id) in seen:
            raise SourceWorkspaceError("INVALID_DOCUMENTS", "Selection contains an unrelated or duplicate document")
        seen.add((dataset_id, document_id))
        result.append({"dataset_id": dataset_id, "document_id": document_id})
    return result


class WorkspaceRepository(Protocol):
    def create(self, owner_id: str, title: str, dataset_ids: list[str]) -> dict[str, Any]: ...
    def list(self, owner_id: str) -> list[dict[str, Any]]: ...
    def get(self, owner_id: str, workspace_id: str) -> dict[str, Any]: ...
    def replace_selection(self, owner_id: str, workspace_id: str, version: int, documents: list[dict[str, str]]) -> dict[str, Any]: ...
    def add_query(self, owner_id: str, workspace_id: str, query: str) -> None: ...


class SourceGateway(Protocol):
    def validate_datasets(self, owner_id: str, dataset_ids: list[str]) -> None: ...
    def document_revisions(self, documents: list[dict[str, str]]) -> list[dict[str, str]]: ...
    def describe_chunks(self, chunks: list[dict[str, Any]], dataset_ids: list[str]) -> list[dict[str, Any]]: ...
    def load_document(self, document: dict[str, str]) -> str: ...
    async def search(self, owner_id: str, dataset_ids: list[str], query: str, document_ids: list[str] | None = None, page: int = 1) -> list[dict[str, Any]]: ...


class AnswerGateway(Protocol):
    async def answer(self, owner_id: str, question: str, previous_question: str, evidence: list[dict[str, str]]) -> str: ...


class SourceWorkspaceService:
    def __init__(
        self,
        repository: WorkspaceRepository,
        gateway: SourceGateway,
        answer_gateway: AnswerGateway | None = None,
    ):
        self.repository = repository
        self.gateway = gateway
        self.answer_gateway = answer_gateway

    async def create(self, owner_id: str, data: dict[str, Any]) -> dict[str, Any]:
        title = _nonempty_string(data.get("title"), "title", 255)
        dataset_ids = _dataset_ids(data.get("dataset_ids"))
        await asyncio.to_thread(self.gateway.validate_datasets, owner_id, dataset_ids)
        return await asyncio.to_thread(self.repository.create, owner_id, title, dataset_ids)

    async def list(self, owner_id: str) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self.repository.list, owner_id)

    async def get(self, owner_id: str, workspace_id: str) -> dict[str, Any]:
        workspace = await asyncio.to_thread(self.repository.get, owner_id, workspace_id)
        selected = workspace["selected_documents"]
        workspace["selected_sources"] = []
        if selected:
            try:
                await asyncio.to_thread(self.gateway.validate_datasets, owner_id, workspace["dataset_ids"])
            except SourceWorkspaceError:
                pass
            else:
                workspace["selected_sources"] = await asyncio.to_thread(
                    self.gateway.describe_chunks,
                    [{"dataset_id": item["dataset_id"], "document_id": item["document_id"]} for item in selected],
                    workspace["dataset_ids"],
                )
        return workspace

    async def search(self, owner_id: str, workspace_id: str, query: str, page: int = 1) -> dict[str, Any]:
        query = _nonempty_string(query, "query", MAX_QUERY_LENGTH)
        if type(page) is not int or not 1 <= page <= 10:
            raise SourceWorkspaceError("INVALID_PAGE", "Search page must be between 1 and 10")
        workspace = await self.get(owner_id, workspace_id)
        await asyncio.to_thread(self.gateway.validate_datasets, owner_id, workspace["dataset_ids"])
        chunks = await self.gateway.search(owner_id, workspace["dataset_ids"], query, page=page)
        candidates = await asyncio.to_thread(self.gateway.describe_chunks, chunks, workspace["dataset_ids"])
        if page == 1:
            await asyncio.to_thread(self.repository.add_query, owner_id, workspace_id, query)
        # RAGFlow may fold many child hits into one parent after retrieval, so
        # the returned count cannot tell whether this was the final raw page.
        return {"query": query, "page": page, "has_more": bool(chunks) and page < 10, "candidates": candidates}

    async def select(self, owner_id: str, workspace_id: str, data: dict[str, Any]) -> dict[str, Any]:
        workspace = await self.get(owner_id, workspace_id)
        version = data.get("expected_version")
        if type(version) is not int or version != workspace["version"]:
            raise SourceWorkspaceError("VERSION_CONFLICT", "Source selection changed; reload it before saving", 409)
        documents = _selected_documents(data.get("selected_documents"), workspace["dataset_ids"])
        await asyncio.to_thread(self.gateway.validate_datasets, owner_id, workspace["dataset_ids"])
        pinned = await asyncio.to_thread(self.gateway.document_revisions, documents)
        await asyncio.to_thread(self.repository.replace_selection, owner_id, workspace_id, version, pinned)
        return await self.get(owner_id, workspace_id)

    async def _selection(self, owner_id: str, workspace_id: str, expected_version: int | None) -> tuple[dict[str, Any], list[dict[str, str]]]:
        workspace = await asyncio.to_thread(self.repository.get, owner_id, workspace_id)
        if expected_version is not None and (type(expected_version) is not int or expected_version != workspace["version"]):
            raise SourceWorkspaceError("VERSION_CONFLICT", "Source selection changed; reload it before continuing", 409)
        selected = workspace["selected_documents"]
        documents = _selected_documents(selected, workspace["dataset_ids"])
        if not documents:
            raise SourceWorkspaceError("EMPTY_SELECTION", "Select documents before asking a question")
        await asyncio.to_thread(self.gateway.validate_datasets, owner_id, workspace["dataset_ids"])
        current = await asyncio.to_thread(self.gateway.document_revisions, documents)
        if any(item.get("revision") != snapshot.get("revision") for item, snapshot in zip(current, selected, strict=True)):
            raise SourceWorkspaceError("SOURCE_CHANGED", "Одна из выбранных статей изменилась. Обновите подборку.", 409)
        return workspace, current

    async def retrieve(self, owner_id: str, workspace_id: str, query: str, expected_version: int | None = None) -> dict[str, Any]:
        query = _nonempty_string(query, "query", MAX_QUERY_LENGTH)
        workspace, documents = await self._selection(owner_id, workspace_id, expected_version)
        chunks = await self.gateway.search(owner_id, workspace["dataset_ids"], query, [item["document_id"] for item in documents])
        await self._selection(owner_id, workspace_id, workspace["version"])
        return {"workspace_id": workspace_id, "version": workspace["version"], "chunks": chunks}

    async def load_selected_documents(self, owner_id: str, workspace_id: str, expected_version: int) -> dict[str, Any]:
        """Load full indexed text for a downstream workflow, with a pinned selection."""
        if type(expected_version) is not int:
            raise SourceWorkspaceError("VERSION_CONFLICT", "Selection version is required", 409)
        workspace, documents = await self._selection(owner_id, workspace_id, expected_version)
        sources = await asyncio.to_thread(self.gateway.describe_chunks, documents, workspace["dataset_ids"])
        by_key = {(source["dataset_id"], source["document_id"]): source for source in sources}
        if any((document["dataset_id"], document["document_id"]) not in by_key for document in documents):
            raise SourceWorkspaceError("SOURCE_CHANGED", "Одна из выбранных статей больше недоступна.", 409)
        loaded = []
        total_characters = 0
        for document in documents:
            text = await asyncio.to_thread(self.gateway.load_document, document)
            total_characters += len(text)
            if total_characters > 10_000_000:
                raise SourceWorkspaceError("SELECTION_TOO_LARGE", "Selected documents exceed the 10 million character workflow limit", 413)
            loaded.append({**document, "source": by_key[(document["dataset_id"], document["document_id"])], "text": text})
        await self._selection(owner_id, workspace_id, workspace["version"])
        return {"workspace_id": workspace_id, "version": workspace["version"], "documents": loaded}

    async def chat(self, owner_id: str, workspace_id: str, data: dict[str, Any]) -> dict[str, Any]:
        question = _nonempty_string(data.get("question"), "question", MAX_QUERY_LENGTH)
        expected_version = data.get("expected_version")
        if type(expected_version) is not int:
            raise SourceWorkspaceError("VERSION_CONFLICT", "Source selection changed; start a new chat", 409)
        previous_question = data.get("previous_question") or ""
        if previous_question:
            previous_question = _nonempty_string(previous_question, "previous_question", MAX_QUERY_LENGTH)
        search_query = f"{previous_question[-200:]} {question}".strip() if previous_question and len(question) < 100 else question
        retrieved = await self.retrieve(owner_id, workspace_id, search_query, expected_version)
        chunks = retrieved["chunks"][:12]
        if not chunks:
            return {"answer": "В выбранных статьях не нашлось фрагментов для ответа.", "sources": [], "version": retrieved["version"]}
        workspace = await asyncio.to_thread(self.repository.get, owner_id, workspace_id)
        if workspace["version"] != retrieved["version"]:
            raise SourceWorkspaceError("VERSION_CONFLICT", "Source selection changed; start a new chat", 409)
        candidates = await asyncio.to_thread(self.gateway.describe_chunks, chunks, workspace["dataset_ids"])
        evidence = []
        for index, chunk in enumerate(chunks, start=1):
            content = str(chunk.get("content_with_weight") or chunk.get("content") or "").strip()
            if content:
                evidence.append({"number": str(index), "document_id": str(chunk.get("document_id") or chunk.get("doc_id")), "text": content[:1600]})
        if not evidence:
            return {"answer": "В выбранных статьях не нашлось текста для ответа.", "sources": [], "version": retrieved["version"]}
        citations: dict[str, list[int]] = {}
        for item in evidence:
            citations.setdefault(item["document_id"], []).append(int(item["number"]))
        candidates = [candidate for candidate in candidates if candidate["document_id"] in citations]
        for candidate in candidates:
            candidate["citation_numbers"] = citations[candidate["document_id"]]
        if self.answer_gateway is None:
            raise SourceWorkspaceError("MODEL_UNAVAILABLE", "Chat model is unavailable", 503)
        answer = await self.answer_gateway.answer(owner_id, question, previous_question, evidence)
        return {"answer": answer, "sources": candidates, "version": retrieved["version"]}
