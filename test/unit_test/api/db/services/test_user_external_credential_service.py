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

import pytest
from peewee import SqliteDatabase
from playhouse.migrate import SqliteMigrator

from api.db.db_models import UserExternalCredential
from api.db.db_models import _migrate_user_external_credential_expiry
from api.db.services.user_external_credential_service import (
    ExternalCredentialError,
    ExternalCredentialDecryptionError,
    ExternalCredentialMissingError,
    UserExternalCredentialService,
)


@pytest.fixture()
def credential_database(monkeypatch):
    monkeypatch.setenv("RAGFLOW_CREDENTIALS_KEY", "test-only-credential-key")
    database = SqliteDatabase(":memory:")
    with database.bind_ctx([UserExternalCredential], bind_refs=False, bind_backrefs=False):
        database.connect()
        database.create_tables([UserExternalCredential])
        yield database
        database.drop_tables([UserExternalCredential])
        database.close()


def test_eva_token_is_encrypted_scoped_and_rotated(credential_database):
    first = UserExternalCredentialService.put_eva_wiki_token(
        "user-1",
        "HTTPS://EVA.EXAMPLE.COM:443/api/",
        "personal-token-v1",
    )
    row = UserExternalCredential.get()

    assert first == {
        "configured": True,
        "scope": "https://eva.example.com/api",
        "credential_version": 1,
        "expires_at": None,
        "expiry_checked_at": None,
    }
    assert "personal-token-v1" not in row.encrypted_secret
    assert row.create_time is not None
    assert row.update_time is not None
    assert UserExternalCredentialService.get_eva_wiki_token("user-1", "https://eva.example.com/api").secret == "personal-token-v1"

    second = UserExternalCredentialService.put_eva_wiki_token("user-1", "https://eva.example.com/api", "personal-token-v2")
    assert second["credential_version"] == 2
    assert UserExternalCredential.select().count() == 1
    assert UserExternalCredentialService.get_eva_wiki_token("user-1", "https://eva.example.com/api/").secret == "personal-token-v2"


def test_eva_token_cannot_be_decrypted_for_another_user(credential_database):
    UserExternalCredentialService.put_eva_wiki_token("user-1", "https://eva.example.com", "personal-token")
    row = UserExternalCredential.get()

    with pytest.raises(ExternalCredentialDecryptionError):
        UserExternalCredentialService._decrypt("user-2", row.provider, row.scope, row.encrypted_secret)


def test_deleting_eva_token_fails_closed(credential_database):
    UserExternalCredentialService.put_eva_wiki_token("user-1", "https://eva.example.com", "personal-token")

    assert UserExternalCredentialService.delete_eva_wiki_token("user-1", "https://eva.example.com") is True
    with pytest.raises(ExternalCredentialMissingError):
        UserExternalCredentialService.get_eva_wiki_token("user-1", "https://eva.example.com")


@pytest.mark.parametrize(
    "value",
    [
        "https://user:password@eva.example.com/api",
        "https://eva.example.com/api?tenant=one",
        "https://eva.example.com/api#token",
        "https://eva.example.com:0/api",
        "https://eva.example.com:70000/api",
    ],
)
def test_eva_scope_rejects_ambiguous_or_sensitive_urls(value):
    with pytest.raises(ExternalCredentialError, match="EVA API URL is invalid"):
        UserExternalCredentialService.normalize_http_scope(value)


def test_eva_scope_supports_ipv6_and_enforces_database_limit():
    assert UserExternalCredentialService.normalize_http_scope("https://[::1]:8443/api/") == "https://[::1]:8443/api"

    with pytest.raises(ExternalCredentialError, match="EVA API URL is too long"):
        UserExternalCredentialService.normalize_http_scope("https://eva.example.com/" + "a" * 600)


@pytest.mark.parametrize("other_user,other_scope", [("user-2", "https://eva.example.com"), ("user-1", "https://other-eva.example.com")])
def test_eva_public_api_does_not_read_or_delete_other_credential(credential_database, other_user, other_scope):
    UserExternalCredentialService.put_eva_wiki_token("user-1", "https://eva.example.com", "synthetic-token")
    with pytest.raises(ExternalCredentialMissingError):
        UserExternalCredentialService.get_eva_wiki_token(other_user, other_scope)
    assert UserExternalCredentialService.delete_eva_wiki_token(other_user, other_scope) is False
    assert UserExternalCredentialService.get_eva_wiki_token("user-1", "https://eva.example.com").secret == "synthetic-token"


def test_eva_statuses_exclude_other_users_and_repeated_delete_is_safe(credential_database):
    UserExternalCredentialService.put_eva_wiki_token("user-1", "https://eva.example.com", "synthetic-token-one")
    UserExternalCredentialService.put_eva_wiki_token("user-2", "https://other-eva.example.com", "synthetic-token-two")
    statuses = UserExternalCredentialService.list_eva_wiki_statuses("user-1")
    assert set(statuses) == {"https://eva.example.com"}
    assert set(statuses["https://eva.example.com"]) == {"configured", "scope", "credential_version", "update_time", "expires_at", "expiry_checked_at"}
    assert UserExternalCredentialService.delete_eva_wiki_token("user-1", "https://eva.example.com") is True
    assert UserExternalCredentialService.delete_eva_wiki_token("user-1", "https://eva.example.com") is False
    assert UserExternalCredentialService.list_eva_wiki_statuses("user-1") == {}
    assert UserExternalCredentialService.get_eva_wiki_token("user-2", "https://other-eva.example.com").secret == "synthetic-token-two"


def test_expiration_is_persisted_and_rotation_clears_old_metadata(credential_database):
    service = UserExternalCredentialService
    service.put_eva_wiki_token("user-1", "https://eva.example.com", "first", expires_at=1_800_000_000_000, expiry_checked_at=123)
    value = service.get_eva_wiki_token("user-1", "https://eva.example.com")
    assert value.expires_at == 1_800_000_000_000
    assert value.expiry_checked_at == 123
    status = service.list_eva_wiki_statuses("user-1")[value.scope]
    assert status["expires_at"] == value.expires_at
    service.put_eva_wiki_token("user-1", value.scope, "second")
    assert service.get_eva_wiki_token("user-1", value.scope).expiry_checked_at is None
    assert not service.update_eva_wiki_expiry("user-1", value.scope, value.credential_id, value.credential_version, expires_at=value.expires_at, expiry_checked_at=124)
    assert service.get_eva_wiki_token("user-1", value.scope).expires_at is None


def test_expiry_update_is_scoped_and_cannot_resurrect_deleted_token(credential_database):
    service = UserExternalCredentialService
    service.put_eva_wiki_token("user-1", "https://eva.example.com", "first")
    value = service.get_eva_wiki_token("user-1", "https://eva.example.com")
    args = (value.scope, value.credential_id, value.credential_version)
    assert not service.update_eva_wiki_expiry("user-2", *args, expires_at=456, expiry_checked_at=123)
    assert service.update_eva_wiki_expiry("user-1", *args, expires_at=None, expiry_checked_at=123)
    assert service.get_eva_wiki_token("user-1", value.scope).expiry_checked_at == 123
    service.delete_eva_wiki_token("user-1", value.scope)
    service.put_eva_wiki_token("user-1", value.scope, "replacement")
    assert not service.update_eva_wiki_expiry("user-1", *args, expires_at=456, expiry_checked_at=123)
    assert service.get_eva_wiki_token("user-1", value.scope).expiry_checked_at is None


def test_expiry_migration_preserves_existing_secret_and_is_repeatable(credential_database):
    service = UserExternalCredentialService
    service.put_eva_wiki_token("user-1", "https://eva.example.com", "existing-token")
    encrypted = UserExternalCredential.get().encrypted_secret
    for column in ("expires_at", "expiry_checked_at"):
        credential_database.execute_sql(f'ALTER TABLE user_external_credential DROP COLUMN "{column}"')
    for _ in range(2):
        _migrate_user_external_credential_expiry(SqliteMigrator(credential_database))
        value = service.get_eva_wiki_token("user-1", "https://eva.example.com")
        assert value.secret == "existing-token"
        assert value.expires_at is None and value.expiry_checked_at is None
        assert UserExternalCredential.get().encrypted_secret == encrypted
