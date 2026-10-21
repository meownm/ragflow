"""Real metadata transactions and late-result cleanup in an owned QA database."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event, get_ident
from time import monotonic, sleep
from types import SimpleNamespace

import pytest
from peewee import PostgresqlDatabase

# Reuse the isolated API bootstrap; importing api.apps directly starts services.
from test.unit_test.api.apps.business_documents import test_sql_result_retention as isolated
from api.apps.business_documents import sql_query_runs as runs
from test.evals.sql_projects.database import disposable_database
from business_documents.sql_query.result_retention import RESULT_RETENTION_MS
from common.time_utils import current_timestamp

Run, Artifact, Command = isolated.Run, isolated.Artifact, isolated.Command
retention = isolated.retention


@pytest.fixture
def metadata_database():
    with disposable_database() as target:
        credentials = target["credentials"]
        database = PostgresqlDatabase(target["database"], host=target["host"], port=target["port"],
                                      user=credentials["username"], password=credentials["password"])
        tables = (Run, Artifact, Command)
        with database.bind_ctx(tables, bind_refs=False, bind_backrefs=False), database.connection_context():
            database.create_tables(tables)
            yield database


def create_run(identity, *, source=None):
    return Run.create(id=identity, tenant_id="qa-tenant", project_id="qa-project", compilation_id="qa-compilation",
                      profile_id="qa-profile", profile_version=1, kind="PYTHON" if source else "SQL", source_run_id=source,
                      status="READY", columns=["value"], rows=[[42]], row_count=1, result_bytes=20, checks={"status":"PASS"})


def age_parent(identity, deadline):
    Run.update(create_time=deadline-RESULT_RETENTION_MS).where(Run.id==identity).execute()
    assert Run.get_by_id(identity).create_time == deadline-RESULT_RETENTION_MS


def test_postgres_exact_deadline_cascades_to_fresh_derived_rows(metadata_database):
    deadline = current_timestamp()
    parent = create_run("parent")
    create_run("derived", source=parent.id)
    age_parent(parent.id, deadline)
    assert retention.expire_due_results(now_ms=deadline-1) == 0
    assert retention.expire_due_results(now_ms=deadline) == 2
    assert all(run.status=="EXPIRED" and run.rows==[] and run.columns==[] and run.checks=={} for run in Run.select())
    assert retention.expire_due_results(now_ms=deadline) == 0


def test_postgres_background_cleanup_catches_publication_after_parent_expiry(metadata_database):
    deadline = current_timestamp()
    parent = create_run("parent")
    age_parent(parent.id, deadline)
    assert retention.expire_due_results(now_ms=deadline) == 1
    create_run("late-derived", source=parent.id)
    assert retention.expire_due_results(now_ms=current_timestamp()) == 1
    assert Run.get_by_id("late-derived").status == "EXPIRED"
    assert Run.get_by_id("late-derived").rows == []


def test_postgres_parent_expiry_cannot_remove_a_fresh_cross_tenant_reference(metadata_database):
    deadline = current_timestamp()
    parent = create_run("parent")
    age_parent(parent.id, deadline)
    assert retention.expire_due_results(now_ms=deadline) == 1
    child = create_run("foreign-child", source=parent.id)
    Run.update(tenant_id="different-tenant").where(Run.id==child.id).execute()
    assert retention.expire_due_results(now_ms=current_timestamp()) == 0
    assert Run.get_by_id(child.id).status == "READY" and Run.get_by_id(child.id).rows == [[42]]
    age_parent(child.id, current_timestamp())
    assert retention.expire_due_results(now_ms=current_timestamp()) == 1
    assert Run.get_by_id(child.id).status == "EXPIRED"


def test_postgres_cleanup_failure_rolls_back_rows_conclusion_and_replay_copy(metadata_database, monkeypatch):
    deadline = current_timestamp()
    parent = create_run("parent")
    age_parent(parent.id, deadline)
    Artifact.create(id="conclusion", tenant_id="qa-tenant", project_id="qa-project", kind="CONCLUSION", revision=1,
                    source_proposal_id="qa-proposal", accepted_by="qa-actor", content_hash="qa-hash",
                    payload={"source_run_id":parent.id, "text":"private draft"})
    response={"proposal_id":"conclusion", "text":"private draft"}
    Command.create(id="replay", tenant_id="qa-tenant", project_id="qa-project", idempotency_key="qa-command",
                   request_hash="qa-hash", response=response)
    with monkeypatch.context() as patch:
        def unavailable(*_args, **_kwargs):
            raise RuntimeError("synthetic ledger failure")
        patch.setattr(Command, "update", unavailable)
        with pytest.raises(RuntimeError, match="synthetic ledger failure"):
            retention.expire_due_results(now_ms=deadline)
    assert Run.get_by_id(parent.id).status == "READY" and Run.get_by_id(parent.id).rows == [[42]]
    assert Artifact.get_by_id("conclusion").payload["text"] == "private draft"
    assert Command.get_by_id("replay").response == response
    assert retention.expire_due_results(now_ms=deadline) == 1
    assert Artifact.get_or_none(Artifact.id=="conclusion") is None
    assert Command.get_by_id("replay").response == retention.EXPIRED_RESPONSE


def test_postgres_cleanup_waits_for_an_inflight_conclusion_and_clears_its_replay(metadata_database, monkeypatch):
    deadline = current_timestamp()
    parent = create_run("parent")
    age_parent(parent.id, deadline)
    monkeypatch.setattr(runs, "current_timestamp", lambda: deadline - 1)
    project = SimpleNamespace(id="qa-project", tenant_id="qa-tenant")
    writer_thread = get_ident()
    updating = Event()
    cleaner_pid = []
    execute = metadata_database.execute_sql

    def observe(query, *args, **kwargs):
        if get_ident() != writer_thread and query.startswith('UPDATE "business_document_sql_query_run"'):
            updating.set()
        return execute(query, *args, **kwargs)

    monkeypatch.setattr(metadata_database, "execute_sql", observe)

    def clean():
        with metadata_database.connection_context():
            cleaner_pid.append(metadata_database.execute_sql("SELECT pg_backend_pid()").fetchone()[0])
            return retention.expire_project_results("qa-tenant", "qa-project", now_ms=deadline)

    with ThreadPoolExecutor(max_workers=1) as executor:
        with metadata_database.atomic():
            # Final source check and publication belong to the same transaction.
            monkeypatch.setattr(retention, "current_timestamp", lambda: deadline - 1)
            assert runs._retained_sql_result(project, parent.id) is not None
            writer_pid = metadata_database.execute_sql("SELECT pg_backend_pid()").fetchone()[0]
            cleanup = executor.submit(clean)
            assert updating.wait(5), "Cleaner never reached the competing row update"
            blocked = False
            until = monotonic() + 5
            while monotonic() < until and not cleanup.done():
                blockers = metadata_database.execute_sql("SELECT pg_blocking_pids(%s)", (cleaner_pid[0],)).fetchone()[0]
                if writer_pid in blockers:
                    blocked = True
                    break
                sleep(0.01)
            Artifact.create(id="late-conclusion", tenant_id="qa-tenant", project_id="qa-project", kind="CONCLUSION", revision=1,
                            source_proposal_id=parent.id, accepted_by="qa-actor", content_hash="qa-hash",
                            payload={"source_run_id": parent.id, "text": "private late draft"})
            isolated.Agents._record_command("qa-tenant", "qa-project", "late-command", "qa-hash",
                                           {"proposal_id": "late-conclusion", "text": "private late draft"})
        assert cleanup.result(timeout=5) == 1
    assert blocked, "Cleanup committed between the final source check and conclusion publication"
    assert Run.get_by_id(parent.id).status == "EXPIRED"
    assert Artifact.get_or_none(Artifact.id == "late-conclusion") is None
    assert Command.get(Command.idempotency_key == "late-command").response == retention.EXPIRED_RESPONSE


def test_postgres_publication_waits_for_cleanup_then_rejects_the_expired_source(metadata_database, monkeypatch):
    deadline = current_timestamp()
    parent = create_run("parent")
    age_parent(parent.id, deadline)
    monkeypatch.setattr(retention, "current_timestamp", lambda: deadline - 1)
    monkeypatch.setattr(runs, "current_timestamp", lambda: deadline - 1)
    project = SimpleNamespace(id="qa-project", tenant_id="qa-tenant")
    connected = Event()
    writer_pid = []

    def publish():
        with metadata_database.connection_context(), metadata_database.atomic():
            writer_pid.append(metadata_database.execute_sql("SELECT pg_backend_pid()").fetchone()[0])
            connected.set()
            try:
                runs._retained_sql_result(project, parent.id)
            except isolated.BusinessDocumentError as error:
                return error.code, error.status
            return "UNEXPECTED_PUBLICATION", 200

    with ThreadPoolExecutor(max_workers=1) as executor:
        with metadata_database.atomic():
            assert retention.expire_project_results("qa-tenant", "qa-project", now_ms=deadline) == 1
            cleaner_pid = metadata_database.execute_sql("SELECT pg_backend_pid()").fetchone()[0]
            publication = executor.submit(publish)
            assert connected.wait(5)
            blocked = False
            until = monotonic() + 5
            while monotonic() < until and not publication.done():
                blockers = metadata_database.execute_sql("SELECT pg_blocking_pids(%s)", (writer_pid[0],)).fetchone()[0]
                if cleaner_pid in blockers:
                    blocked = True
                    break
                sleep(0.01)
        assert publication.result(timeout=5) == ("SQL_RESULT_EXPIRED", 410)
    assert blocked, "Final source check did not wait for the cleanup transaction"
    assert Run.get_by_id(parent.id).status == "EXPIRED"
    assert Artifact.select().count() == Command.select().count() == 0
