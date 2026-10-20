import asyncio
from copy import deepcopy

import pytest

from api.technical_specifications.application import Specifications
from api.technical_specifications.domain import SpecificationError, empty_sections, markdown


async def run(method, *args, **kwargs):
    return method(*args, **kwargs)


class Eva:
    def __init__(self):
        self.content = empty_sections()
        self.content.update({"4": "Лимит: 20 файлов\nРазмер: 10 МБ", "5": "Выбрать до 20 файлов", "8": "Отклонить более 20 файлов"})
        self.version, self.reads = "v1", 0

    def resolve_page_url(self, actor, url):
        assert actor == "author"
        return {"connector_id": "connector", "eva_origin": ["https", "eva.test", 443], "project_id": "project", "document_id": "page", "page_url": url, "document_name": "Загрузка"}

    def read_connected_page(self, actor, binding):
        assert actor == "author"
        self.reads += 1
        return {**binding, "remote_version": self.version}, markdown(self.content)

    def search_sources(self, actor, query, limit):
        assert actor == "author" and limit == 20
        binding = self.resolve_page_url(actor, "https://eva.test/page")
        return {"items": [{**binding, "id": "page", "name": "Загрузка", "version": self.version}], "connectors": []}


class AI:
    def __init__(self):
        self.verifications = []

    async def verify(self, actor, before, after, merge=None):
        self.verifications.append((deepcopy(before), deepcopy(after), deepcopy(merge)))
        inconsistent = "35 файлов" in after["4"] and "50 файлов" in after["5"]
        return {"valid": not inconsistent, "issues": ["Разные лимиты в разделах 4 и 5"] if inconsistent else []}

    async def resolve_merge(self, actor, base, local, remote, choice):
        result = deepcopy(remote)
        if "40 МБ" in local["4"]:
            result["4"] = result["4"].replace("10 МБ", "40 МБ")
        if choice == "local":
            for key in ("4", "5", "8"):
                result[key] = result[key].replace("50 файлов", "35 файлов")
        return result


async def imported(store):
    eva, ai = Eva(), AI()
    service = Specifications(store, ai, run, eva)
    doc = await service.import_eva("author", "workspace", {"url": "https://eva.test/page"})
    return service, eva, ai, doc


@pytest.mark.asyncio
async def test_business_document_reservation_blocks_binding_but_allows_independent_copy(store):
    from api.db.db_models import DocumentExchangeBinding, TechnicalSpecification
    from api.document_exchange.bindings import reserve

    eva = Eva()
    with store.database.atomic():
        reserve("author", "business_requirements", "hidden-business-document", eva.resolve_page_url("author", "https://eva.test/page"))
    service = Specifications(store, AI(), run, eva)
    found = await service.eva_pages("author", "workspace", "Загрузка")
    assert found["items"][0]["binding_available"] is False
    assert found["items"][0]["linked_document"] is None
    with pytest.raises(SpecificationError) as caught:
        await service.import_eva("author", "workspace", {"url": "https://eva.test/page"})
    assert caught.value.code == "EVA_BINDING_EXISTS"
    assert "hidden-business-document" not in str(caught.value)
    assert TechnicalSpecification.select().count() == 0
    copy = await service.import_eva("author", "workspace", {"url": "https://eva.test/page", "copy": True})
    assert copy["eva"] is None and copy["origin"]["document_id"] == "page"
    assert DocumentExchangeBinding.select().count() == 1


@pytest.mark.asyncio
async def test_reimport_opens_existing_without_replacing_local_revision(store):
    service, eva, _ai, doc = await imported(store)
    edited = deepcopy(doc["sections"])
    edited["9"] = "Локальная правка"
    saved = await service.edit("author", "workspace", doc["id"], {"version": doc["version"], "title": doc["title"], "sections": edited})
    eva.content["9"] = "Внешняя правка"
    result = await service.import_eva("author", "workspace", {"url": "https://eva.test/page"})
    assert result == saved
    assert eva.reads == 1
    copied = await service.import_eva("author", "workspace", {"url": "https://eva.test/page", "copy": True})
    assert copied["eva"] is None and copied["id"] != doc["id"]
    assert copied["sections"]["9"] == "Внешняя правка"
    assert copied["origin"]["document_id"] == "page"
    assert copied["origin"]["remote_version"] == "v1"
    assert copied["origin"]["received_at"]


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["Загрузка", "https://eva.test/page?ignored=" + "x" * 600])
async def test_eva_search_marks_an_existing_document_without_reading_its_content(store, query):
    service, _eva, _ai, doc = await imported(store)
    found = await service.eva_pages("author", "sibling", query)
    assert found["items"][0]["linked_document"] == {"id": doc["id"], "workspace_id": "workspace"}
    assert found["items"][0]["binding_available"] is False


@pytest.mark.asyncio
async def test_eva_search_hides_an_inaccessible_linked_document(store):
    from api.db.db_models import SourceWorkspace, SourceWorkspaceMember, UserTenant

    SourceWorkspace.create(id="hidden", tenant_id="tenant", owner_id="stranger", title="Скрытый проект")
    UserTenant.create(id="membership", tenant_id="tenant", user_id="author", role="normal", invited_by="stranger")
    SourceWorkspace.update(tenant_id="tenant").where(SourceWorkspace.id == "workspace").execute()
    SourceWorkspaceMember.create(workspace_id="hidden", user_id="author", role="editor")
    service = Specifications(store, AI(), run, Eva())
    doc = await service.import_eva("author", "hidden", {"url": "https://eva.test/page"})
    SourceWorkspaceMember.delete().where(SourceWorkspaceMember.workspace_id == "hidden").execute()
    found = await service.eva_pages("author", "workspace", "Загрузка")
    item = found["items"][0]
    assert item["binding_available"] is False and item["linked_document"] is None
    assert doc["id"] not in str(found) and "Скрытый" not in str(found)


@pytest.mark.asyncio
async def test_eva_search_checks_edit_permission_before_external_access(store):
    from api.source_workbench.errors import SourceWorkspaceError

    service = Specifications(store, AI(), run, Eva())
    with pytest.raises(SourceWorkspaceError):
        await service.eva_pages("reader", "workspace", "Загрузка")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "selection",
    [
        {"id": "page", "project_id": "project", "version": "v0"},
        {"id": "other-page", "project_id": "project", "version": "v1"},
    ],
)
async def test_import_rejects_an_outdated_selection_without_creating_a_document(store, selection):
    service = Specifications(store, AI(), run, Eva())
    with pytest.raises(SpecificationError) as error:
        await service.import_eva("author", "workspace", {"url": "https://eva.test/page", "selection": selection})
    assert error.value.code == "EVA_CHANGED"
    assert store.list("author", "workspace") == []


@pytest.mark.asyncio
async def test_independent_eva_changes_merge_without_losing_local_size(store):
    service, eva, ai, doc = await imported(store)
    local = deepcopy(doc["sections"])
    local["4"] = local["4"].replace("10 МБ", "40 МБ")
    doc = await service.edit("author", "workspace", doc["id"], {"version": doc["version"], "title": doc["title"], "sections": local})
    for key in ("4", "5", "8"):
        eva.content[key] = eva.content[key].replace("20 файлов", "50 файлов")
    eva.version = "v2"
    pending = await service.pull("author", "workspace", doc["id"], {"version": doc["version"]})
    assert pending["sections"] == local
    proposal = pending["proposals"][-1]
    assert proposal["status"] == "ready" and not proposal["conflicts"]
    result = await service.apply("author", "workspace", doc["id"], {"version": pending["version"], "proposal_id": proposal["id"]})
    assert all("50 файлов" in result["sections"][key] for key in ("4", "5", "8"))
    assert "40 МБ" in result["sections"]["4"]
    assert result["eva"]["base"] == eva.content
    assert ai.verifications[-1][2]["remote"] == eva.content


@pytest.mark.asyncio
async def test_original_35_50_counterexample_requires_linked_resolution(store):
    service, eva, ai, doc = await imported(store)
    local = deepcopy(doc["sections"])
    local["4"] = local["4"].replace("20 файлов", "35 файлов").replace("10 МБ", "40 МБ")
    doc = await service.edit("author", "workspace", doc["id"], {"version": doc["version"], "title": doc["title"], "sections": local})
    for key in ("4", "5", "8"):
        eva.content[key] = eva.content[key].replace("20 файлов", "50 файлов")
    eva.version = "v2"
    pending = await service.pull("author", "workspace", doc["id"], {"version": doc["version"]})
    proposal = pending["proposals"][-1]
    assert proposal["status"] == "needs_resolution"
    with pytest.raises(SpecificationError) as error:
        await service.apply("author", "workspace", doc["id"], {"version": pending["version"], "proposal_id": proposal["id"]})
    assert error.value.code == "UNRESOLVED_CONFLICT"
    resolved = await service.resolve_eva("author", "workspace", doc["id"], {"version": pending["version"], "proposal_id": proposal["id"], "choice": "local"})
    result = await service.apply("author", "workspace", doc["id"], {"version": resolved["version"], "proposal_id": proposal["id"]})
    assert all("35 файлов" in result["sections"][key] for key in ("4", "5", "8"))
    assert "40 МБ" in result["sections"]["4"]
    assert all("50 файлов" in result["eva"]["base"][key] for key in ("4", "5", "8"))
    assert ai.verifications[-1][2]["choice"] == "local"


@pytest.mark.asyncio
async def test_eva_changed_after_preview_blocks_apply_and_preserves_local_content(store):
    service, eva, ai, doc = await imported(store)
    eva.content["9"], eva.version = "Новый журнал", "v2"
    pending = await service.pull("author", "workspace", doc["id"], {"version": doc["version"]})
    eva.content["9"], eva.version = "Ещё одна правка", "v3"
    with pytest.raises(SpecificationError) as error:
        await service.apply("author", "workspace", doc["id"], {"version": pending["version"], "proposal_id": pending["proposals"][-1]["id"]})
    assert error.value.code == "EVA_CHANGED"
    assert store.get("author", "workspace", doc["id"])["sections"] == doc["sections"]
    assert not ai.verifications


@pytest.mark.asyncio
async def test_eva_change_during_verification_is_checked_before_commit(store):
    service, eva, ai, doc = await imported(store)
    eva.content["9"], eva.version = "Журнал v2", "v2"
    pending = await service.pull("author", "workspace", doc["id"], {"version": doc["version"]})

    async def verify(*args, **kwargs):
        eva.content["9"], eva.version = "Журнал v3", "v3"
        return {"valid": True, "issues": []}

    ai.verify = verify
    with pytest.raises(SpecificationError) as error:
        await service.apply("author", "workspace", doc["id"], {"version": pending["version"], "proposal_id": pending["proposals"][-1]["id"]})
    assert error.value.code == "EVA_CHANGED"
    assert store.get("author", "workspace", doc["id"])["sections"] == doc["sections"]


@pytest.mark.asyncio
async def test_semantic_conflict_without_overlapping_lines_can_be_resolved(store):
    service, eva, _ai, doc = await imported(store)
    local = deepcopy(doc["sections"])
    local["4"] = local["4"].replace("20 файлов", "35 файлов")
    doc = await service.edit("author", "workspace", doc["id"], {"version": doc["version"], "title": doc["title"], "sections": local})
    for key in ("5", "8"):
        eva.content[key] = eva.content[key].replace("20 файлов", "50 файлов")
    eva.version = "v2"
    pending = await service.pull("author", "workspace", doc["id"], {"version": doc["version"]})
    proposal = pending["proposals"][-1]
    assert proposal["status"] == "ready" and not proposal["conflicts"]
    blocked = await service.apply("author", "workspace", doc["id"], {"version": pending["version"], "proposal_id": proposal["id"]})
    assert blocked["proposals"][-1]["status"] == "needs_resolution"
    assert blocked["proposals"][-1]["issues"]
    assert blocked["sections"] == local
    resolved = await service.resolve_eva("author", "workspace", doc["id"], {"version": blocked["version"], "proposal_id": proposal["id"], "choice": "local"})
    assert resolved["proposals"][-1]["status"] == "ready"
    assert resolved["proposals"][-1]["issues"] == []


@pytest.mark.asyncio
async def test_parallel_comment_during_apply_does_not_invalidate_text_revision(store):
    service, eva, ai, doc = await imported(store)
    eva.content["9"], eva.version = "Новый журнал", "v2"
    pending = await service.pull("author", "workspace", doc["id"], {"version": doc["version"]})

    async def verify(*args, **kwargs):
        current = store.get("author", "workspace", doc["id"])
        await service.comment(
            "author",
            "workspace",
            doc["id"],
            {"version": current["version"], "message_id": "during-verification", "text": "Обсуждение", "context": {"document_id": doc["id"], "scope": "9", "revision": 1, "anchor": None}},
        )
        return {"valid": True, "issues": []}

    ai.verify = verify
    result = await service.apply("author", "workspace", doc["id"], {"version": pending["version"], "proposal_id": pending["proposals"][-1]["id"]})
    assert len(result["comments"]) == 1 and result["revision"] == 2
    assert result["sections"]["9"] == "Новый журнал"


@pytest.mark.asyncio
async def test_changed_proposal_cannot_be_applied_with_an_earlier_verification(store):
    service, eva, ai, doc = await imported(store)
    eva.content["9"], eva.version = "Новый журнал", "v2"
    pending = await service.pull("author", "workspace", doc["id"], {"version": doc["version"]})

    async def verify(*args, **kwargs):
        current = store.get("author", "workspace", doc["id"])
        store.change("author", "workspace", doc["id"], current["version"], lambda value: value["proposals"][-1]["sections"].update({"9": "Другой выбор"}), "resolve")
        return {"valid": True, "issues": []}

    ai.verify = verify
    with pytest.raises(SpecificationError) as error:
        await service.apply("author", "workspace", doc["id"], {"version": pending["version"], "proposal_id": pending["proposals"][-1]["id"]})
    assert error.value.code == "STALE_PROPOSAL"
    assert store.get("author", "workspace", doc["id"])["sections"] == doc["sections"]


@pytest.mark.asyncio
async def test_simultaneous_apply_of_one_proposal_creates_one_revision(store):
    service, eva, ai, doc = await imported(store)
    eva.content["9"], eva.version = "Новый журнал", "v2"
    pending = await service.pull("author", "workspace", doc["id"], {"version": doc["version"]})
    entered, count = asyncio.Event(), 0

    async def verify(*args, **kwargs):
        nonlocal count
        count += 1
        if count == 2:
            entered.set()
        await asyncio.wait_for(entered.wait(), 5)
        return {"valid": True, "issues": []}

    ai.verify = verify
    request = {"version": pending["version"], "proposal_id": pending["proposals"][-1]["id"]}
    results = await asyncio.gather(*(service.apply("author", "workspace", doc["id"], request) for _ in range(2)))
    assert [result["revision"] for result in results] == [2, 2]
    assert len(store.history("author", "workspace", doc["id"])) == 2
