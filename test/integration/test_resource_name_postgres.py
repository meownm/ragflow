"""Actual PostgreSQL indexes and concurrent writes in an isolated test schema."""

import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import PurePath
import re
from threading import Barrier, Lock
from types import SimpleNamespace
from uuid import uuid4

import peewee
import pytest
from playhouse.db_url import connect
from playhouse.postgres_ext import JSONField

from api.db.resource_name_schema import install_resource_name_indexes, is_resource_name_conflict
from common.constants import ParserType, StatusEnum
from test.unit_test.test_team_resource_visibility import load_definitions


@pytest.fixture
def names_database():
    dsn = os.environ.get("RESOURCE_NAME_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("Set RESOURCE_NAME_TEST_POSTGRES_DSN to a disposable loopback PostgreSQL")
    from urllib.parse import urlsplit

    assert urlsplit(dsn).hostname in {"127.0.0.1", "localhost"}
    database = connect(dsn)
    assert isinstance(database, peewee.PostgresqlDatabase)
    schema = "resource_names_" + uuid4().hex

    class Base(peewee.Model):
        create_time = peewee.IntegerField(default=1)
        update_time = peewee.IntegerField(default=1)

        class Meta:
            database = None

    ns = {**vars(peewee), "DataBaseModel": Base, "JSONField": JSONField, "LongTextField": peewee.TextField, "ParserType": ParserType, "os": os}
    load_definitions("api/db/db_models.py", ["Dialog", "UserCanvas", "Knowledgebase"], ns, methods={"Meta"})
    models = [ns[name] for name in ("Dialog", "UserCanvas", "Knowledgebase")]
    database.connect()
    database.execute_sql(f'CREATE SCHEMA "{schema}"')
    try:
        database.execute_sql(f'SET search_path TO "{schema}"')
        for model in models:
            model.bind(database)
            model._meta.schema = schema
        database.create_tables(models)
        install_resource_name_indexes(database)

        def insert(model, identity, name, owner="owner", **extras):
            payload = dict(id=identity, **extras)
            payload.update({"user_id": owner, "title": name} if model is ns["UserCanvas"] else {"tenant_id": owner, "name": name})
            for field_name, field in model._meta.fields.items():
                if field_name not in payload and not field.null and field.default is None:
                    payload[field_name] = "" if isinstance(field, peewee.CharField) else 0
            return model.create(**payload)

        yield SimpleNamespace(DB=database, ns=ns, models=models, insert=insert, schema=schema)
    finally:
        database.execute_sql("SET search_path TO public")
        database.execute_sql(f'DROP SCHEMA "{schema}" CASCADE')
        database.close()


@pytest.mark.parametrize("model_name,index", [("Dialog", "dialog_owner_name_unique"), ("UserCanvas", "user_canvas_owner_title_unique"), ("Knowledgebase", "knowledgebase_owner_name_unique")])
def test_postgres_concurrent_case_collisions(names_database, model_name, index):
    r = names_database
    model = r.ns[model_name]
    barrier = Barrier(2)

    def write(i):
        with r.DB.connection_context():
            r.DB.execute_sql(f'SET search_path TO "{r.schema}"')
            barrier.wait(timeout=10)
            try:
                r.insert(model, f"race-{i}", ["Проект", "ПРОЕКТ"][i])
                return "saved"
            except peewee.IntegrityError as exc:
                assert is_resource_name_conflict(exc, index)
                return "conflict"

    with ThreadPoolExecutor(max_workers=2) as workers:
        assert sorted(workers.map(write, range(2))) == ["conflict", "saved"]
    assert model.select().count() == 1
    r.insert(model, "other-owner", "проект", owner="other")
    if model_name == "UserCanvas":
        r.insert(model, "other-category", "проект", canvas_category="dataflow_canvas")
    else:
        r.insert(model, "deleted", "проект", status="0")
    install_resource_name_indexes(r.DB)


def test_postgres_dataset_retry_uses_savepoint_after_actual_conflict(names_database):
    r = names_database
    ns = r.ns
    ns.update(DB=r.DB, CommonService=object, StatusEnum=StatusEnum, PurePath=PurePath, re=re, DATASET_NAME_LIMIT=128, is_resource_name_conflict=is_resource_name_conflict)
    load_definitions("api/db/services/__init__.py", ["duplicate_name", "_split_name_counter"], ns)
    load_definitions("api/db/services/knowledgebase_service.py", ["KnowledgebaseService"], ns, methods={"query_by_name_case_insensitive", "save_with_unique_name"})
    service = ns["KnowledgebaseService"]
    original_query = service.query_by_name_case_insensitive
    barrier, lock, queries = Barrier(2), Lock(), 0

    def query(**kwargs):
        nonlocal queries
        rows = list(original_query(**kwargs))
        with lock:
            queries += 1
            ordinal = queries
        if ordinal <= 2:
            barrier.wait(timeout=10)
        return rows

    service.query_by_name_case_insensitive = staticmethod(query)

    def create(i):
        with r.DB.connection_context():
            r.DB.execute_sql(f'SET search_path TO "{r.schema}"')
            payload = dict(id=f"kb-{i}", tenant_id="owner", name="Я" * 128, created_by="owner", embd_id="test")
            service.save_with_unique_name(payload)
            return payload["name"]

    with ThreadPoolExecutor(max_workers=2) as workers:
        names = list(workers.map(create, range(2)))
    assert set(names) == {"Я" * 128, "Я" * 125 + "(1)"}
    assert r.ns["Knowledgebase"].select().count() == 2


def test_postgres_migration_rejects_existing_duplicates_atomically(names_database):
    r = names_database
    r.DB.execute_sql("DROP INDEX dialog_owner_name_unique")
    r.insert(r.ns["Dialog"], "a", "Private-name")
    r.insert(r.ns["Dialog"], "b", "PRIVATE-NAME")
    with pytest.raises(RuntimeError, match="Resolve 1 duplicate name groups in dialog") as caught:
        install_resource_name_indexes(r.DB)
    assert "private" not in str(caught.value).lower()
    assert r.ns["Dialog"].select().count() == 2


def test_postgres_late_collision_rolls_back_all_new_indexes(names_database):
    r = names_database
    for index in ("dialog_owner_name_unique", "user_canvas_owner_title_unique", "knowledgebase_owner_name_unique"):
        r.DB.execute_sql(f"DROP INDEX {index}")
    r.insert(r.ns["Knowledgebase"], "a", "Private-name")
    r.insert(r.ns["Knowledgebase"], "b", "PRIVATE-NAME")
    with pytest.raises(RuntimeError, match="knowledgebase"):
        install_resource_name_indexes(r.DB)
    count = r.DB.execute_sql(
        "SELECT count(*) FROM pg_indexes WHERE schemaname = %s AND indexname IN (%s, %s, %s)",
        (r.schema, "dialog_owner_name_unique", "user_canvas_owner_title_unique", "knowledgebase_owner_name_unique"),
    ).fetchone()[0]
    assert count == 0
