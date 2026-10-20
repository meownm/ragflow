import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from api.db.db_models import DocumentExchangeOperation, SourceWorkspaceMember, TechnicalSpecification, TechnicalSpecificationRevision
from api.source_workbench.errors import SourceWorkspaceError
from api.technical_specifications.application import Specifications
from api.technical_specifications.domain import SpecificationError, empty_sections
from test.unit_test.technical_specifications.test_eva import AI, Eva, run

REQUEST = {"url": "https://eva.test/page", "copy": True, "operation_id": "copy-request"}


@pytest.mark.asyncio
@pytest.mark.parametrize("extra", [{"url": "https://user:password@eva.test/page"}, {"selection": {"id": "page", "project_id": "project", "version": "x" * 256}}])
async def test_invalid_or_sensitive_request_is_rejected_before_journaling(store, extra):
    service = Specifications(store, AI(), run, Eva())
    with pytest.raises(SpecificationError) as error:
        await service.import_eva("author", "workspace", {**REQUEST, **extra})
    assert error.value.code == "INVALID_INPUT"
    assert DocumentExchangeOperation.select().count() == TechnicalSpecification.select().count() == 0


@pytest.mark.asyncio
async def test_lost_commit_response_replays_current_document_without_another_eva_read(store, monkeypatch):
    eva = Eva()
    service = Specifications(store, AI(), run, eva)
    save = store.save_import

    def lost(*args):
        save(*args)
        raise ConnectionError("Lost local response after commit")

    monkeypatch.setattr(store, "save_import", lost)
    with pytest.raises(ConnectionError):
        await service.import_eva("author", "workspace", REQUEST)
    assert DocumentExchangeOperation.get().state == "completed"
    assert TechnicalSpecification.select().count() == 1
    monkeypatch.setattr(store, "save_import", save)
    result = await service.import_eva("author", "workspace", REQUEST)
    edited = store.change("author", "workspace", result["id"], result["version"], lambda doc: doc.update(title="Локальное название"), "edit")
    eva.version = "v2"
    assert await service.import_eva("author", "workspace", REQUEST) == edited
    assert eva.reads == 1
    assert (await service.cancel_import("author", "workspace", REQUEST))["document"] == edited
    with pytest.raises(SpecificationError) as error:
        await service.import_eva("author", "workspace", {**REQUEST, "copy": False})
    assert error.value.code == "IDEMPOTENCY_KEY_REUSED"
    with pytest.raises(SpecificationError):
        await service.import_eva("author", "sibling", REQUEST)
    assert TechnicalSpecification.select().count() == 1


@pytest.mark.asyncio
async def test_cancel_before_delivery_is_a_tombstone_for_the_late_original_request(store):
    eva = Eva()
    service = Specifications(store, AI(), run, eva)
    canceled = await service.cancel_import("author", "workspace", REQUEST)
    assert canceled["state"] == "canceled"
    with pytest.raises(SpecificationError) as error:
        await service.import_eva("author", "workspace", REQUEST)
    assert error.value.code == "IMPORT_CANCELED"
    assert TechnicalSpecification.select().count() == eva.reads == 0


@pytest.mark.asyncio
async def test_cancel_during_external_read_prevents_local_document_creation(store):
    eva = Eva()
    entered, release = asyncio.Event(), asyncio.Event()

    async def paused(function, *args, **kwargs):
        if function == eva.read_connected_page:
            entered.set()
            await release.wait()
        return function(*args, **kwargs)

    service = Specifications(store, AI(), paused, eva)
    task = asyncio.create_task(service.import_eva("author", "workspace", REQUEST))
    await asyncio.wait_for(entered.wait(), timeout=2)
    assert (await service.cancel_import("author", "workspace", REQUEST))["state"] == "canceled"
    release.set()
    with pytest.raises(SpecificationError) as error:
        await task
    assert error.value.code == "IMPORT_INTERRUPTED"
    assert TechnicalSpecification.select().count() == TechnicalSpecificationRevision.select().count() == 0
    assert DocumentExchangeOperation.get().state == "canceled"


def test_expired_attempt_cannot_commit_after_retry_claims_the_request(store):
    request = {"url": REQUEST["url"], "copy": True}
    old = store.begin_import("author", "workspace", "retry", request)
    with pytest.raises(SpecificationError) as error:
        store.begin_import("author", "workspace", "retry", request)
    assert error.value.code == "IMPORT_IN_PROGRESS"
    DocumentExchangeOperation.update(lease_until=datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1)).execute()
    new = store.begin_import("author", "workspace", "retry", request)
    values = {"title": "Копия", "sections": empty_sections()}
    with pytest.raises(SpecificationError) as error:
        store.save_import("author", "workspace", "retry", old["attempt"], values)
    assert error.value.code == "IMPORT_INTERRUPTED"
    result = store.save_import("author", "workspace", "retry", new["attempt"], values)
    assert store.begin_import("author", "workspace", "retry", request)["operation"]["document"] == result
    assert TechnicalSpecification.select().count() == 1


@pytest.mark.asyncio
async def test_document_and_receipt_rollback_together_and_failed_import_can_retry(store, monkeypatch):
    service = Specifications(store, AI(), run, Eva())
    create = TechnicalSpecificationRevision.create

    def fail_revision(**_values):
        raise RuntimeError("Revision unavailable")

    monkeypatch.setattr(TechnicalSpecificationRevision, "create", fail_revision)
    with pytest.raises(RuntimeError):
        await service.import_eva("author", "workspace", REQUEST)
    assert TechnicalSpecification.select().count() == 0
    operation = DocumentExchangeOperation.get()
    assert operation.state == "failed" and operation.result == {}
    monkeypatch.setattr(TechnicalSpecificationRevision, "create", create)
    result = await service.import_eva("author", "workspace", REQUEST)
    assert result["id"] and TechnicalSpecification.select().count() == 1
    assert DocumentExchangeOperation.get().state == "completed"


@pytest.mark.asyncio
async def test_receipts_are_personal_and_membership_is_rechecked(store):
    class MemberEva(Eva):
        def resolve_page_url(self, actor, url):
            return super().resolve_page_url("author", url)

        def read_connected_page(self, actor, binding):
            return super().read_connected_page("author", binding)

    SourceWorkspaceMember.update(role="editor").where(SourceWorkspaceMember.user_id == "reader").execute()
    service = Specifications(store, AI(), run, MemberEva())
    await service.import_eva("reader", "workspace", REQUEST)
    assert await service.import_operation("author", "workspace", REQUEST["operation_id"]) is None
    SourceWorkspaceMember.delete().where(SourceWorkspaceMember.user_id == "reader").execute()
    for action in (
        lambda: service.import_operation("reader", "workspace", REQUEST["operation_id"]),
        lambda: service.import_eva("reader", "workspace", REQUEST),
        lambda: service.cancel_import("reader", "workspace", REQUEST),
    ):
        with pytest.raises(SourceWorkspaceError):
            await action()


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", [False, True])
async def test_completed_receipt_can_be_closed_when_its_cross_project_result_becomes_unavailable(store, missing):
    from api.db.db_models import SourceWorkspace, UserTenant

    SourceWorkspace.create(id="target", tenant_id="tenant", owner_id="stranger", title="Закрытый проект")
    SourceWorkspace.update(tenant_id="tenant").where(SourceWorkspace.id == "workspace").execute()
    UserTenant.create(id="membership", tenant_id="tenant", user_id="author", role="normal", invited_by="stranger")
    SourceWorkspaceMember.create(workspace_id="target", user_id="author", role="editor")
    service = Specifications(store, AI(), run, Eva())
    target = await service.import_eva("author", "target", {"url": REQUEST["url"]})
    request = {**REQUEST, "operation_id": "open-from-source", "copy": False}
    assert (await service.import_eva("author", "workspace", request))["id"] == target["id"]
    if missing:
        TechnicalSpecification.delete().where(TechnicalSpecification.id == target["id"]).execute()
    else:
        SourceWorkspaceMember.delete().where(SourceWorkspaceMember.workspace_id == "target").execute()
    result = await service.import_operation("author", "workspace", request["operation_id"])
    assert result["state"] == "completed" and result["document_unavailable"] is True
    assert target["id"] not in str(result) and "Закрытый проект" not in str(result)
    with pytest.raises(SpecificationError) as error:
        await service.import_eva("author", "workspace", request)
    assert error.value.code == "IMPORT_RESULT_UNAVAILABLE"
    assert await service.cancel_import("author", "workspace", request) == result
    assert TechnicalSpecification.select().count() == int(not missing)
