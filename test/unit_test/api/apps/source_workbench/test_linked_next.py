"""Executable acceptance for dependency chains, implementation and document sets."""

from copy import deepcopy

from playhouse.test_utils import count_queries
import pytest

from api.db.db_models import SourceDocumentDependency, SourceDocumentInput, SourceWorkspaceDraft, SourceWorkspaceMember
from api.source_workbench.errors import SourceWorkspaceError
from api.source_workbench.linked_graph import cyclic_components
from test.unit_test.api.apps.source_workbench import test_linked_documents as harness

pytestmark = [pytest.mark.p1, pytest.mark.asyncio]


@pytest.fixture
def linked(tmp_path):
    yield from harness.linked.__wrapped__(tmp_path)


async def document(linked, title="ТЗ"):
    return await linked.service.create("author", "workspace", {"title": title, "document_type": "technical_specification", "content": "# Хранение\n30 дней\n# Ручной раздел\nНе менять.\n"})


async def link(linked, source, target, **values):
    return await linked.service.dependency("author", "workspace", target["id"], {"source_id": source["id"], "expected_version": target["version"], **values})


async def approve(linked, item):
    return await linked.service.approve("author", "workspace", item["id"], {"expected_version": item["version"]})


async def implement(linked, item):
    return await linked.service.implementation("author", "workspace", item["id"], {"expected_version": item["version"], "revision_id": item["approved_revision_id"]})


async def test_cycle_rejected_reference_cycle_permitted_and_graph_preserved(linked):
    a, b = await document(linked, "А"), await document(linked, "Б")
    b = await link(linked, a, b)
    with pytest.raises(SourceWorkspaceError, match="цикл") as error:
        await link(linked, b, a)
    assert error.value.code == "DEPENDENCY_CYCLE" and error.value.status == 409
    assert "Б → А → Б" in error.value.message
    assert SourceDocumentDependency.select().count() == 1
    a = await link(linked, b, a, relation="reference", required=False, blocks_approval=False, maintenance="manual")
    assert len(a["dependencies"]) == 1


async def test_legacy_cycle_stops_previously_queued_job_before_model(linked):
    a, b = await document(linked), await document(linked)
    b = await link(linked, a, b)
    await linked.service.prepare("author", "workspace", b["id"], {"expected_version": b["version"]})
    SourceDocumentDependency.create(id="legacy", workspace_id="workspace", source_id=b["id"], target_id=a["id"], relation="requires")
    with pytest.raises(SourceWorkspaceError) as error:
        await linked.service.execute(linked.repo.claim())
    assert error.value.code == "DEPENDENCY_CYCLE" and linked.provider.calls == 0
    result = await linked.service.overview("author", "workspace")
    assert {item["state"] for item in result["items"]} == {"cycle"}


async def test_only_responsible_editor_confirms_approved_revision_idempotently(linked):
    item = await approve(linked, await document(linked))
    SourceWorkspaceMember.create(workspace_id="workspace", user_id="reviewer", role="editor")
    item = await linked.service.edit("author", "workspace", item["id"], {"expected_version": item["version"], "responsible_id": "reviewer"})
    data = {"expected_version": item["version"], "revision_id": item["approved_revision_id"]}
    with pytest.raises(SourceWorkspaceError) as error:
        await linked.service.implementation("author", "workspace", item["id"], data)
    assert error.value.status == 403
    item = await linked.service.implementation("reviewer", "workspace", item["id"], data)
    repeated = await linked.service.implementation("reviewer", "workspace", item["id"], data)
    assert repeated["implementation_event_id"] == item["implementation_event_id"]
    assert len(repeated["implementation_history"]) == 1
    updated = await linked.service.edit("reviewer", "workspace", item["id"], {"expected_version": item["version"], "content": item["content"] + "Новая рабочая правка\n"})
    assert updated["current_revision_id"] != updated["implemented_revision_id"] == item["approved_revision_id"]


@pytest.mark.parametrize("url", ["https://[broken", "javascript:alert(1)", "file:///etc/passwd"])
async def test_invalid_implementation_evidence_is_validation_error(linked, url):
    item = await approve(linked, await document(linked))
    with pytest.raises(SourceWorkspaceError) as error:
        await linked.service.implementation("author", "workspace", item["id"], {"expected_version": item["version"], "revision_id": item["approved_revision_id"], "evidence_url": url})
    assert error.value.code == "INVALID_INPUT"


async def test_revoke_reconfirm_same_revision_invalidates_plan_and_pins_new_fact(linked):
    source = await approve(linked, await document(linked))
    target = await link(linked, source, await document(linked), refresh_on="implemented")
    assert target["waiting"] and not target["impacts"]
    source = await implement(linked, source)
    target = await linked.service.get("author", "workspace", target["id"])
    change = await harness.prepare(linked, target)
    old_token = change["inputs"][0]["source_state_token"]
    source = await linked.service.implementation(
        "author", "workspace", source["id"], {"expected_version": source["version"], "implementation_event_id": old_token, "reason": "Ошибка подтверждения"}, revoke=True
    )
    with pytest.raises(SourceWorkspaceError):
        await linked.service.decide("author", "workspace", target["id"], change["id"], {"action": "accept"})
    assert (await linked.service.get("author", "workspace", target["id"]))["content"] == target["content"]
    source = await implement(linked, source)
    assert source["implementation_event_id"] != old_token
    with pytest.raises(SourceWorkspaceError):
        await linked.service.decide("author", "workspace", target["id"], change["id"], {"action": "accept"})
    current = await linked.service.get("author", "workspace", target["id"])
    change = await harness.prepare(linked, current)
    applied = await linked.service.decide("author", "workspace", target["id"], change["id"], {"action": "accept"})
    pinned = SourceDocumentInput.get(SourceDocumentInput.revision_id == applied["current_revision_id"])
    assert pinned.source_state_token == source["implementation_event_id"]
    assert "# Ручной раздел\nНе менять." in applied["content"]


async def test_overview_is_readonly_batched_and_empty_set_is_not_ready(linked):
    item = await document(linked)
    item = await approve(linked, item)
    empty = await linked.service.overview("author", "workspace")
    assert not empty["readiness"]["configured"] and not empty["readiness"]["documents_agreed"]
    item = await linked.service.edit("author", "workspace", item["id"], {"expected_version": item["version"], "required_for_readiness": True})
    first = await linked.service.overview("author", "workspace")
    assert first["readiness"]["documents_agreed"] and first["readiness"]["maintenance_complete"]
    template = deepcopy(SourceWorkspaceDraft.get_by_id(item["id"]).__data__)
    with linked.database.atomic():
        for number in range(20):
            SourceWorkspaceDraft.create(**{**template, "id": str(number), "required_for_readiness": False, "title": "Заметка"})
    with count_queries() as twenty:
        await linked.service.overview("author", "workspace")
    with linked.database.atomic():
        for number in range(20, 200):
            SourceWorkspaceDraft.create(**{**template, "id": str(number), "required_for_readiness": False, "title": "Заметка"})
    with count_queries() as two_hundred:
        result = await linked.service.overview("author", "workspace")
    assert two_hundred.count == twenty.count and linked.provider.calls == 0
    assert result["readiness"] == first["readiness"]
    assert all(not ({"content", "sources", "source_ref", "prompt"} & set(row)) for row in result["items"])


async def test_waiting_chain_does_not_promote_unconfirmed_changes(linked):
    a, b, c = await document(linked, "А"), await document(linked, "Б"), await document(linked, "В")
    b = await link(linked, a, b)
    c = await link(linked, b, c, maintenance="auto")
    SourceDocumentDependency.update(reviewed_revision_id=b["current_revision_id"], reviewed_target_revision_id=c["current_revision_id"], reviewed_source_state_token="").where(
        SourceDocumentDependency.id == c["dependencies"][0]["id"]
    ).execute()
    c = await approve(linked, c)
    await linked.service.edit("author", "workspace", a["id"], {"expected_version": a["version"], "content": a["content"].replace("30 дней", "90 дней")})
    proposal = await harness.prepare(linked, b)
    assert await linked.service.inspect_automatic("author", "workspace", c["id"]) is None
    assert (await linked.service.get("author", "workspace", b["id"]))["current_revision_id"] == b["current_revision_id"]
    result = await linked.service.overview("author", "workspace")
    assert next(row for row in result["items"] if row["id"] == c["id"])["state"] == "waiting_upstream"
    updated = await linked.service.decide("author", "workspace", b["id"], proposal["id"], {"action": "accept"})
    first = await linked.service.inspect_automatic("author", "workspace", c["id"])
    repeated = await linked.service.inspect_automatic("author", "workspace", c["id"])
    assert first["id"] == repeated["id"]
    queued = linked.repo.job("workspace", c["id"], first["id"])
    assert queued["payload"]["inputs"][0]["source_revision_id"] == updated["current_revision_id"]
    assert (await linked.service.get("author", "workspace", c["id"]))["content"] == c["content"]


async def test_fan_in_uses_both_confirmed_snapshots_and_rejects_old_job(linked):
    a, b, c, d = [await document(linked, name) for name in ("А", "Б", "В", "Г")]
    b, c = await link(linked, a, b), await link(linked, a, c)
    d = await link(linked, b, d, maintenance="auto")
    d = await link(linked, c, d, maintenance="auto")
    await linked.service.edit("author", "workspace", a["id"], {"expected_version": a["version"], "content": a["content"].replace("30 дней", "90 дней")})
    b_change = await harness.prepare(linked, b)
    b = await linked.service.decide("author", "workspace", b["id"], b_change["id"], {"action": "accept"})
    c_job = await linked.service.prepare("author", "workspace", c["id"], {"expected_version": c["version"]})
    first = await linked.service.inspect_automatic("author", "workspace", d["id"])
    claimed = linked.repo.claim()
    assert claimed["id"] == c_job["id"]
    await linked.service.execute(claimed)
    current_c = await linked.service.get("author", "workspace", c["id"])
    c = await linked.service.decide("author", "workspace", c["id"], current_c["changes"][0]["id"], {"action": "accept"})
    old_job = linked.repo.claim()
    assert old_job["id"] == first["id"]
    with pytest.raises(SourceWorkspaceError):
        await linked.service.execute(old_job)
    linked.repo.finish_job(old_job["id"], old_job["lease_token"], error={"code": "STALE_CHANGE"})
    latest = await linked.service.inspect_automatic("author", "workspace", d["id"])
    repeated = await linked.service.inspect_automatic("author", "workspace", d["id"])
    assert latest["id"] == repeated["id"] != first["id"]
    inputs = linked.repo.job("workspace", d["id"], latest["id"])["payload"]["inputs"]
    assert {item["source_id"]: item["source_revision_id"] for item in inputs} == {b["id"]: b["current_revision_id"], c["id"]: c["current_revision_id"]}
    assert (await linked.service.get("author", "workspace", d["id"]))["current_revision_id"] == d["current_revision_id"]


async def test_iterative_cycle_detection_handles_deep_graph_and_overlapping_cycles():
    links = [{"source_id": str(i), "target_id": str(i + 1), "relation": "requires", "required": True, "blocks_approval": True, "maintenance": "manual"} for i in range(2000)]
    assert cyclic_components(links) == []
    links.append({**links[0], "source_id": "2000", "target_id": "0"})
    assert len(cyclic_components(links)[0]) == 2001


@pytest.mark.parametrize("mandatory", [True, False])
async def test_readiness_keeps_unmarked_mandatory_ancestors_and_optional_waiting_separate(linked, mandatory):
    a, b, c = await document(linked, "А"), await document(linked, "Б"), await document(linked, "В")
    b = await link(linked, a, b)
    # Represent an already reconciled B -> C without changing A -> B.
    c = await link(linked, b, c, required=mandatory, blocks_approval=mandatory)
    dependency = c["dependencies"][0]
    SourceDocumentDependency.update(reviewed_revision_id=b["current_revision_id"], reviewed_target_revision_id=c["current_revision_id"], reviewed_source_state_token="").where(
        SourceDocumentDependency.id == dependency["id"]
    ).execute()
    SourceWorkspaceDraft.update(approved_revision_id=c["current_revision_id"], required_for_readiness=True).where(SourceWorkspaceDraft.id == c["id"]).execute()
    result = await linked.service.overview("author", "workspace")
    row = next(row for row in result["items"] if row["id"] == c["id"])
    assert row["state"] == "waiting_upstream"
    assert bool(row["blockers"]) is mandatory
    assert result["readiness"]["documents_agreed"] is not mandatory
    assert not result["readiness"]["maintenance_complete"]


async def test_applied_text_stays_after_revocation_but_approval_is_blocked(linked):
    source = await implement(linked, await approve(linked, await document(linked)))
    target = await link(linked, source, await document(linked), refresh_on="implemented")
    change = await harness.prepare(linked, target)
    target = await linked.service.decide("author", "workspace", target["id"], change["id"], {"action": "accept"})
    target = await approve(linked, target)
    before = target["content"], target["current_revision_id"]
    await linked.service.implementation(
        "author", "workspace", source["id"], {"expected_version": source["version"], "implementation_event_id": source["implementation_event_id"], "reason": "Реализация не завершена"}, revoke=True
    )
    current = await linked.service.get("author", "workspace", target["id"])
    assert (current["content"], current["current_revision_id"]) == before and current["waiting"]
    with pytest.raises(SourceWorkspaceError):
        await approve(linked, current)


async def test_external_source_cannot_wait_for_implementation(linked):
    source, target = await harness.scenario(linked)
    with pytest.raises(SourceWorkspaceError) as error:
        await link(linked, source, target, refresh_on="implemented")
    assert error.value.code == "INVALID_INPUT"
