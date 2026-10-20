import asyncio

import pytest

from api.document_exchange.content import content_hash
from api.source_workbench.errors import SourceWorkspaceError
from api.technical_specifications.application import Specifications
from api.technical_specifications.domain import SpecificationError, markdown, parse_markdown
from test.unit_test.technical_specifications.test_eva import AI, Eva, run


class PublishingEva(Eva):
    def __init__(self):
        super().__init__()
        self.name, self.writes, self.calls = "Загрузка", 0, 0
        self.lose_response = False

    def read_connected_page(self, actor, binding):
        binding, source = super().read_connected_page(actor, binding)
        return {**binding, "document_name": self.name, "remote_content_hash": content_hash(source)}, source

    def publish_connected_page(self, actor, binding, source, name):
        self.calls += 1
        if source != markdown(self.content) or name != self.name:
            self.writes += 1
            self.content, self.name, self.version = parse_markdown(source), name, "published"
            if self.lose_response:
                self.lose_response = False
                raise TimeoutError("Publication response lost")
        return self.read_connected_page(actor, binding)[0]


async def imported(store):
    eva = PublishingEva()
    service = Specifications(store, AI(), run, eva)
    doc = await service.import_eva("author", "workspace", {"url": "https://eva.test/page"})
    return service, eva, doc


@pytest.mark.asyncio
async def test_publish_updates_binding_without_a_local_content_revision(store):
    service, eva, doc = await imported(store)
    content = {**doc["sections"], "9": "Новые требования к журналу"}
    doc = await service.edit("author", "workspace", doc["id"], {"version": doc["version"], "title": "Новое название", "sections": content})
    result = await service.publish_eva("author", "workspace", doc["id"], {"version": doc["version"]})
    assert eva.writes == 1 and eva.content == content and eva.name == "Новое название"
    assert result["eva"]["document_id"] == "page" and result["eva"]["base"] == content
    assert result["eva_export"]["status"] == "published" and result["eva_export"]["revision"] == doc["revision"]
    assert result["revision"] == doc["revision"]
    assert len(store.history("author", "workspace", doc["id"])) == 2


@pytest.mark.asyncio
async def test_lost_response_can_be_rechecked_without_another_external_write(store):
    service, eva, doc = await imported(store)
    doc = await service.edit("author", "workspace", doc["id"], {"version": doc["version"], "title": doc["title"], "sections": {**doc["sections"], "9": "Новое"}})
    eva.lose_response = True
    with pytest.raises(TimeoutError):
        await service.publish_eva("author", "workspace", doc["id"], {"version": doc["version"]})
    failed = await service.get("author", "workspace", doc["id"])
    assert failed["eva_export"]["status"] == "failed"
    result = await service.publish_eva("author", "workspace", doc["id"], {"version": failed["version"]})
    assert result["eva_export"]["status"] == "published"
    assert eva.writes == 1 and eva.calls == 2


@pytest.mark.asyncio
async def test_busy_export_rejects_second_request_and_keeps_concurrent_local_edit(store):
    service, eva, doc = await imported(store)
    accepted, proceed = asyncio.Event(), asyncio.Event()

    async def pause(method, *args, **kwargs):
        if method == eva.publish_connected_page:
            accepted.set()
            await proceed.wait()
        return method(*args, **kwargs)

    service.run = pause
    first = asyncio.create_task(service.publish_eva("author", "workspace", doc["id"], {"version": doc["version"]}))
    await asyncio.wait_for(accepted.wait(), 2)
    current = await service.get("author", "workspace", doc["id"])
    with pytest.raises(SpecificationError, match="Обновление Eva ещё выполняется"):
        await service.publish_eva("author", "workspace", doc["id"], {"version": current["version"]})
    newer = {**current["sections"], "9": "Более новая локальная правка"}
    edited = await service.edit("author", "workspace", doc["id"], {"version": current["version"], "title": "Новое локальное название", "sections": newer})
    proceed.set()
    result = await first
    assert result["sections"] == newer and result["title"] == edited["title"]
    assert result["eva"]["base"] == doc["sections"]
    assert result["eva_export"]["revision"] < result["revision"]


@pytest.mark.asyncio
async def test_canceling_request_keeps_external_attempt_busy(store):
    service, eva, doc = await imported(store)
    accepted = asyncio.Event()

    async def pause(method, *args, **kwargs):
        if method == eva.publish_connected_page:
            accepted.set()
            await asyncio.Event().wait()
        return method(*args, **kwargs)

    service.run = pause
    task = asyncio.create_task(service.publish_eva("author", "workspace", doc["id"], {"version": doc["version"]}))
    await asyncio.wait_for(accepted.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert store.get("author", "workspace", doc["id"])["eva_export"]["status"] == "publishing"
    assert eva.calls == 0


@pytest.mark.asyncio
async def test_stale_version_and_unauthorized_actor_never_publish(store):
    service, eva, doc = await imported(store)
    with pytest.raises(SpecificationError):
        await service.publish_eva("author", "workspace", doc["id"], {"version": 0})
    for actor, status in (("reader", 403), ("other", 404)):
        with pytest.raises(SourceWorkspaceError) as denied:
            await service.publish_eva(actor, "workspace", doc["id"], {"version": doc["version"]})
        assert denied.value.status == status
    assert eva.calls == 0


@pytest.mark.asyncio
async def test_link_existing_page_preserves_local_text_and_reimport_finds_it(store):
    eva = PublishingEva()
    service = Specifications(store, AI(), run, eva)
    local = await service.create("author", "workspace", {"title": "Локальное ТЗ"})
    linked = await service.connect_eva("author", "workspace", local["id"], {"version": local["version"], "url": "https://eva.test/page"})
    assert linked["title"] == local["title"] and linked["sections"] == local["sections"]
    assert linked["revision"] == local["revision"] and linked["eva"]["base"] == eva.content
    assert eva.writes == 0
    reopened = await service.import_eva("author", "workspace", {"url": "https://eva.test/page"})
    assert reopened["id"] == local["id"]


@pytest.mark.asyncio
async def test_occupied_page_cannot_be_linked_and_changes_nothing(store):
    service, _eva, _original = await imported(store)
    local = await service.create("author", "workspace", {"title": "Другое ТЗ"})
    with pytest.raises(SpecificationError) as caught:
        await service.connect_eva("author", "workspace", local["id"], {"version": local["version"], "url": "https://eva.test/page"})
    assert caught.value.code == "EVA_BINDING_EXISTS"
    assert store.get("author", "workspace", local["id"]) == local
