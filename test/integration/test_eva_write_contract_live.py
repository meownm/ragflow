"""Opt-in Eva CAS probe using only new disposable local QA pages."""

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit
from uuid import uuid4

import pytest


def test_eva_create_draft_cas_and_publication_version_guards():
    if os.environ.get("RAGFLOW_LOCAL_DOCKER_CONNECTOR_TEST") != "1":
        pytest.skip("Requires the local Docker QA connector contour")
    from api.db.db_models import Connector
    from common.data_source.eva_wiki_connector import EvaWikiConnector
    from common.data_source.exceptions import ConnectorValidationError

    row = Connector.get_or_none(Connector.name == "EVA Wiki native smoke")
    assert row is not None, "Dedicated QA connector missing"
    config = dict(row.config)
    credentials = config.pop("credentials")
    config.pop("sync_deleted_files", None)
    assert urlsplit(config["api_base_url"]).hostname in {"host.docker.internal", "localhost", "127.0.0.1", "eva-wiki-http"}
    config.update(include_attachments=False, retry_count=0)

    def connect():
        client = EvaWikiConnector(**config)
        client.load_credentials(credentials)
        return client

    client = connect()
    marker = uuid4().hex
    page_id = "CmfDocument:" + str(uuid4())
    name = "TZ MVP contract " + marker
    code = "TZ-QA-" + marker[:16]
    identity = [["id", "==", page_id], ["project_id", "==", config["project_id"]]]
    by_name = [["name", "==", name], ["project_id", "==", config["project_id"]]]

    def read():
        return client._rpc("CmfDocument.get", {"fields": ["id", "name", "code", "project_id", "cmf_version", "text", "text_draft"], "filter": identity}, timeout=15)

    def conditional(connection, expected, body):
        try:
            result = connection._rpc(
                "CmfDocument.upsert",
                {},
                timeout=15,
                call_kwargs={"filter": [*identity, ["cmf_version", "==", expected]], "id": page_id, "project": config["project_id"], "name": name, "code": code, "text_draft": body},
            )
            assert (result.get("id") if isinstance(result, dict) else result) == page_id
            return True
        except ConnectorValidationError as error:
            # A network failure must fail this probe, not count as CAS rejection.
            assert "EVA Wiki API error in CmfDocument.upsert:" in str(error)
            return False

    try:
        created = client._rpc("CmfDocument.create", {}, timeout=15, call_kwargs={"id": page_id, "project": config["project_id"], "name": name, "code": code, "text_draft": "<p>baseline</p>"})
        assert created == page_id
        page = read()
        assert page and page["name"] == name and page["project_id"] == config["project_id"]
        code = page["code"]
        initial = page["cmf_version"]
        assert initial
        assert conditional(client, initial, "<p>first</p>")
        assert not conditional(client, initial, "<p>stale</p>")
        page = read()
        assert page["text_draft"] == "<p>first</p>"
        expected = page["cmf_version"]
        barrier = threading.Barrier(3)

        def race(number):
            connection = connect()
            try:
                barrier.wait(timeout=5)
                return conditional(connection, expected, f"<p>race {number}</p>")
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(race, number) for number in (1, 2)]
            barrier.wait(timeout=5)
            assert sum(future.result(timeout=25) for future in futures) == 1
        assert read()["text_draft"] in {"<p>race 1</p>", "<p>race 2</p>"}
        rows = client._rpc("CmfDocument.list", {"fields": ["id"], "filter": by_name}, timeout=15)
        assert [page["id"] for page in rows] == [page_id]
        # These guards are unsafe in this Eva version: publication ignores both.
        # Keep this characterization explicit so draft CAS is not mistaken for
        # a supported product publication contract. No notifications are sent.
        client._rpc("CmfDocument.do_publish", {}, args=[page_id], call_kwargs={"notify_persons": False}, timeout=15)
        for mode in ("filter", "keyword"):
            before = read()
            candidate = f"<p>publication guard {mode}</p>"
            assert conditional(client, before["cmf_version"], candidate)
            assert read()["cmf_version"] != before["cmf_version"]
            stale = before["cmf_version"]
            filters = {"filter": [*identity, ["cmf_version", "==", stale]]} if mode == "filter" else {}
            arguments = {"notify_persons": False, **({"cmf_version": stale} if mode == "keyword" else {})}
            client._rpc("CmfDocument.do_publish", filters, args=[page_id], call_kwargs=arguments, timeout=15)
            assert read()["text"] == candidate, "Eva publication contract changed; requalify the guarded product scenario"
    finally:
        try:
            # Reconcile a lost create response by the marker, never create again.
            rows = client._rpc("CmfDocument.list", {"fields": ["id", "name", "code", "project_id"], "filter": by_name}, timeout=15)
            for page in rows:
                assert page["name"] == name and page["project_id"] == config["project_id"] and page["code"].endswith(marker[:16])
                client._rpc("CmfDocument.delete", {}, args=[page["id"]], timeout=15)
            assert not client._rpc("CmfDocument.list", {"fields": ["id"], "filter": by_name}, timeout=15)
        finally:
            client.close()
