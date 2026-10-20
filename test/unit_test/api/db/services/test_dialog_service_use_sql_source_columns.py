#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
import asyncio
import sqlite3
import sys
import types
import warnings
from types import SimpleNamespace

import pytest

# xgboost imports pkg_resources and emits a deprecation warning that is promoted
# to error in our pytest configuration; ignore it for this unit test module.
warnings.filterwarnings(
    "ignore",
    message="pkg_resources is deprecated as an API.*",
    category=UserWarning,
)


def _install_cv2_stub_if_unavailable():
    try:
        import cv2  # noqa: F401

        return
    except Exception:
        pass

    stub = types.ModuleType("cv2")

    # Constants referenced by deepdoc import-time defaults.
    stub.INTER_LINEAR = 1
    stub.INTER_CUBIC = 2
    stub.BORDER_CONSTANT = 0
    stub.BORDER_REPLICATE = 1
    stub.COLOR_BGR2RGB = 0
    stub.COLOR_BGR2GRAY = 1
    stub.COLOR_GRAY2BGR = 2
    stub.IMREAD_IGNORE_ORIENTATION = 128
    stub.IMREAD_COLOR = 1
    stub.RETR_LIST = 1
    stub.CHAIN_APPROX_SIMPLE = 2

    def _missing(*_args, **_kwargs):
        raise RuntimeError("cv2 runtime call is unavailable in this test environment")

    def _module_getattr(name):
        if name.isupper():
            return 0
        return _missing

    stub.__getattr__ = _module_getattr
    sys.modules["cv2"] = stub


_install_cv2_stub_if_unavailable()

from api.db.services import dialog_service


class _StubChatModel:
    def __init__(self, outputs):
        self._outputs = outputs
        self.calls = []

    async def async_chat(self, system_prompt, messages, llm_setting):
        idx = len(self.calls)
        if idx >= len(self._outputs):
            raise AssertionError("async_chat called more times than expected")
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "message": messages[0]["content"],
                "llm_setting": llm_setting,
            }
        )
        return self._outputs[idx]


class _StubRetriever:
    def __init__(self, results):
        self._results = results
        self.sql_calls = []

    def sql_retrieval(self, sql, format="json"):
        assert format == "json"
        idx = len(self.sql_calls)
        if idx >= len(self._results):
            raise AssertionError("sql_retrieval called more times than expected")
        self.sql_calls.append(sql)
        return self._results[idx]


class _StubAsyncRetriever:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def retrieval(self, *args, **kwargs):
        self.calls.append({"args": args, "kwargs": kwargs})
        return self.result

    def retrieval_by_children(self, chunks, tenant_ids):
        return chunks


@pytest.mark.p1
def test_use_sql_scopes_aggregate_citations_with_alias(monkeypatch, force_es_engine):
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute("CREATE TABLE ragflow_tenant(doc_id TEXT, docnm_kwd TEXT, kb_id TEXT, amount INT)")
        allowed = "11111111-1111-4111-8111-111111111111"
        foreign = "22222222-2222-4222-8222-222222222222"
        connection.executemany("INSERT INTO ragflow_tenant VALUES (?,?,?,?)", [("own", "Own", allowed, 1), ("private", "Private", foreign, 2)])
        calls = []

        def execute(sql, format="json"):
            calls.append(sql)
            cursor = connection.execute(sql)
            return {"columns": [{"name": column[0]} for column in cursor.description], "rows": cursor.fetchall()}

        monkeypatch.setattr(dialog_service.settings, "retriever", SimpleNamespace(sql_retrieval=execute))
        model = _StubChatModel(["SELECT COUNT(*) FROM ragflow_tenant AS t WHERE t.amount=1 OR t.amount=2"])
        result = asyncio.run(dialog_service.use_sql("count", {"amount": "amount"}, "tenant", model, kb_ids=[allowed]))
        assert "|1|" in result["answer"]
        assert [chunk["doc_id"] for chunk in result["reference"]["chunks"]] == ["own"]
        assert len(calls) == 2
        assert all(allowed in sql for sql in calls)
    finally:
        connection.close()


@pytest.mark.p1
@pytest.mark.parametrize("initial_error", [False, True])
def test_use_sql_never_executes_unscoped_model_or_repair(monkeypatch, force_es_engine, initial_error):
    calls = []

    def execute(sql, format="json"):
        calls.append(sql)
        raise RuntimeError("controlled engine failure")

    monkeypatch.setattr(dialog_service.settings, "retriever", SimpleNamespace(sql_retrieval=execute))
    queries = ["SELECT doc_id FROM private_tenant", "SELECT doc_id FROM private_tenant"]
    if initial_error:
        queries[0] = "SELECT doc_id FROM ragflow_tenant"
    model = _StubChatModel(queries)
    result = asyncio.run(dialog_service.use_sql("question", {"amount": "amount"}, "tenant", model, kb_ids=["11111111-1111-4111-8111-111111111111"]))
    assert result is None
    assert len(calls) == int(initial_error)
    assert len(model.calls) == 2


@pytest.mark.p1
@pytest.mark.parametrize("engine", ["es", "os"])
@pytest.mark.parametrize("predicate", ["content_ltks like 'weather'", "content_ltks like 'weather' OR amount=2"])
def test_scoped_sql_keeps_es_text_predicate_preprocessing(engine, predicate):
    from inspect import getclosurevars

    from common.doc_store.es_conn_base import ESConnectionBase
    from common.sql_scope import scope_retrieval_sql
    from rag.utils.opensearch_conn import OSConnection

    calls = []
    connection = sqlite3.connect(":memory:")
    try:
        connection.create_function("MATCH", 3, lambda *_args: 1)
        connection.execute("CREATE TABLE ragflow_tenant(doc_id TEXT, kb_id TEXT, content_ltks TEXT, amount INT)")
        connection.executemany("INSERT INTO ragflow_tenant VALUES (?,?,?,?)", [("own", "11111111-1111-4111-8111-111111111111", "weather", 1), ("private", "22222222-2222-4222-8222-222222222222", "weather", 2)])

        def query(**kwargs):
            sql = kwargs["body"]["query"]
            calls.append(sql)
            return {"rows": connection.execute(sql).fetchall()}

        client = SimpleNamespace(sql=SimpleNamespace(query=query))
        receiver = SimpleNamespace(es=client, os=client, logger=dialog_service.logger)
        sql = scope_retrieval_sql("SELECT doc_id FROM ragflow_tenant WHERE " + predicate, "ragflow_tenant", engine, ["11111111-1111-4111-8111-111111111111"])
        # Inspect the singleton's class without opening an OpenSearch connection.
        adapter = ESConnectionBase if engine == "es" else getclosurevars(OSConnection).nonlocals["cls"]
        result = adapter.sql(receiver, sql, 128, "json")
        assert result["rows"] == [("own",)]
    finally:
        connection.close()
    assert len(calls) == 1
    assert "MATCH(content_ltks," in calls[0]
    assert "11111111-1111-4111-8111-111111111111" in calls[0]


@pytest.fixture
def force_es_engine(monkeypatch):
    monkeypatch.setattr(dialog_service.settings, "DOC_ENGINE_INFINITY", False)
    monkeypatch.setattr(dialog_service.settings, "DOC_ENGINE_OCEANBASE", False)
    monkeypatch.setattr(dialog_service, "index_name", lambda _tenant: "ragflow_tenant")


@pytest.mark.p1
@pytest.mark.parametrize("column", ["kb_id", "kb_id_kwd", "KB_ID_KWD"])
def test_use_sql_cannot_read_metadata_via_forged_kb_projection(monkeypatch, force_es_engine, column):
    from api.db.services.doc_metadata_service import DocMetadataService

    allowed = ["11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222"]
    foreign = "33333333-3333-4333-8333-333333333333"
    metadata_calls = []

    def search_metadata(kb_id, **kwargs):
        metadata_calls.append(kb_id)
        return [{"doc_id": "private-doc", "meta_fields": {"secret": "PRIVATE_SENTINEL"}}]

    monkeypatch.setattr(DocMetadataService, "_search_metadata", search_metadata)
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute("CREATE TABLE ragflow_tenant(kb_id TEXT, amount INT)")
        connection.execute("INSERT INTO ragflow_tenant VALUES (?, 1)", (allowed[0],))

        def execute(sql, format="json"):
            cursor = connection.execute(sql)
            return {"columns": [{"name": field[0]} for field in cursor.description], "rows": cursor.fetchall()}

        monkeypatch.setattr(dialog_service.settings, "retriever", SimpleNamespace(sql_retrieval=execute))
        model = _StubChatModel([f"SELECT 'private-doc' AS doc_id, 'Private' AS docnm_kwd, '{foreign}' AS {column}, amount FROM ragflow_tenant"])
        result = asyncio.run(dialog_service.use_sql("list amount", {"amount": "amount"}, "tenant", model, kb_ids=allowed))
        chunks = result["reference"]["chunks"]
        dialog_service._enrich_chunks_with_document_metadata(chunks)
        assert chunks
        assert all(chunk.get("kb_id") in allowed or "kb_id" not in chunk for chunk in chunks)
        assert all("document_metadata" not in chunk for chunk in chunks)
        assert metadata_calls == []
    finally:
        connection.close()


@pytest.mark.parametrize("column", ["kb_id", "kb_id_kwd", "KB_ID_KWD"])
def test_source_kb_accepts_authorized_aliases_only(column):
    assert dialog_service._chunk_kb_id_for_doc({column: "kb_b"}, ["kb_a", "kb_b"]) == "kb_b"
    assert dialog_service._chunk_kb_id_for_doc({column: "kb_c"}, ["kb_a", "kb_b"]) is None
    assert dialog_service._chunk_kb_id_for_doc({column: "kb_a"}, []) is None


@pytest.mark.p2
def test_use_sql_repairs_missing_source_columns_for_non_aggregate(monkeypatch, force_es_engine):
    retriever = _StubRetriever(
        [
            {
                "columns": [{"name": "product"}],
                "rows": [["desk"], ["monitor"]],
            },
            {
                "columns": [{"name": "doc_id"}, {"name": "docnm_kwd"}, {"name": "product"}],
                "rows": [["doc-1", "products.xlsx", "desk"], ["doc-2", "products.xlsx", "monitor"]],
            },
        ]
    )
    chat_model = _StubChatModel(
        [
            "SELECT product FROM ragflow_tenant",
            "SELECT doc_id, docnm_kwd, product FROM ragflow_tenant",
        ]
    )
    monkeypatch.setattr(dialog_service.settings, "retriever", retriever, raising=False)

    result = asyncio.run(
        dialog_service.use_sql(
            question="show me column of product",
            field_map={"product": "product"},
            tenant_id="tenant-id",
            chat_mdl=chat_model,
            quota=True,
            kb_ids=["11111111-1111-4111-8111-111111111111"],
        )
    )

    assert result is not None
    assert "|product|Source|" in result["answer"]
    answer_lines = [ln.strip() for ln in result["answer"].splitlines() if ln.strip().startswith("|")]
    header, separator = answer_lines[0], answer_lines[1]
    assert header.count("|") == separator.count("|")
    assert len(chat_model.calls) == 2
    assert len(retriever.sql_calls) == 2


@pytest.mark.p2
def test_use_sql_separator_matches_header_without_doc_name(monkeypatch, force_es_engine):
    retriever = _StubRetriever(
        [
            {
                "columns": [{"name": "doc_id"}, {"name": "product"}],
                "rows": [["doc-1", "desk"]],
            },
        ]
    )
    chat_model = _StubChatModel(["SELECT doc_id, product FROM ragflow_tenant"])
    monkeypatch.setattr(dialog_service.settings, "retriever", retriever, raising=False)

    result = asyncio.run(
        dialog_service.use_sql(
            question="show product with doc id only",
            field_map={"product": "product"},
            tenant_id="tenant-id",
            chat_mdl=chat_model,
            quota=True,
            kb_ids=["11111111-1111-4111-8111-111111111111"],
        )
    )

    assert result is not None
    answer_lines = [ln.strip() for ln in result["answer"].splitlines() if ln.strip().startswith("|")]
    assert answer_lines[0] == "|product|"
    assert answer_lines[1] == "|------"
    assert "|------|------|" not in result["answer"]


@pytest.mark.p2
def test_use_sql_keeps_aggregate_flow_without_source_repair(monkeypatch, force_es_engine):
    retriever = _StubRetriever(
        [
            {
                "columns": [{"name": "count(star)"}],
                "rows": [[6]],
            },
        ]
    )
    chat_model = _StubChatModel(
        [
            "SELECT COUNT(*) FROM ragflow_tenant",
        ]
    )
    monkeypatch.setattr(dialog_service.settings, "retriever", retriever, raising=False)

    result = asyncio.run(
        dialog_service.use_sql(
            question="how many rows are there",
            field_map={"product": "product"},
            tenant_id="tenant-id",
            chat_mdl=chat_model,
            quota=True,
            kb_ids=["11111111-1111-4111-8111-111111111111"],
        )
    )

    assert result is not None
    assert "|COUNT(*)|" in result["answer"]
    assert "Source" not in result["answer"]
    assert len(chat_model.calls) == 1
    assert len(retriever.sql_calls) == 1


@pytest.mark.p2
def test_use_sql_source_repair_is_bounded_to_single_retry(monkeypatch, force_es_engine):
    retriever = _StubRetriever(
        [
            {
                "columns": [{"name": "product"}],
                "rows": [["desk"]],
            },
            {
                "columns": [{"name": "product"}],
                "rows": [["desk"]],
            },
        ]
    )
    chat_model = _StubChatModel(
        [
            "SELECT product FROM ragflow_tenant",
            "SELECT product FROM ragflow_tenant WHERE product IS NOT NULL",
        ]
    )
    monkeypatch.setattr(dialog_service.settings, "retriever", retriever, raising=False)

    result = asyncio.run(
        dialog_service.use_sql(
            question="show me column of product",
            field_map={"product": "product"},
            tenant_id="tenant-id",
            chat_mdl=chat_model,
            quota=True,
            kb_ids=["11111111-1111-4111-8111-111111111111"],
        )
    )

    assert result is not None
    assert "|product|" in result["answer"]
    assert "Source" not in result["answer"]
    assert len(chat_model.calls) == 2
    assert len(retriever.sql_calls) == 2


@pytest.mark.p2
def test_async_chat_uses_all_docs_when_no_doc_ids_selected(monkeypatch):
    retriever = _StubAsyncRetriever(
        {
            "total": 1,
            "chunks": [
                {
                    "chunk_id": "chunk-1",
                    "content_ltks": "chunk text",
                    "content_with_weight": "Chunk text from dataset.",
                    "doc_id": "doc-1",
                    "docnm_kwd": "doc.txt",
                    "kb_id": "kb-1",
                    "important_kwd": [],
                    "positions": [],
                    "vector": [0.1, 0.2],
                }
            ],
            "doc_aggs": [],
        }
    )
    chat_model = _StubChatModel(["stub answer"])
    dialog = SimpleNamespace(
        kb_ids=["kb-1"],
        llm_id="chat-model",
        tenant_id="tenant-id",
        llm_setting={},
        similarity_threshold=0.1,
        vector_similarity_weight=0.2,
        top_n=8,
        top_k=32,
        meta_data_filter=None,
        prompt_config={
            "quote": False,
            "keyword": False,
            "tts": False,
            "empty_response": "",
            "system": "Use only this knowledge: {knowledge}",
            "parameters": [{"key": "knowledge", "optional": False}],
            "reasoning": False,
            "toc_enhance": False,
            "use_kg": False,
        },
    )

    monkeypatch.setattr(dialog_service.settings, "retriever", retriever, raising=False)
    monkeypatch.setattr(dialog_service, "get_model_type_by_name", lambda _tid, _llm_id: ["chat"])
    monkeypatch.setattr(
        dialog_service,
        "get_model_config_from_provider_instance",
        lambda *_args, **_kwargs: {"llm_factory": "unit", "max_tokens": 4096, "model_type": "chat"},
    )
    monkeypatch.setattr(dialog_service.TenantLangfuseService, "filter_by_tenant", lambda **_kwargs: None)
    monkeypatch.setattr(
        dialog_service,
        "get_models",
        lambda _dialog, **_kwargs: ([SimpleNamespace(tenant_id="tenant-id")], object(), None, chat_model, None),
    )
    monkeypatch.setattr(dialog_service.KnowledgebaseService, "get_field_map", lambda _kb_ids: {})
    monkeypatch.setattr(dialog_service, "label_question", lambda _question, _kbs: None)
    monkeypatch.setattr(
        dialog_service,
        "kb_prompt",
        lambda kbinfos, _max_tokens: ["Chunk text from dataset."] if kbinfos["chunks"] else [],
    )
    monkeypatch.setattr(dialog_service, "message_fit_in", lambda msg, _max_tokens: (0, msg))

    async def _collect():
        items = []
        async for item in dialog_service.async_chat(dialog, [{"role": "user", "content": "What does the dataset say?"}], stream=False):
            items.append(item)
        return items

    result = asyncio.run(_collect())

    assert len(retriever.calls) == 1
    assert retriever.calls[0]["kwargs"]["doc_ids"] is None
    assert "Chunk text from dataset." in chat_model.calls[0]["system_prompt"]
    assert result[0]["answer"] == "stub answer"
