"""Real source owner query boundary with isolated relational fixtures."""

from contextlib import contextmanager
from pathlib import Path
import sys
from types import ModuleType

import pytest

from api.db.db_models import BusinessDocument, BusinessDocumentEvent, BusinessDocumentRevision
from api.source_workbench.business_source import BusinessDocumentSources
from api.source_workbench.errors import SourceWorkspaceError
from business_documents.application.queries import DocumentQueries
from test.unit_test.api.apps.source_workbench import test_linked_documents as harness

pytestmark = [pytest.mark.p1, pytest.mark.asyncio]


@pytest.fixture
def linked(tmp_path):
    yield from harness.linked.__wrapped__(tmp_path)


@contextmanager
def business(linked):
    # Keep the owner adapter real without bootstrapping the HTTP server/search engine.
    if "api.apps" not in sys.modules:
        package = ModuleType("api.apps")
        package.__path__ = [str(Path(__file__).resolve().parents[5] / "api" / "apps")]
        sys.modules["api.apps"] = package
    from api.apps.business_documents.adapters.queries import PeeweeDocumentReader

    with linked.database.bind_ctx([BusinessDocument, BusinessDocumentEvent, BusinessDocumentRevision]):
        linked.database.create_tables([BusinessDocument, BusinessDocumentEvent, BusinessDocumentRevision])
        gateway = BusinessDocumentSources(DocumentQueries(PeeweeDocumentReader(), None, lambda: 0))
        linked.service.business = linked.repo.business = gateway
        yield gateway


def seed(document_id="native", revision_id="native-r1", sequence=1, *, event_type="ReviewAgreedWithoutChanges", payload=None):
    BusinessDocument.get_or_create(
        id=document_id,
        defaults={
            "tenant_id": "another-tenant",
            "owner_id": "another-owner",
            "chat_id": document_id,
            "title": "Согласованные требования",
            "title_key": document_id,
            "idea": "Идея",
            "template_version": "v1",
            "policy_version": "v1",
            "current_revision_id": revision_id,
            "lifecycle_state": "REVIEW",
        },
    )
    BusinessDocumentRevision.get_or_create(
        id=revision_id,
        defaults={
            "document_id": document_id,
            "revision_number": sequence,
            "document_ast": {},
            "body_markdown": f"# Хранение\n{30 if sequence == 1 else 90} дней\n",
            "content_hash": revision_id,
            "source_event_ids": [],
        },
    )
    return BusinessDocumentEvent.create(
        id=f"{document_id}-event-{sequence}",
        document_id=document_id,
        sequence=sequence,
        event_type=event_type,
        actor_type="USER",
        actor_id="another-owner",
        payload={"revision_id": revision_id} if payload is None else payload,
        correlation_id="test",
    )


async def test_shared_catalog_uses_last_proven_agreement_not_current_draft(linked):
    with business(linked):
        first = seed()
        seed(revision_id="native-draft", sequence=2, event_type="ChangesApplied", payload={"revision_id": "native-draft", "review_continues": True})
        BusinessDocument.update(current_revision_id="native-draft").execute()
        catalog = await linked.service.business_sources("author", "workspace")
        assert catalog["items"][0]["revision_id"] == "native-r1"
        imported = await linked.service.import_source("author", "workspace", {"kind": "business_document", "document_id": "native"})
        assert imported["document_type"] == "business_requirements" and not imported["capabilities"]["approve"]
        assert "30 дней" in imported["content"] and imported["source_ref"]["agreement_event_id"] == first.id
        repeated = await linked.service.import_source("author", "workspace", {"kind": "business_document", "document_id": "native"})
        assert repeated["id"] == imported["id"]
        seed(revision_id="native-draft", sequence=3)
        refreshed = await linked.service.refresh("author", "workspace", imported["id"])
        assert "90 дней" in refreshed["content"] and refreshed["current_revision_id"] != imported["current_revision_id"]
        old = await linked.service.history("author", "workspace", imported["id"], imported["current_revision_id"])
        assert old["source_metadata"]["agreement_event_id"] == first.id
        assert BusinessDocument.get_by_id("native").current_revision_id == "native-draft"


@pytest.mark.parametrize("proof", [{}, {"review_continues": 0}, {"review_continues": None}, {"review_continues": True}])
async def test_missing_or_nonboolean_agreement_is_never_inferred_from_lifecycle(linked, proof):
    with business(linked):
        seed(event_type="ChangesApplied", payload={"revision_id": "native-r1", **proof})
        BusinessDocument.update(lifecycle_state="AGREED").execute()
        assert (await linked.service.business_sources("author", "workspace"))["items"] == []
        with pytest.raises(SourceWorkspaceError) as error:
            await linked.service.import_source("author", "workspace", {"kind": "business_document", "document_id": "native"})
        assert error.value.code == "DOCUMENT_UNAVAILABLE"


async def test_noop_agreement_changes_token_without_new_body_revision(linked):
    with business(linked) as gateway:
        seed()
        old = gateway.read_many("author", ["native"])["native"]
        source = await linked.service.import_source("author", "workspace", {"kind": "business_document", "document_id": "native"})
        seed(sequence=2)
        current = gateway.read_many("author", ["native"])["native"]
        assert old["revision_id"] == current["revision_id"] and old["revision"] != current["revision"]
        with linked.database.atomic(), pytest.raises(SourceWorkspaceError) as error:
            gateway.validate("author", [old], lock=True, fresh=True)
        assert error.value.code == "SOURCE_CHANGED"
        refreshed = await linked.service.refresh("author", "workspace", source["id"])
        assert refreshed["content"] == source["content"] and refreshed["current_revision_id"] != source["current_revision_id"]
        latest = await linked.service.history("author", "workspace", source["id"], refreshed["current_revision_id"])
        previous = await linked.service.history("author", "workspace", source["id"], source["current_revision_id"])
        assert latest["source_metadata"]["agreement_event_id"] == current["agreement_event_id"]
        assert previous["source_metadata"]["agreement_event_id"] == old["agreement_event_id"]


async def test_deleted_or_inactive_source_cannot_be_read_via_snapshot(linked):
    with business(linked):
        seed()
        source = await linked.service.import_source("author", "workspace", {"kind": "business_document", "document_id": "native"})
        BusinessDocument.delete().execute()
        with pytest.raises(SourceWorkspaceError):
            await linked.service.get("author", "workspace", source["id"])


async def test_finalization_checks_native_agreement_inside_transaction(linked):
    with business(linked):
        seed()
        source = await linked.service.import_source("author", "workspace", {"kind": "business_document", "document_id": "native"})
        target = await linked.service.create("author", "workspace", {"title": "ТЗ", "document_type": "technical_specification", "content": "# Хранение\n30 дней\n"})
        target = await linked.service.dependency("author", "workspace", target["id"], {"source_id": source["id"], "refresh_on": "approved", "expected_version": target["version"]})
        change = await harness.prepare(linked, target)
        seed(sequence=2)
        with pytest.raises(SourceWorkspaceError):
            linked.repo.finalize("author", "workspace", target["id"], change["id"], target["sections"], change["inputs"], "", "accept")
        assert (await linked.service.get("author", "workspace", target["id"]))["content"] == target["content"]


async def test_new_native_agreement_is_visible_without_mutation_and_applied_replay_is_safe(linked):
    with business(linked):
        seed()
        source = await linked.service.import_source("author", "workspace", {"kind": "business_document", "document_id": "native"})
        target = await linked.service.create("author", "workspace", {"title": "ТЗ", "content": "# Хранение\n30 дней\n", "required_for_readiness": True})
        target = await linked.service.dependency("author", "workspace", target["id"], {"source_id": source["id"], "refresh_on": "approved", "expected_version": target["version"]})
        change = await harness.prepare(linked, target)
        target = await linked.service.decide("author", "workspace", target["id"], change["id"], {"action": "accept"})
        target = await linked.service.approve("author", "workspace", target["id"], {"expected_version": target["version"]})
        target = await linked.service.edit("author", "workspace", target["id"], {"expected_version": target["version"], "required_for_readiness": True})
        seed(sequence=2)
        repeated = await linked.service.decide("author", "workspace", target["id"], change["id"], {"action": "accept"})
        assert repeated["current_revision_id"] == target["current_revision_id"] and len(repeated["revisions"]) == len(target["revisions"])
        result = await linked.service.overview("author", "workspace")
        imported = next(row for row in result["items"] if row["id"] == source["id"])
        assert imported["source_update_available"] and imported["state"] == "needs_review"
        assert not result["readiness"]["documents_agreed"]
        assert (await linked.service.get("author", "workspace", source["id"]))["current_revision_id"] == source["current_revision_id"]


async def test_gateway_closes_owned_thread_connection_but_preserves_caller_transaction(linked):
    import asyncio

    with business(linked) as gateway:
        seed()

        def standalone():
            assert linked.database.is_closed()
            gateway.read_many("author", ["native"])
            assert linked.database.is_closed()
            gateway.list("author")
            assert linked.database.is_closed()

        await asyncio.to_thread(standalone)
        with linked.database.atomic():
            gateway.validate("author", [{"document_id": "native"}], lock=True)
            assert linked.database.in_transaction() and not linked.database.is_closed()


async def test_legacy_draft_read_checks_native_source_and_active_actor(linked, monkeypatch):
    import inspect
    from api.db.db_models import SourceWorkspaceDraft, User
    from api.source_workbench.adapters import SourceWorkspaceRepository

    with business(linked) as gateway:
        runtime = ModuleType("api.apps.business_documents.runtime")
        runtime.document_queries = gateway.queries
        monkeypatch.setitem(sys.modules, runtime.__name__, runtime)
        seed()
        source = await linked.service.import_source("author", "workspace", {"kind": "business_document", "document_id": "native"})
        target = await linked.service.create("author", "workspace", {"title": "Черновик", "content": "Текст"})
        await linked.service.dependency("author", "workspace", target["id"], {"source_id": source["id"], "expected_version": target["version"]})
        SourceWorkspaceDraft.update(source_version=1).where(SourceWorkspaceDraft.id == target["id"]).execute()
        get, listing = inspect.unwrap(SourceWorkspaceRepository.get_draft), inspect.unwrap(SourceWorkspaceRepository.list_drafts)
        assert get("author", "workspace", target["id"])["content"] == "Текст"
        assert listing("author", "workspace")
        User.update(is_active="0").where(User.id == "author").execute()
        with pytest.raises(SourceWorkspaceError) as error:
            get("author", "workspace", target["id"])
        assert error.value.status == 403
        User.update(is_active="1").where(User.id == "author").execute()
        BusinessDocument.delete().execute()
        with pytest.raises(SourceWorkspaceError):
            get("author", "workspace", target["id"])


async def test_native_metadata_is_batched_and_only_selected_latest_body_is_loaded(linked, monkeypatch):
    from playhouse.test_utils import count_queries

    with business(linked) as gateway:
        for number in range(200):
            seed(document_id=str(number), revision_id=f"r{number}")
        with count_queries() as twenty:
            gateway.read_many("author", [str(i) for i in range(20)])
        with count_queries() as two_hundred:
            rows = gateway.read_many("author", [str(i) for i in range(200)])
        assert twenty.count == two_hundred.count and len(rows) == 200
        assert all("body_markdown" not in row for row in rows.values())
        seed(document_id="0", revision_id="r0-latest", sequence=2)
        reader = gateway.queries._reader
        original = reader.source_revisions
        body_requests = []

        def track(ids, *, include_body=False):
            if include_body:
                body_requests.append(set(ids))
            return original(ids, include_body=include_body)

        monkeypatch.setattr(reader, "source_revisions", track)
        latest = gateway.read_many("author", ["0"], body=True)["0"]
        assert latest["revision_id"] == "r0-latest" and body_requests == [{"r0-latest"}]


async def test_native_revision_removed_between_metadata_and_body_is_unavailable(linked, monkeypatch):
    with business(linked) as gateway:
        seed()
        reader = gateway.queries._reader
        original = reader.source_revisions

        def disappearing(ids, *, include_body=False):
            return () if include_body else original(ids)

        monkeypatch.setattr(reader, "source_revisions", disappearing)
        with pytest.raises(SourceWorkspaceError) as error:
            await linked.service.import_source("author", "workspace", {"kind": "business_document", "document_id": "native"})
        assert error.value.code == "DOCUMENT_UNAVAILABLE" and error.value.status == 409
