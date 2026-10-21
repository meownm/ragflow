"""Shared reservations, saved-data projection and transactional rollback."""

import pytest
from peewee import SqliteDatabase

from api.db.db_models import BusinessDocument, BusinessDocumentEvaBinding, DocumentExchangeBinding, TechnicalSpecification
from api.document_exchange.bindings import BindingConflict, occupancy, release, reserve
from document_exchange.identity import binding_keys
from api.document_exchange.migrations import migrate_bindings
from api.technical_specifications.domain import eva_identity


@pytest.fixture
def database(tmp_path):
    db = SqliteDatabase(tmp_path / "bindings.sqlite")
    models = [BusinessDocument, BusinessDocumentEvaBinding, DocumentExchangeBinding, TechnicalSpecification]
    with db.bind_ctx(models):
        db.create_tables(models)
        yield db
        db.close()


def binding(page="page", url=None):
    return {"eva_origin": ["https", "eva.test", 443], "project_id": "project", "document_id": page, "page_url": url or f"https://eva.test/project/Document/{page}", "status": "CONNECTED"}


def business(document_id="business", tenant="tenant"):
    return BusinessDocument.create(
        id=document_id, tenant_id=tenant, owner_id="author", chat_id=document_id, title=document_id, title_key=document_id, idea="", dataset_ids=[], template_version="1", policy_version="1"
    )


def saved_business(page=None, document_id="business"):
    business(document_id)
    return BusinessDocumentEvaBinding.create(document_id=document_id, status="CONNECTED", binding=page or binding(), page_url_key=document_id, eva_identity_key="old-" + document_id)


def saved_specification(page=None):
    value = page or binding()
    return TechnicalSpecification.create(id="spec", tenant_id="tenant", workspace_id="workspace", title="ТЗ", origin_key=eva_identity(value), payload={"eva": value})


@pytest.mark.parametrize("origin", ["https://EVA.test", ["https", "eva.test", None], ("https", "eva.test", 443)])
def test_products_and_origin_representations_share_one_identity(origin):
    value = binding()
    expected = eva_identity(value)
    value["eva_origin"] = origin
    assert binding_keys(value)[1] == expected


def test_claims_require_the_owners_transaction(database):
    with pytest.raises(RuntimeError, match="transaction"):
        reserve("tenant", "technical_specification", "spec", binding())


def test_both_products_compete_on_page_identity_even_with_different_urls(database):
    with database.atomic():
        reserve("tenant", "business_requirements", "business", binding())
    with pytest.raises(BindingConflict), database.atomic():
        reserve("tenant", "technical_specification", "spec", binding(url="https://eva.test/alias"))
    with database.atomic():
        reserve("other-tenant", "technical_specification", "spec", binding())
    assert DocumentExchangeBinding.select().count() == 2
    owner = occupancy("tenant", set(binding_keys(binding())))
    assert owner[binding_keys(binding())[1]] == {"kind": "business_requirements", "document_id": "business"}


def test_conflicting_rebind_preserves_previous_claim_and_outer_transaction(database):
    with database.atomic():
        reserve("tenant", "business_requirements", "business", binding())
        reserve("tenant", "technical_specification", "spec", binding("other"))
    with database.atomic():
        with pytest.raises(BindingConflict):
            reserve("tenant", "technical_specification", "spec", binding())
        reserve("tenant", "technical_specification", "third", binding("third"))
    assert occupancy("tenant", {binding_keys(binding("other"))[1]})
    with database.atomic():
        release("other-tenant", "business_requirements", "business")
    assert DocumentExchangeBinding.select().count() == 3
    with database.atomic():
        release("tenant", "business_requirements", "business")
    assert not occupancy("tenant", {binding_keys(binding())[1]})


def test_failed_document_transaction_releases_reservation(database):
    with pytest.raises(ValueError, match="abort"), database.atomic():
        reserve("tenant", "technical_specification", "spec", binding())
        raise ValueError("abort")
    assert DocumentExchangeBinding.select().count() == 0


def test_migration_preserves_payloads_and_is_repeatable(database):
    old = saved_business()
    specification = saved_specification(binding("other"))
    migrate_bindings()
    migrate_bindings()
    assert DocumentExchangeBinding.select().count() == 2
    assert BusinessDocumentEvaBinding.get_by_id(old.document_id).binding == old.binding
    assert TechnicalSpecification.get_by_id(specification.id).payload == specification.payload
    assert BusinessDocumentEvaBinding.get_by_id(old.document_id).eva_identity_key == binding_keys(old.binding)[1]


def test_migration_conflict_rolls_back_all_claims_and_key_updates(database):
    old = saved_business()
    saved_specification()
    with pytest.raises(RuntimeError, match="multiple owners"):
        migrate_bindings()
    assert DocumentExchangeBinding.select().count() == 0
    assert BusinessDocumentEvaBinding.get_by_id(old.document_id).eva_identity_key == old.eva_identity_key
