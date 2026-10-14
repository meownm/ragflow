"""Restored configuration scenarios must check SDK transport and persisted readback."""

from copy import deepcopy
import importlib
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from ragflow_sdk import RAGFlow
from ragflow_sdk.modules.chat import Chat


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "test/testcases"))
try:
    create = importlib.import_module("test.testcases.test_sdk_api.test_chat_assistant_management.test_create_chat_assistant")
    update = importlib.import_module("test.testcases.test_sdk_api.test_chat_assistant_management.test_update_chat_assistant")
finally:
    sys.path.pop(0)


@pytest.mark.parametrize("operation", ["create", "update"])
@pytest.mark.parametrize(
    "field,value",
    [
        ("llm_setting", {"temperature": "provider-extension"}),
        ("llm_setting", {"max_token": -1}),
        ("prompt_config", {"system": 123}),
        ("prompt_config", {"unknown": "provider-extension"}),
    ],
)
@pytest.mark.parametrize("drop_on_read", [False, True])
def test_configuration_scenario_detects_persisted_value_loss(operation, field, value, drop_on_read, monkeypatch):
    client = RAGFlow(api_key="disposable", base_url="http://no-network.invalid")
    stored = {"id": "chat", "name": "existing", "llm_setting": {}, "prompt_config": {}}
    requests = []

    def response(data):
        return SimpleNamespace(json=lambda: {"code": 0, "data": deepcopy(data)})

    def write(path, payload):
        requests.append((path, deepcopy(payload)))
        stored.update(deepcopy(payload))
        return response(stored)

    def read(path, *_args, **_kwargs):
        assert path == "/chats/chat"
        data = deepcopy(stored)
        if drop_on_read:
            data[field] = {}
        return response(data)

    monkeypatch.setattr(client, "post", write)
    monkeypatch.setattr(client, "patch", write)
    monkeypatch.setattr(client, "get", read)
    dataset = SimpleNamespace(id="dataset")
    if operation == "create":
        scenario = getattr(create.TestChatAssistantCreate(), "test_" + field)
        arguments = (client, (dataset, None, None), value, "")
    else:
        scenario = getattr(update.TestChatAssistantUpdate(), "test_" + field)
        arguments = (client, (dataset, None, [Chat(client, deepcopy(stored))]), value, "")
    if drop_on_read:
        with pytest.raises((AttributeError, AssertionError)):
            scenario(*arguments)
    else:
        scenario(*arguments)
    assert len(requests) == 1
    assert requests[0][1][field] == value
