"""Saved Retrieval Canvas contract using the actual tool registry."""

import json
import os
import subprocess
import sys
from pathlib import Path


def test_retrieval_canvas_loads_through_dynamic_tool_registry(tmp_path):
    fixture = tmp_path / "retrieval-canvas.json"
    fixture.write_text(
        json.dumps(
            {
                "components": {
                    "Retrieval:Contract": {
                        "obj": {
                            "component_name": "Retrieval",
                            "params": {
                                "dataset_ids": [],
                                "kb_ids": ["legacy-dataset-id"],
                                "memory_ids": ["memory-id"],
                                "meta_data_filter": {},
                                "similarity_threshold": 0.2,
                                "keywords_similarity_weight": 0.5,
                                "top_n": 8,
                                "top_k": 1024,
                            },
                        },
                        "downstream": [],
                        "upstream": [],
                    }
                },
                "history": [],
                "path": [],
                "retrieval": {"chunks": [], "doc_aggs": []},
                "globals": {
                    "sys.query": "",
                    "sys.user_id": "isolated-test",
                    "sys.conversation_turns": 0,
                    "sys.files": [],
                },
            }
        ),
        encoding="utf-8",
    )
    script = r"""
import json
import socket
import sys
import contextvars
import types


def install_infrastructure_stub(name, **attributes):
    module = types.ModuleType(name)
    for attribute, value in attributes.items():
        setattr(module, attribute, value)
    sys.modules[name] = module


def install_infrastructure_package(name):
    module = types.ModuleType(name)
    module.__path__ = []
    sys.modules[name] = module


class StubService:
    pass


class StubToolCallSession:
    pass


class StubRedis:
    def delete(self, *_args, **_kwargs):
        return None


def passthrough_timeout(*_args, **_kwargs):
    return lambda function: function


async def empty_cross_languages(*_args, **_kwargs):
    return ""


# Keep the real Retrieval class, parameter validation, registry discovery, and
# Canvas serialization. Only infrastructure that the contract never invokes is
# replaced, avoiding a dependency on database, search, cache, or MCP runtimes.
install_infrastructure_stub("common.connection_utils", timeout=passthrough_timeout)
install_infrastructure_stub(
    "common.token_utils",
    token_usage_sink=contextvars.ContextVar("token_usage_sink", default=None),
    langfuse_run_attrs=contextvars.ContextVar("langfuse_run_attrs", default=None),
)
install_infrastructure_stub(
    "common.llm_request_context",
    set_llm_request_context=lambda *_args, **_kwargs: None,
    reset_llm_request_context=lambda *_args, **_kwargs: None,
)
install_infrastructure_stub(
    "common.mcp_tool_call_conn",
    MCPToolBinding=StubToolCallSession,
    MCPToolCallSession=StubToolCallSession,
    ToolCallSession=StubToolCallSession,
)
install_infrastructure_stub("common.metadata_utils", apply_meta_data_filter=lambda *_args, **_kwargs: [])
install_infrastructure_stub("common.settings")
install_infrastructure_package("api.db.services")
install_infrastructure_stub("api.db.services.doc_metadata_service", DocMetadataService=StubService)
install_infrastructure_stub("api.db.services.file_service", FileService=StubService)
install_infrastructure_stub("api.db.services.knowledgebase_service", KnowledgebaseService=StubService)
install_infrastructure_stub("api.db.services.llm_service", LLMBundle=StubService)
install_infrastructure_stub("api.db.services.memory_service", MemoryService=StubService)
install_infrastructure_stub("api.db.services.task_service", has_canceled=lambda *_args, **_kwargs: False)
install_infrastructure_stub(
    "api.db.joint_services.memory_message_service",
    queue_save_to_memory_task=lambda *_args, **_kwargs: None,
    query_message=lambda *_args, **_kwargs: [],
)
install_infrastructure_stub(
    "api.db.joint_services.tenant_model_service",
    get_tenant_default_model_by_type=lambda *_args, **_kwargs: None,
    get_model_config_from_provider_instance=lambda *_args, **_kwargs: None,
)
install_infrastructure_stub("api.utils.file_utils", is_video_filename=lambda *_args, **_kwargs: False)
install_infrastructure_stub("rag.app.tag", label_question=lambda *_args, **_kwargs: None)
install_infrastructure_stub(
    "rag.prompts.generator",
    chunks_format=lambda *_args, **_kwargs: [],
    cross_languages=empty_cross_languages,
    kb_prompt=lambda *_args, **_kwargs: "",
    memory_prompt=lambda *_args, **_kwargs: [],
)
install_infrastructure_stub("rag.utils.redis_conn", REDIS_CONN=StubRedis())
install_infrastructure_stub("rag.utils.tts_cache", synthesize_with_cache=lambda *_args, **_kwargs: None)

blocked_network = []
socketpair_code = getattr(socket.socketpair, "__code__", None)

def deny_network(event, args):
    if event not in {"socket.connect", "socket.getaddrinfo", "socket.sendto"}:
        return
    # Windows asyncio creates a private socketpair via a temporary TCP listener.
    # Allow only that stdlib frame connecting to its own just-created listener.
    caller = sys._getframe(1)
    if event == "socket.connect" and caller.f_code is socketpair_code:
        listener = caller.f_locals.get("lsock")
        if listener is not None and args[1] == listener.getsockname()[:2]:
            return
    blocked_network.append(event)
    raise RuntimeError("Dynamic-registry test forbids external network access")

sys.addaudithook(deny_network)

# This contract verifies registry and Canvas persistence, not synonym lookup.
# Avoid importing the external WordNet corpus as an unrelated subprocess side effect.
from nltk.corpus import wordnet

wordnet.ensure_loaded = lambda: None

from agent.component import component_class
from agent.canvas import Canvas

assert component_class("Retrieval").__module__ == "agent.tools.retrieval"
assert component_class("RetrievalParam").__module__ == "agent.tools.retrieval"
with open(sys.argv[1], encoding="utf-8") as source:
    document = json.load(source)
canvas = Canvas(json.dumps(document), tenant_id="isolated-test")
try:
    component = canvas.components["Retrieval:Contract"]["obj"]
    assert isinstance(component, component_class("Retrieval"))
    assert component._dataset_ids == ["legacy-dataset-id"]
    assert component._param.memory_ids == ["memory-id"]
    assert component._param.meta_data_filter == {}
    saved = str(canvas)
finally:
    canvas._thread_pool.shutdown(wait=True)
restored = Canvas(saved, tenant_id="isolated-test")
try:
    component = restored.components["Retrieval:Contract"]["obj"]
    assert component._dataset_ids == ["legacy-dataset-id"]
    assert component._param.memory_ids == ["memory-id"]
finally:
    restored._thread_pool.shutdown(wait=True)
assert not blocked_network, blocked_network
print("DYNAMIC_RETRIEVAL_REGISTRY_OK")
"""
    environment = {**os.environ, "LITELLM_LOCAL_MODEL_COST_MAP": "True", "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}
    result = subprocess.run(
        [sys.executable, "-c", script, str(fixture)],
        cwd=Path(__file__).resolve().parents[3],
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-4000:]
    assert "DYNAMIC_RETRIEVAL_REGISTRY_OK" in result.stdout
    assert "Exception ignored in:" not in result.stderr, result.stderr[-4000:]
