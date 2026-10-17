"""Credentials for the persistent local test account, kept outside Git."""

from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values

LOCAL_CREDENTIALS_FILE = Path(__file__).resolve().parents[1] / ".env.test.local"


def local_login_credentials(base_url: str) -> tuple[str, str] | None:
    if not LOCAL_CREDENTIALS_FILE.is_file():
        return None
    values = dotenv_values(LOCAL_CREDENTIALS_FILE, interpolate=False)
    target = urlsplit(base_url)
    configured = urlsplit(values.get("RAGFLOW_TEST_BASE_URL") or "")
    # Localhost aliases reach the same service, but other hosts/ports must not
    # receive the persistent account's password.
    if (
        target.hostname not in {"127.0.0.1", "localhost", "::1"}
        or configured.hostname not in {"127.0.0.1", "localhost", "::1"}
        or (target.scheme, target.port, target.path.rstrip("/")) != (configured.scheme, configured.port, configured.path.rstrip("/"))
    ):
        return None
    email = values.get("SEEDED_USER_EMAIL")
    password = values.get("SEEDED_USER_PASSWORD")
    if not email or not password:
        raise ValueError("Local test credentials require SEEDED_USER_EMAIL and SEEDED_USER_PASSWORD")
    return email, password
