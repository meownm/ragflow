"""Binding and import invariants on the runtime driver and disposable PostgreSQL."""

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from peewee import InterfaceError, OperationalError
from playhouse.db_url import connect

from api.db.db_models import BusinessDocument, BusinessDocumentEvaBinding, DocumentExchangeBinding, DocumentExchangeOperation, RetryingPooledPostgresqlDatabase, TechnicalSpecification
from api.document_exchange import operations
from api.document_exchange.bindings import BindingConflict, reserve
from document_exchange.identity import binding_keys
from api.document_exchange.migrations import migrate_bindings


@pytest.fixture
def database():
    dsn = os.environ.get("DOCUMENT_EXCHANGE_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("Set DOCUMENT_EXCHANGE_TEST_POSTGRES_DSN to disposable loopback PostgreSQL")
    assert urlsplit(dsn).hostname in {"127.0.0.1", "localhost"}
    schema = "exchange_" + uuid4().hex
    address = urlsplit(dsn)
    db = RetryingPooledPostgresqlDatabase(
        address.path.lstrip("/"),
        host=address.hostname,
        port=address.port or 5432,
        user=address.username,
        password=address.password,
        options=f"-csearch_path={schema}",
        max_connections=8,
        max_retries=1,
        retry_delay=0,
    )
    db.connect()
    db.execute_sql(f'CREATE SCHEMA "{schema}"')
    try:
        models = [BusinessDocument, BusinessDocumentEvaBinding, DocumentExchangeBinding, DocumentExchangeOperation, TechnicalSpecification]
        with db.bind_ctx(models):
            db.create_tables(models)
            yield db
    finally:
        db.execute_sql(f'DROP SCHEMA "{schema}" CASCADE')
        db.close_all()


def terminate_connection(database):
    """Terminate this test's session through an independent disposable connection."""
    pid = database.execute_sql("SELECT pg_backend_pid()").fetchone()[0]
    control = connect(os.environ["DOCUMENT_EXCHANGE_TEST_POSTGRES_DSN"])
    try:
        assert control.execute_sql("SELECT pg_terminate_backend(%s)", (pid,)).fetchone()[0]
    finally:
        control.close()


@pytest.mark.parametrize("alias", [False, True])
def test_two_products_cannot_claim_the_same_page_concurrently(database, alias):
    barrier = Barrier(2)

    def write(index):
        with database.connection_context():
            page = {"page_url": f"https://eva.test/{index if alias else 'page'}", "eva_origin": ["https", "eva.test", 443], "project_id": "project", "document_id": "page"}
            barrier.wait(timeout=10)
            try:
                with database.atomic():
                    reserve("tenant", ["business_requirements", "technical_specification"][index], str(index), page)
                return "saved"
            except BindingConflict:
                return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(write, range(2)))
    assert sorted(results) == ["conflict", "saved"]
    assert DocumentExchangeBinding.select().count() == 1


@pytest.mark.parametrize("conflict", [False, True])
def test_saved_links_migrate_atomically_and_repeatably(database, conflict):
    page = {"page_url": "https://eva.test/first", "eva_origin": ["https", "eva.test", 443], "project_id": "project", "document_id": "first"}
    other = page if conflict else {**page, "document_id": "second", "page_url": "https://eva.test/second"}
    BusinessDocument.create(id="business", tenant_id="tenant", owner_id="author", chat_id="chat", title="БТ", title_key="business", idea="", template_version="1", policy_version="1")
    BusinessDocumentEvaBinding.create(document_id="business", status="CONNECTED", page_url_key="old-page", eva_identity_key="old-identity", binding=page)
    TechnicalSpecification.create(id="spec", tenant_id="tenant", workspace_id="workspace", title="ТЗ", origin_key=binding_keys(other)[1], payload={"eva": other})
    if conflict:
        with pytest.raises(RuntimeError, match="multiple owners"):
            migrate_bindings()
        assert DocumentExchangeBinding.select().count() == 0
        assert BusinessDocumentEvaBinding.get_by_id("business").eva_identity_key == "old-identity"
    else:
        migrate_bindings()
        migrate_bindings()
        assert DocumentExchangeBinding.select().count() == 2
        assert BusinessDocumentEvaBinding.get_by_id("business").eva_identity_key == binding_keys(page)[1]
    assert TechnicalSpecification.get_by_id("spec").payload == {"eva": other}


def test_one_request_claims_once_across_connections(database):
    barrier = Barrier(2)

    def claim(_index):
        with database.connection_context():
            barrier.wait(timeout=10)
            try:
                with database.atomic():
                    value, token = operations.claim("tenant", "technical_specification", "actor", "request", "workspace", {"url": "https://eva.test/page", "copy": True})
                    return bool(token), value["state"]
            except operations.OperationError as error:
                return False, error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, range(2)))
    assert sorted(results) == [(False, "IMPORT_IN_PROGRESS"), (True, "processing")]
    assert DocumentExchangeOperation.select().count() == 1


def test_cancel_racing_completion_has_one_atomic_outcome(database):
    request = {"url": "https://eva.test/page", "copy": True}
    with database.atomic():
        _value, token = operations.claim("tenant", "technical_specification", "actor", "request", "workspace", request)
    barrier = Barrier(2)

    def save():
        with database.connection_context():
            barrier.wait(timeout=10)
            try:
                with database.atomic():
                    row = operations.guard("tenant", "technical_specification", "actor", "request", token)
                    TechnicalSpecification.create(id="copy", workspace_id="workspace", tenant_id="tenant", title="Копия", payload={})
                    operations.complete(row, {"id": "copy", "workspace_id": "workspace"})
                return "completed"
            except operations.OperationError as error:
                assert error.code == "IMPORT_INTERRUPTED"
                return "canceled"

    def cancel():
        with database.connection_context():
            barrier.wait(timeout=10)
            with database.atomic():
                return operations.cancel("tenant", "technical_specification", "actor", "request", "workspace", request)["state"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        saved, canceled = pool.submit(save), pool.submit(cancel)
        assert saved.result(timeout=20) == canceled.result(timeout=20)
    state = DocumentExchangeOperation.get().state
    assert state in {"completed", "canceled"}
    assert TechnicalSpecification.select().count() == int(state == "completed")


def test_connection_loss_does_not_replay_part_of_import_transaction(database):
    request = {"url": "https://eva.test/page", "copy": True}
    with database.atomic():
        _value, token = operations.claim("tenant", "technical_specification", "actor", "request", "workspace", request)

    with pytest.raises((OperationalError, InterfaceError)), database.atomic():
        row = operations.guard("tenant", "technical_specification", "actor", "request", token)
        TechnicalSpecification.create(id="copy", workspace_id="workspace", tenant_id="tenant", title="Копия", payload={})
        terminate_connection(database)
        operations.complete(row, {"id": "copy", "workspace_id": "workspace"})

    database.close()
    assert TechnicalSpecification.select().count() == 0
    assert DocumentExchangeOperation.get().state == "processing"
    with database.atomic():
        operations.failed("tenant", "technical_specification", "actor", "request", token, {"code": "connection_lost"})
    with database.atomic():
        _value, retry = operations.claim("tenant", "technical_specification", "actor", "request", "workspace", request)
    assert retry != token
    with database.atomic():
        row = operations.guard("tenant", "technical_specification", "actor", "request", retry)
        TechnicalSpecification.create(id="copy", workspace_id="workspace", tenant_id="tenant", title="Копия", payload={})
        operations.complete(row, {"id": "copy", "workspace_id": "workspace"})
    assert TechnicalSpecification.select().count() == 1
    assert DocumentExchangeOperation.get().state == "completed"


def test_runtime_driver_reconnects_outside_transaction(database):
    terminate_connection(database)
    assert database.execute_sql("SELECT 1").fetchone() == (1,)
