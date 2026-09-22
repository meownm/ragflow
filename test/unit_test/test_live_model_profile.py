"""Isolated pytest harness contract; no live server or provider calls."""

import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
TESTCASES = ROOT / "test" / "testcases"


@pytest.fixture
def harness(tmp_path):
    for name in ("configs.py", "conftest.py"):
        shutil.copy2(TESTCASES / name, tmp_path / name)
    with (tmp_path / "conftest.py").open("a", encoding="utf-8") as stream:
        stream.write("""

def _forbidden_network(*args, **kwargs):
    raise AssertionError("NETWORK_ATTEMPTED")

requests.sessions.Session.request = _forbidden_network
""")
    (tmp_path / "pytest.ini").write_text("[pytest]\nmarkers =\n    p1: priority\n    p2: priority\n    p3: priority\n", encoding="utf-8")

    def run(source, *args, credentials=None, name="test_probe.py"):
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8")
        env = os.environ.copy()
        for key in ("ZHIPU_AI_API_KEY", "SILICONFLOW_API_KEY", "PYTEST_ADDOPTS"):
            env.pop(key, None)
        env.update(credentials or {})
        env["PYTHONPATH"] = str(tmp_path)
        env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "--strict-markers", "--tb=short", "-c", str(tmp_path / "pytest.ini"), str(target), *args],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=45,
        )
        assert "NETWORK_ATTEMPTED" not in result.stdout + result.stderr
        return result

    return run


def test_collection_without_cloud_credentials(harness):
    result = harness("import pytest\nfrom configs import ZHIPU_AI_API_KEY\n@pytest.mark.p1\ndef test_contract(): pass\n", "--collect-only")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 test collected" in result.stdout


@pytest.mark.parametrize("credentials", [{}, {"ZHIPU_AI_API_KEY": " "}, {"ZHIPU_AI_API_KEY": "synthetic-not-a-key"}])
def test_opt_in_cloud_fails_before_auth_network(harness, credentials):
    result = harness("import pytest\n@pytest.mark.p1\ndef test_contract(auth): pass\n", "--model-profile=cloud", credentials=credentials)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Missing cloud model prerequisites" in result.stdout
    assert "SILICONFLOW_API_KEY" in result.stdout
    assert "1 error" in result.stdout
    assert "skipped" not in result.stdout


def test_local_unknown_test_runs_without_cloud_setup(harness):
    result = harness("import pytest\n@pytest.mark.p1\ndef test_contract(): pass\n", "--model-profile=local")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout


def test_default_profile_is_local(harness):
    result = harness("import pytest\n@pytest.mark.p1\ndef test_contract(): pass\n")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout


@pytest.mark.parametrize("requirement", ["@pytest.mark.cloud_models\n", ""])
def test_local_skips_explicit_cloud_requirement(harness, requirement):
    fixture = "" if requirement else "cloud_model_credentials"
    source = f"import pytest\n@pytest.mark.p1\n@pytest.mark.local_api\n{requirement}def test_contract({fixture}): pass\n"
    result = harness(source, "--model-profile=local")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 skipped" in result.stdout


def test_local_same_named_fixture_is_not_treated_as_cloud(harness):
    result = harness(
        "import pytest\n@pytest.fixture\ndef set_tenant_info(): return 'local-double'\n@pytest.mark.p1\ndef test_contract(set_tenant_info): assert set_tenant_info == 'local-double'\n",
        "--model-profile=local",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout


def test_local_auth_initializes_without_provider_setup(harness):
    result = harness(
        """
import pytest
import requests

@pytest.fixture(scope="session", autouse=True)
def fake_transport():
    calls = []
    original = requests.post
    def post(url, **kwargs):
        calls.append(url.rsplit("/", 1)[-1])
        assert url.endswith(("/users", "/auth/login"))
        class Response:
            headers = {"Authorization": "synthetic-session"}
            def json(self): return {"code": 0}
        return Response()
    requests.post = post
    yield calls
    requests.post = original

@pytest.mark.p1
@pytest.mark.local_api
def test_auth(auth, fake_transport):
    assert auth == "synthetic-session"
    assert fake_transport == ["users", "login"]
""",
        "--model-profile=local",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout


def test_local_sdk_token_is_issued_by_admin_api(harness):
    result = harness(
        """
import pytest
import requests

@pytest.fixture(scope="session", autouse=True)
def fake_transport():
    calls = []
    original_post = requests.post
    original_session = requests.Session

    class Response:
        def __init__(self, data, authorization=None):
            self._data = data
            self.headers = {"Authorization": authorization} if authorization else {}
        def json(self): return self._data

    def post(url, **kwargs):
        calls.append(url)
        if url.endswith("/users"):
            return Response({"code": 0})
        return Response({"code": 0}, "synthetic-session")

    class Session:
        headers = {}
        def __enter__(self): return self
        def __exit__(self, *args): return None
        def post(self, url, **kwargs):
            calls.append(url)
            if url.endswith("/admin/login"):
                return Response({"code": 0}, "synthetic-admin-session")
            return Response({"code": 0, "data": {"token": "ragflow-synthetic"}})

    requests.post = post
    requests.Session = Session
    yield calls
    requests.post = original_post
    requests.Session = original_session

@pytest.mark.p1
def test_token(token, fake_transport):
    assert token == "ragflow-synthetic"
    assert any(url.endswith("/admin/login") for url in fake_transport)
    assert any(url.endswith("/users/qa@infiniflow.org/new_token") for url in fake_transport)
""",
        "--model-profile=local",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout


def test_cloud_setup_runs_once_with_explicit_credentials(harness):
    result = harness(
        """
import pytest

calls = []
@pytest.fixture(scope="session")
def set_tenant_info(cloud_model_credentials):
    assert set(cloud_model_credentials) == {"ZHIPU_AI_API_KEY", "SILICONFLOW_API_KEY"}
    calls.append("configured")

@pytest.mark.p1
def test_first(): assert calls == ["configured"]
@pytest.mark.p1
def test_second(): assert calls == ["configured"]
""",
        "--model-profile=cloud",
        credentials={"ZHIPU_AI_API_KEY": "synthetic-not-a-key", "SILICONFLOW_API_KEY": "synthetic-not-a-key"},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "2 passed" in result.stdout
