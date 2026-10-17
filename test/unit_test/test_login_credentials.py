import base64
import importlib.util
from pathlib import Path

import pytest

from test import login_credentials


@pytest.fixture
def credentials_file(tmp_path, monkeypatch):
    path = tmp_path / ".env.test.local"
    path.write_text(
        "RAGFLOW_TEST_BASE_URL=http://127.0.0.1:9380\nSEEDED_USER_EMAIL=qa@infiniflow.org\nSEEDED_USER_PASSWORD=unit-test-${literal}-password\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(login_credentials, "LOCAL_CREDENTIALS_FILE", path)
    return path


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "[::1]"])
def test_local_account_is_available_only_for_matching_service(credentials_file, host):
    assert login_credentials.local_login_credentials(f"http://{host}:9380/") == (
        "qa@infiniflow.org",
        "unit-test-${literal}-password",
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://remote.example:9380",
        "http://127.0.0.1:19380",
        "https://127.0.0.1:9380",
        "http://127.0.0.1:9380/other-app",
    ],
)
def test_local_password_is_not_used_for_another_target(credentials_file, url):
    assert login_credentials.local_login_credentials(url) is None


def test_missing_local_file_is_allowed(credentials_file):
    credentials_file.unlink()
    assert login_credentials.local_login_credentials("http://127.0.0.1:9380") is None


def test_incomplete_local_credentials_fail(credentials_file):
    credentials_file.write_text("RAGFLOW_TEST_BASE_URL=http://127.0.0.1:9380\n", encoding="utf-8")
    with pytest.raises(ValueError, match="require SEEDED_USER_EMAIL and SEEDED_USER_PASSWORD"):
        login_credentials.local_login_credentials("http://127.0.0.1:9380")


def test_api_config_encrypts_local_password_for_current_login_contract(credentials_file, monkeypatch):
    from api.utils.crypt import decrypt

    for name in ("SEEDED_USER_EMAIL", "SEEDED_USER_PASSWORD", "HOST_ADDRESS"):
        monkeypatch.delenv(name, raising=False)
    config_path = Path(__file__).resolve().parents[1] / "testcases" / "configs.py"
    spec = importlib.util.spec_from_file_location("qa_credentials_config", config_path)
    config = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(config)
    assert config.EMAIL == "qa@infiniflow.org"
    assert decrypt(config.PASSWORD) == base64.b64encode(b"unit-test-${literal}-password").decode()
