"""Real HTTP/SDK interoperability with a disposable reranker, stub inference.

Set RERANKER_SERVICE_TEST_ROOT and RERANKER_SERVICE_TEST_PYTHON explicitly.
Only the temporary loopback server is started; no installed service is changed.
"""

import importlib.util
import os
from pathlib import Path
import socket
import subprocess
import time

import numpy as np
import pytest
import requests


SERVER = """
import os, pathlib, runpy, uvicorn
root = pathlib.Path(os.environ['RERANKER_SERVICE_TEST_ROOT'])
runpy.run_path(str(root / 'tests/conftest.py'))
from reranker_service.src import main
main.CrossEncoder = runpy.run_path(str(root / 'tests/test_api.py'))['FakeCrossEncoder']
uvicorn.run(main.app, host='127.0.0.1', port=int(os.environ['RERANKER_CONTRACT_PORT']), log_level='error')
"""


@pytest.fixture(scope="module")
def disposable_reranker(tmp_path_factory):
    root = os.environ.get("RERANKER_SERVICE_TEST_ROOT")
    python = os.environ.get("RERANKER_SERVICE_TEST_PYTHON")
    if not root or not python:
        pytest.skip("Requires an explicit reranker source directory and test Python")
    root = Path(root).resolve()
    assert (root / "src/main.py").is_file()
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    env = {
        **os.environ,
        "RERANKER_SERVICE_TEST_ROOT": str(root),
        "RERANKER_CONTRACT_PORT": str(port),
        "RERANKER_MODEL_NAME": "model-r",
        "RERANKER_DEVICE": "cpu",
        "RERANKER_MODEL_PATH": "",
        "RERANKER_MODEL_ARTIFACT_SHA256": "",
        "RERANKER_MAX_LENGTH": "512",
        "HF_HUB_OFFLINE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    log_path = tmp_path_factory.mktemp("reranker-contract") / "server.log"
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen([python, "-c", SERVER], cwd=root.parent, env=env, stdout=log, stderr=log)
        endpoint = f"http://127.0.0.1:{port}"
        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    pytest.fail(log_path.read_text(encoding="utf-8"))
                try:
                    if requests.get(endpoint + "/health", timeout=0.5).status_code == 200:
                        break
                except requests.RequestException:
                    pass
                time.sleep(0.1)
            else:
                pytest.fail("Disposable reranker did not become healthy: " + log_path.read_text(encoding="utf-8"))
            yield endpoint
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


@pytest.fixture(scope="module")
def adapters():
    path = Path(__file__).resolve().parents[2] / "rag/llm/rerank_model.py"
    spec = importlib.util.spec_from_file_location("wire_rerank_adapters", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "adapter_name,suffix",
    [
        ("JinaRerank", "/v1/rerank"),
        ("OpenAI_APIRerank", "/v1"),
        ("NewAPIRerank", "/v1"),
        ("CoHereRerank", "/v1"),
        ("CoHereRerank", "/v2/rerank"),
    ],
)
def test_actual_adapter_and_sdk_over_http(disposable_reranker, adapters, adapter_name, suffix):
    model = getattr(adapters, adapter_name)("test-key", "model-r", disposable_reranker + suffix)
    try:
        rank, tokens = model.similarity("question", ["first", "second", "third"])
        assert np.allclose(rank, [0.2689414213699951, 0.5, 0.7310585786300049])
        assert tokens > 0
    finally:
        if adapter_name == "CoHereRerank":
            model.client._client_wrapper.httpx_client.httpx_client.close()


@pytest.mark.parametrize("adapter_name", ["JinaRerank", "OpenAI_APIRerank", "NewAPIRerank", "CoHereRerank"])
def test_unknown_model_fails_in_actual_clients(disposable_reranker, adapters, adapter_name):
    model = getattr(adapters, adapter_name)("test-key", "missing-model", disposable_reranker + "/v1")
    try:
        with pytest.raises(Exception):
            model.similarity("question", ["first"])
    finally:
        if adapter_name == "CoHereRerank":
            model.client._client_wrapper.httpx_client.httpx_client.close()


def test_cohere_sdk_decodes_returned_document_objects(disposable_reranker, adapters):
    model = adapters.CoHereRerank("test-key", "model-r", disposable_reranker + "/v1")
    try:
        response = model.client.rerank(model="model-r", query="question", documents=[{"text": "first"}, {"text": "second"}], top_n=1, return_documents=True)
        assert response.results[0].index == 1
        assert response.results[0].document.text == "second"
    finally:
        model.client._client_wrapper.httpx_client.httpx_client.close()
