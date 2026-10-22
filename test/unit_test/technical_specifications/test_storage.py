from copy import deepcopy

import pytest

from api.db.db_models import SourceWorkspaceMember, TechnicalSpecificationRevision
from api.source_workbench.errors import SourceWorkspaceError
from api.technical_specifications.domain import SpecificationError, empty_sections


def test_projection_includes_the_resolver_name_when_they_did_not_write_the_thread(store):
    from api.db.db_models import User

    doc = store.create("author", "workspace", "ТЗ", empty_sections())

    def add_thread(value):
        value["comments"].append(
            {
                "id": "comment",
                "author_id": "author",
                "text": "Уточнение",
                "replies": [],
                "context": {"document_id": doc["id"], "scope": "all", "revision": 1, "anchor": None},
                "resolved": True,
                "resolved_by": "reader",
                "resolved_at": "2026-10-21T10:38:00Z",
            }
        )

    store.change("author", "workspace", doc["id"], doc["version"], add_thread, "comment")
    saved = store.get("reader", "workspace", doc["id"])
    assert saved["people"]["reader"] == User.get_by_id("reader").nickname
    assert saved["revision"] == 1


def test_real_storage_appends_revisions_and_rejects_stale_write(store):
    doc = store.create("author", "workspace", "ТЗ", empty_sections())

    def update(value):
        value["sections"]["5"] = "Сценарий"

    saved = store.change("author", "workspace", doc["id"], 1, update, "edit")
    assert saved["revision"] == 2 and saved["version"] == 2
    assert store.history("author", "workspace", doc["id"], 1)["sections"]["5"] == ""
    with pytest.raises(SpecificationError):
        store.change("author", "workspace", doc["id"], 1, update, "edit")
    assert len(store.history("author", "workspace", doc["id"])) == 2


def test_revision_failure_rolls_back_entire_projection(store, monkeypatch):
    doc = store.create("author", "workspace", "ТЗ", empty_sections())

    def fail_revision(**kwargs):
        raise RuntimeError("storage interrupted")

    monkeypatch.setattr(TechnicalSpecificationRevision, "create", fail_revision)
    with pytest.raises(RuntimeError):
        store.change("author", "workspace", doc["id"], 1, lambda value: value["sections"].update({"5": "Новый текст"}), "edit")
    assert store.get("author", "workspace", doc["id"]) == doc


def test_workspace_acl_and_document_ownership_are_enforced(store):
    doc = store.create("author", "workspace", "ТЗ", empty_sections())
    assert store.get("reader", "workspace", doc["id"])["id"] == doc["id"]
    with pytest.raises(SourceWorkspaceError):
        store.change("reader", "workspace", doc["id"], 1, lambda value: value.update(title="Запрещено"), "edit")
    with pytest.raises(SourceWorkspaceError):
        store.get("stranger", "workspace", doc["id"])
    with pytest.raises(SpecificationError):
        store.get("stranger", "other", doc["id"])


def test_drafts_are_user_scoped_and_cannot_overwrite_another_window(store):
    doc = store.create("author", "workspace", "ТЗ", empty_sections())
    payload = {
        "message_id": "message",
        "text": "Уточнить цитату",
        "context": {"document_id": doc["id"], "scope": "selection", "revision": 1, "anchor": {"section": "5", "quote": "Цитата", "prefix": "до ", "suffix": " после"}},
    }
    first = store.save_draft("author", "workspace", doc["id"], 0, payload)
    assert first == {"version": 1, **payload}
    assert store.get_draft("reader", "workspace", doc["id"]) is None
    store.save_draft("reader", "workspace", doc["id"], 0, {**payload, "text": "Личный черновик читателя"})
    with pytest.raises(SpecificationError) as error:
        store.save_draft("author", "workspace", doc["id"], 0, {**payload, "text": "Другое окно"})
    assert error.value.code == "DRAFT_CONFLICT"
    assert store.get_draft("author", "workspace", doc["id"]) == first
    assert store.get_draft("reader", "workspace", doc["id"])["text"] == "Личный черновик читателя"


def test_repeat_import_opens_existing_and_copy_is_independent(store):
    content = empty_sections()
    first = store.create("author", "workspace", "Из Eva", content, binding={"id": "page"}, origin_key="eva-page-key")
    updated = deepcopy(content)
    updated["5"] = "Изменено снаружи"
    repeated = store.create("author", "workspace", "Не переименовывать", updated, binding={"id": "page"}, origin_key="eva-page-key")
    assert repeated == first
    copied = store.create("author", "workspace", "Независимая копия", updated)
    assert copied["id"] != first["id"] and copied["eva"] is None
    assert len(store.list("author", "workspace")) == 2


def test_one_active_page_binding_spans_projects_in_same_tenant(store):
    first = store.create("author", "workspace", "Из Eva", empty_sections(), binding={"id": "page"}, origin_key="same-page")
    repeated = store.create("author", "sibling", "Другой импорт", empty_sections(), binding={"id": "page"}, origin_key="same-page")
    assert repeated["id"] == first["id"] and repeated["workspace_id"] == "workspace"
    assert store.list("author", "sibling") == []


def test_revoked_membership_prevents_reading_document_and_personal_draft(store):
    doc = store.create("author", "workspace", "ТЗ", empty_sections())
    SourceWorkspaceMember.delete().where(SourceWorkspaceMember.user_id == "reader").execute()
    for method in (store.get, store.get_draft, store.history):
        with pytest.raises(SourceWorkspaceError):
            method("reader", "workspace", doc["id"])


def test_full_snapshot_proposals_are_compacted_without_losing_their_original_revision(store):
    from api.db.db_models import TechnicalSpecification

    content = empty_sections()
    content["1"] = "Исходный текст"
    doc = store.create("author", "workspace", "ТЗ", content)
    candidate = {**content, "5": "Предложение"}
    row = TechnicalSpecification.get_by_id(doc["id"])
    payload = deepcopy(row.payload)
    payload["proposals"] = [{"id": "proposal", "source": "chat", "message_id": "original-request", "base_revision": 1, "sections": candidate, "status": "ready"}]
    TechnicalSpecification.update(payload=payload).where(TechnicalSpecification.id == doc["id"]).execute()
    assert store.get("author", "workspace", doc["id"])["proposals"][0]["sections"] == candidate
    saved = store.change("author", "workspace", doc["id"], 1, lambda value: value["sections"].update({"1": "Текущий текст"}), "edit")
    assert saved["revision"] == 2 and saved["proposals"][0]["sections"] == candidate
    stored = TechnicalSpecification.get_by_id(doc["id"]).payload["proposals"][0]
    assert stored["patches"] == {"5": "Предложение"} and "sections" not in stored
    assert stored["message_id"] == "original-request"
    assert store.get("author", "workspace", doc["id"]) == saved
    assert store.history("author", "workspace", doc["id"], 1)["sections"] == content


@pytest.mark.parametrize("count", [1, 20])
def test_proposal_hydration_reads_base_revisions_in_one_query(store, monkeypatch, count):
    content = empty_sections()
    doc = store.create("author", "workspace", "ТЗ", content)

    def proposals(value):
        value["proposals"] = [{"id": str(index), "source": "chat", "base_revision": 1, "sections": {**content, "5": str(index)}, "status": "ready"} for index in range(count)]

    store.change("author", "workspace", doc["id"], 1, proposals, "seed-proposals")
    execute = store.database.execute_sql
    queries = []

    def capture(sql, *args, **kwargs):
        if sql.startswith("SELECT") and "technical_specification_revision" in sql:
            queries.append(sql)
        return execute(sql, *args, **kwargs)

    monkeypatch.setattr(store.database, "execute_sql", capture)
    restored = store.get("author", "workspace", doc["id"])
    assert len(restored["proposals"]) == count
    assert len(queries) == 1
