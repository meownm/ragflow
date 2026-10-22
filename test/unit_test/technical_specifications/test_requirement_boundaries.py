"""Product acceptance boundaries using disposable storage and controlled model calls."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from api.db.db_models import DocumentExchangeBinding, SourceWorkspaceMember, TechnicalSpecification
from api.source_workbench.errors import SourceWorkspaceError
from api.technical_specifications.application import Specifications
from api.technical_specifications.domain import MAX_CONTENT, SpecificationError, empty_sections, markdown, parse_markdown, sections
from test.unit_test.technical_specifications.test_chat_requests import setup
from test.unit_test.technical_specifications.test_eva import AI, Eva, imported, run


@pytest.mark.parametrize("extra", [0, 1])
def test_aggregate_content_limit_is_exact_and_never_truncates(extra):
    content = empty_sections()
    content["1"] = "я" * (MAX_CONTENT - sum(map(len, content.values())) + extra)
    original = deepcopy(content)
    if extra:
        with pytest.raises(SpecificationError) as caught:
            sections(content)
        assert caught.value.code == "DOCUMENT_TOO_LARGE"
    else:
        assert sections(content) == original
        assert sum(map(len, parse_markdown(markdown(content)).values())) == MAX_CONTENT
    assert content == original


@pytest.mark.parametrize("body", ["```python\nunfinished", "~~~json\n{}", "````text\nbody\n```"])
def test_unclosed_code_fence_cannot_be_saved_or_imported(body):
    content = {**empty_sections(), "8": body}
    for validate, value in [(sections, content), (parse_markdown, markdown(content))]:
        with pytest.raises(SpecificationError) as caught:
            validate(value)
        assert caught.value.code == "INVALID_FORMAT"


@pytest.mark.parametrize("section", ["1", "5", "9"])
@pytest.mark.parametrize("body", ["\n\n  Начало\t\nконец  \n\n", '```json\n{"path":"C:\\temp\\reports","literal":"\\n"}\n```\n', "  строка\r\n\tследующая\r\n"])
def test_markdown_outline_framing_preserves_body_whitespace_and_code(section, body):
    content = {**empty_sections(), section: body}
    assert parse_markdown(markdown(content)) == content


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["failed", "expired"])
async def test_retry_after_document_edit_preserves_old_message_without_another_model_call(store, status):
    class FailingAI:
        calls = 0

        async def chat(self, *args):
            self.calls += 1
            raise TimeoutError("upstream disconnected")

    ai = FailingAI()
    service, doc, request = setup(store, ai)
    with pytest.raises(TimeoutError):
        await service.chat("author", "workspace", doc["id"], request)
    saved = store.get("author", "workspace", doc["id"])
    if status == "expired":
        saved = store.change(
            "author",
            "workspace",
            doc["id"],
            saved["version"],
            lambda value: value["messages"][0].update(status="processing", started_at=(datetime.now(UTC) - timedelta(minutes=7)).isoformat()),
            "expired-attempt",
        )
    saved = await service.edit("author", "workspace", doc["id"], {"version": saved["version"], "title": doc["title"], "sections": {**doc["sections"], "9": "Новая редакция"}})
    with pytest.raises(SpecificationError) as caught:
        await service.chat("author", "workspace", doc["id"], request)
    assert caught.value.code == "STALE_MESSAGE"
    assert ai.calls == 1
    assert store.get("author", "workspace", doc["id"]) == saved
    assert saved["messages"][0]["context"] == request["draft"]["context"]
    assert len(saved["messages"]) == 1 and saved["proposals"] == []


@pytest.mark.asyncio
async def test_unchanged_eva_pull_is_a_noop_without_a_proposal_or_revision(store):
    service, eva, ai, doc = await imported(store)
    assert await service.pull("author", "workspace", doc["id"], {"version": doc["version"]}) == doc
    assert store.get("author", "workspace", doc["id"]) == doc
    assert len(store.history("author", "workspace", doc["id"])) == 1
    assert eva.reads == 2 and ai.verifications == []


@pytest.mark.asyncio
@pytest.mark.parametrize("conflicting", [False, True])
async def test_repeated_eva_pull_reuses_pending_proposal_and_preserves_both_sources(store, conflicting):
    service, eva, _ai, doc = await imported(store)
    if conflicting:
        doc = await service.edit("author", "workspace", doc["id"], {"version": doc["version"], "title": doc["title"], "sections": {**doc["sections"], "9": "Локальный аудит"}})
    local = deepcopy(doc["sections"])
    eva.content["9"], eva.version = "Внешний аудит", "v2"
    remote = deepcopy(eva.content)
    first = await service.pull("author", "workspace", doc["id"], {"version": doc["version"]})
    repeated = await service.pull("author", "workspace", doc["id"], {"version": first["version"]})
    assert repeated == first and len(first["proposals"]) == 1
    proposal = first["proposals"][0]
    assert proposal["status"] == ("needs_resolution" if conflicting else "ready")
    assert proposal["remote"] == remote
    assert repeated["sections"] == local and repeated["revision"] == doc["revision"]
    assert eva.content == remote and repeated["eva"] == doc["eva"]


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["pull", "publish_eva"])
async def test_unbound_eva_operation_fails_before_external_access_or_local_writes(store, operation):
    eva = Eva()
    service = Specifications(store, AI(), run, eva)
    doc = await service.create("author", "workspace", {"title": "Локальное ТЗ"})
    with pytest.raises(SpecificationError) as caught:
        await getattr(service, operation)("author", "workspace", doc["id"], {"version": doc["version"]})
    assert caught.value.code == "EVA_NOT_BOUND"
    assert eva.reads == 0 and store.get("author", "workspace", doc["id"]) == doc


@pytest.mark.asyncio
async def test_connecting_an_already_bound_document_keeps_the_original_binding(store):
    service, _eva, _ai, doc = await imported(store)
    binding = DocumentExchangeBinding.get().__data__.copy()
    with pytest.raises(SpecificationError) as caught:
        await service.connect_eva("author", "workspace", doc["id"], {"version": doc["version"], "url": "https://eva.test/page"})
    assert caught.value.code == "EVA_ALREADY_BOUND"
    assert store.get("author", "workspace", doc["id"]) == doc
    assert DocumentExchangeBinding.select().count() == 1 and DocumentExchangeBinding.get().__data__ == binding


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["edit", "restore"])
@pytest.mark.parametrize("proposal_format", [None, "full", "compact"])
async def test_unchanged_edit_or_restore_does_not_create_a_revision_or_version(store, monkeypatch, operation, proposal_format):
    service = Specifications(store, AI(), run)
    doc = await service.create("author", "workspace", {"title": "Без изменений"})
    if proposal_format:
        candidate = {**doc["sections"], "5": "Неприменённое предложение"}
        doc = store.change(
            "author",
            "workspace",
            doc["id"],
            doc["version"],
            lambda value: value["proposals"].append({"id": "proposal", "source": "chat", "base_revision": 1, "sections": candidate, "status": "ready"}),
            "seed-proposal",
        )
        if proposal_format == "full":
            payload = deepcopy(TechnicalSpecification.get_by_id(doc["id"]).payload)
            payload["proposals"][0].pop("patches")
            payload["proposals"][0]["sections"] = candidate
            TechnicalSpecification.update(payload=payload).where(TechnicalSpecification.id == doc["id"]).execute()
    before = deepcopy(TechnicalSpecification.get_by_id(doc["id"]).__data__)
    revision_reads = []
    execute = store.database.execute_sql

    def capture(sql, *args, **kwargs):
        if sql.startswith("SELECT") and "technical_specification_revision" in sql:
            revision_reads.append(sql)
        return execute(sql, *args, **kwargs)

    monkeypatch.setattr(store.database, "execute_sql", capture)
    data = {"version": doc["version"], **({"title": doc["title"], "sections": doc["sections"]} if operation == "edit" else {"revision": doc["revision"]})}
    assert await getattr(service, operation)("author", "workspace", doc["id"], data) == doc
    # Restore reads its requested revision; compact proposals are hydrated once
    # for the mutation and once for the returned projection, regardless of count.
    assert len(revision_reads) == int(operation == "restore") + (2 if proposal_format == "compact" else 0)
    assert TechnicalSpecification.get_by_id(doc["id"]).__data__ == before
    assert store.get("author", "workspace", doc["id"]) == doc
    assert len(store.history("author", "workspace", doc["id"])) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["chat", "comment"])
@pytest.mark.parametrize("changed", ["context", "author"])
async def test_message_id_cannot_be_reused_with_another_context_or_author(store, operation, changed):
    SourceWorkspaceMember.create(workspace_id="workspace", user_id="editor", role="editor")

    class CountingAI:
        calls = 0

        async def chat(self, *args):
            self.calls += 1
            return {"answer": "Ответ без правок", "patches": {}}

    ai = CountingAI()
    service, doc, request = setup(store, ai)
    data = request if operation == "chat" else {"version": doc["version"], **request["draft"]}
    saved = await getattr(service, operation)("author", "workspace", doc["id"], data)
    data = deepcopy(data)
    data["version"] = saved["version"]
    if changed == "context":
        (data["draft"] if operation == "chat" else data)["context"]["scope"] = "5"
    with pytest.raises(SpecificationError) as caught:
        await getattr(service, operation)("editor" if changed == "author" else "author", "workspace", doc["id"], data)
    assert caught.value.code == "IDEMPOTENCY_KEY_REUSED"
    assert ai.calls == int(operation == "chat")
    assert store.get("author", "workspace", doc["id"]) == saved


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["chat", "apply"])
async def test_access_revoked_during_model_work_prevents_result_write(store, operation):
    SourceWorkspaceMember.create(workspace_id="workspace", user_id="editor", role="editor")

    class RevokingAI:
        def revoke(self):
            SourceWorkspaceMember.delete().where(SourceWorkspaceMember.user_id == "editor").execute()

        async def chat(self, *args):
            if operation == "chat":
                self.revoke()
            return {"answer": "Предложение", "patches": {"5": "Новый сценарий"}}

        async def verify(self, *args, **kwargs):
            self.revoke()
            return {"valid": True, "issues": []}

    service, doc, request = setup(store, RevokingAI())
    if operation == "apply":
        proposal = await service.chat("editor", "workspace", doc["id"], request)
        data = {"version": proposal["version"], "proposal_id": proposal["proposals"][0]["id"]}
    else:
        data = request
    with pytest.raises(SourceWorkspaceError):
        await getattr(service, operation)("editor", "workspace", doc["id"], data)
    saved = store.get("author", "workspace", doc["id"])
    assert saved["sections"] == doc["sections"] and saved["revision"] == doc["revision"]
    assert saved["messages"][0]["context"] == request["draft"]["context"]
    if operation == "chat":
        assert len(saved["messages"]) == 1 and saved["proposals"] == []
    else:
        assert saved == proposal
