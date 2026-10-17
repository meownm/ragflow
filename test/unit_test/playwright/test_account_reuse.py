from unittest.mock import MagicMock

import pytest

from test.playwright import conftest as fixtures


@pytest.fixture(autouse=True)
def account_environment(monkeypatch):
    for name in (
        "SEEDED_USER_EMAIL",
        "SEEDED_USER_PASSWORD",
        "E2E_ADMIN_EMAIL",
        "E2E_ADMIN_PASSWORD",
        "REG_EMAIL_BASE",
        "RAGFLOW_TEST_DISPOSABLE",
    ):
        monkeypatch.delenv(name, raising=False)

    def unexpected_request(*args, **kwargs):
        pytest.fail("Resolving existing credentials must not provision or log in a user")

    monkeypatch.setattr(fixtures, "urlopen", unexpected_request)
    monkeypatch.setattr(fixtures, "local_login_credentials", lambda _url: None)


def test_repeated_runs_reuse_canonical_qa_account(monkeypatch):
    monkeypatch.setenv("SEEDED_USER_PASSWORD", "existing-password")
    for _ in range(2):
        monkeypatch.delenv("SEEDED_USER_EMAIL", raising=False)
        assert fixtures.seeded_user_credentials.__wrapped__("http://127.0.0.1:9380") == ("qa@infiniflow.org", "existing-password")


def test_explicit_existing_credentials_take_precedence(monkeypatch):
    monkeypatch.setenv("SEEDED_USER_EMAIL", "tester@example.org")
    monkeypatch.setenv("SEEDED_USER_PASSWORD", "test-password")
    monkeypatch.setenv("E2E_ADMIN_EMAIL", "admin@example.org")
    monkeypatch.setenv("E2E_ADMIN_PASSWORD", "admin-password")
    assert fixtures.seeded_user_credentials.__wrapped__("http://127.0.0.1:9380") == ("tester@example.org", "test-password")


def test_provisioned_admin_credentials_are_reused(monkeypatch):
    monkeypatch.setenv("E2E_ADMIN_EMAIL", "admin@example.org")
    monkeypatch.setenv("E2E_ADMIN_PASSWORD", "admin-password")
    assert fixtures.seeded_user_credentials.__wrapped__("http://127.0.0.1:9380") == ("admin@example.org", "admin-password")


def test_configured_base_account_has_no_generated_suffix(monkeypatch):
    monkeypatch.setenv("REG_EMAIL_BASE", "existing@example.org")
    monkeypatch.setenv("SEEDED_USER_PASSWORD", "existing-password")
    assert fixtures.seeded_user_credentials.__wrapped__("http://127.0.0.1:9380") == ("existing@example.org", "existing-password")


def test_local_credentials_are_used_without_environment_password(monkeypatch):
    monkeypatch.setattr(fixtures, "local_login_credentials", lambda _url: ("qa@infiniflow.org", "local-password"))
    assert fixtures.seeded_user_credentials.__wrapped__("http://127.0.0.1:9380") == ("qa@infiniflow.org", "local-password")


def test_environment_credentials_override_local_file(monkeypatch):
    monkeypatch.setattr(fixtures, "local_login_credentials", lambda _url: ("qa@infiniflow.org", "local-password"))
    monkeypatch.setenv("SEEDED_USER_PASSWORD", "environment-password")
    assert fixtures.seeded_user_credentials.__wrapped__("http://127.0.0.1:9380") == ("qa@infiniflow.org", "environment-password")


@pytest.mark.parametrize("email", [None, "existing@example.org"])
def test_missing_password_fails_instead_of_creating_an_account(monkeypatch, email):
    if email:
        monkeypatch.setenv("SEEDED_USER_EMAIL", email)
    with pytest.raises(RuntimeError, match="Set SEEDED_USER_PASSWORD"):
        fixtures.seeded_user_credentials.__wrapped__("http://127.0.0.1:9380")


def test_explicit_email_without_password_is_not_mixed_with_admin(monkeypatch):
    monkeypatch.setenv("SEEDED_USER_EMAIL", "existing@example.org")
    monkeypatch.setenv("E2E_ADMIN_EMAIL", "admin@example.org")
    monkeypatch.setenv("E2E_ADMIN_PASSWORD", "admin-password")
    with pytest.raises(RuntimeError, match="Set SEEDED_USER_PASSWORD"):
        fixtures.seeded_user_credentials.__wrapped__("http://127.0.0.1:9380")


def test_qa_password_is_not_mixed_with_admin_email(monkeypatch):
    monkeypatch.setenv("SEEDED_USER_PASSWORD", "existing-password")
    monkeypatch.setenv("E2E_ADMIN_EMAIL", "admin@example.org")
    monkeypatch.setenv("E2E_ADMIN_PASSWORD", "admin-password")
    assert fixtures.seeded_user_credentials.__wrapped__("http://127.0.0.1:9380") == ("qa@infiniflow.org", "existing-password")


def test_login_journey_uses_fixture_account_even_with_old_demo_flag(monkeypatch):
    from test.playwright.auth.test_login_success_optional import step_01_open_login

    monkeypatch.setenv("DEMO_CREDS", "1")
    page, state = MagicMock(), {}
    step_01_open_login(
        page,
        state,
        "http://qa/login",
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        ("qa@infiniflow.org", "existing-password"),
    )
    assert state["seeded_email"] == "qa@infiniflow.org"
    assert state["seeded_password"] == "existing-password"
    page.goto.assert_called_once_with("http://qa/login", wait_until="domcontentloaded")


@pytest.mark.parametrize("name", ["E2E_ADMIN_EMAIL", "E2E_ADMIN_PASSWORD"])
def test_partial_admin_credentials_fail(monkeypatch, name):
    monkeypatch.setenv(name, "partial")
    with pytest.raises(RuntimeError, match="Set both E2E_ADMIN"):
        fixtures.seeded_user_credentials.__wrapped__("http://127.0.0.1:9380")


@pytest.mark.parametrize("unique", [False, True])
def test_registration_is_blocked_on_persistent_stack(unique):
    with pytest.raises(RuntimeError, match="disposable application stack"):
        fixtures.reg_email_generator.__wrapped__("qa@example.org", unique)


def test_registration_still_uses_distinct_addresses_in_disposable_stack(monkeypatch):
    monkeypatch.setenv("RAGFLOW_TEST_DISPOSABLE", "1")
    generate = fixtures.reg_email_generator.__wrapped__("qa@example.org", True)
    first, second = generate(), generate()
    assert first != second
    assert first.startswith("qa_") and first.endswith("@example.org")
    assert second.startswith("qa_") and second.endswith("@example.org")
