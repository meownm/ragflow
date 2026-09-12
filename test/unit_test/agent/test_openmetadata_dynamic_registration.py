"""Saved Canvas contract using the actual registry in an isolated interpreter."""

import json
import os
import subprocess
import sys
from pathlib import Path

from common.openmetadata_agents import OPENMETADATA_AGENT_ROLES, build_openmetadata_agent_dsl


def test_saved_openmetadata_canvas_loads_through_dynamic_registry(tmp_path):
    fixtures = tmp_path / "canvases.json"
    fixtures.write_text(json.dumps([build_openmetadata_agent_dsl(role) for role in OPENMETADATA_AGENT_ROLES]), encoding="utf-8")
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


class StubRedis:
    def delete(self, *_args, **_kwargs):
        return None


def passthrough_timeout(*_args, **_kwargs):
    return lambda function: function


# The contract exercises the real component registry, Canvas, and persisted DSL.
# Keep unrelated database, network, cache, and model bootstraps outside this
# isolated child process so their optional deployment dependencies are not part
# of the registry contract.
install_infrastructure_stub("common.connection_utils", timeout=passthrough_timeout)
install_infrastructure_stub("common.settings")
install_infrastructure_package("api.db.services")
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
install_infrastructure_stub("api.db.services.file_service", FileService=StubService)
install_infrastructure_stub("api.db.services.llm_service", LLMBundle=StubService)
install_infrastructure_stub("api.db.services.task_service", has_canceled=lambda *_args, **_kwargs: False)
install_infrastructure_stub(
    "api.db.joint_services.tenant_model_service",
    get_tenant_default_model_by_type=lambda *_args, **_kwargs: None,
)
install_infrastructure_stub(
    "api.db.joint_services.memory_message_service",
    queue_save_to_memory_task=lambda *_args, **_kwargs: None,
)
install_infrastructure_stub("api.utils.file_utils", is_video_filename=lambda *_args, **_kwargs: False)
install_infrastructure_stub("rag.prompts.generator", chunks_format=lambda *_args, **_kwargs: [])
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

from agent.component import component_class
from agent.canvas import Canvas

assert component_class("OpenMetadata").__module__ == "agent.component.openmetadata"
assert component_class("OpenMetadataParam").__module__ == "agent.component.openmetadata"
with open(sys.argv[1], encoding="utf-8") as source:
    documents = json.load(source)
for dsl in documents:
    role = dsl["meta"]["role_id"]
    component_id = f"OpenMetadata:{role.replace('_', '')}"
    canvas = Canvas(json.dumps(dsl), tenant_id="isolated-test")
    try:
        component = canvas.components[component_id]["obj"]
        assert isinstance(component, component_class("OpenMetadata"))
        assert component._param.role == role
        assert set(component._param.outputs) >= {"content", "result", "entity_ids"}
        saved = str(canvas)
    finally:
        canvas._thread_pool.shutdown(wait=True)
    restored = Canvas(saved, tenant_id="isolated-test")
    try:
        assert restored.components[component_id]["obj"]._param.role == role
        assert restored.components[component_id]["downstream"] == [f"Message:{role}"]
    finally:
        restored._thread_pool.shutdown(wait=True)
    invalid = json.loads(saved)
    invalid["components"][component_id]["obj"]["params"]["role"] = "unknown-persisted-role"
    try:
        Canvas(json.dumps(invalid), tenant_id="isolated-test")
    except ValueError as error:
        assert "Role" in str(error)
    else:
        raise AssertionError("Invalid persisted role must fail parameter validation")
assert not blocked_network, blocked_network
print(f"DYNAMIC_REGISTRY_OK:{len(documents)}")
"""
    environment = {**os.environ, "LITELLM_LOCAL_MODEL_COST_MAP": "True", "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}
    result = subprocess.run(
        [sys.executable, "-c", script, str(fixtures)],
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
    assert f"DYNAMIC_REGISTRY_OK:{len(OPENMETADATA_AGENT_ROLES)}" in result.stdout
    assert "Exception ignored in:" not in result.stderr, result.stderr[-4000:]
