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

import base64
import hashlib
import os
from dataclasses import dataclass
from urllib.parse import urlparse

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from api.db.db_models import UserExternalCredential
from api.db.services.common_service import CommonService
from common import settings
from common.misc_utils import get_uuid
from common.time_utils import current_timestamp, timestamp_to_date


class ExternalCredentialError(RuntimeError):
    pass


class ExternalCredentialMissingError(ExternalCredentialError):
    pass


class ExternalCredentialDecryptionError(ExternalCredentialError):
    pass


@dataclass(frozen=True)
class ExternalCredentialValue:
    secret: str
    credential_version: int
    scope: str


class UserExternalCredentialService(CommonService):
    """Store user-owned external credentials without exposing plaintext."""

    model = UserExternalCredential
    EVA_WIKI_PROVIDER = "eva_wiki"
    _ENVELOPE_VERSION = "v1"
    _MAX_SCOPE_LENGTH = 512

    @staticmethod
    def normalize_http_scope(value: str) -> str:
        try:
            parsed = urlparse(str(value or "").strip())
            hostname = parsed.hostname
            port = parsed.port
        except ValueError as exc:
            raise ExternalCredentialError("EVA API URL is invalid") from exc
        if parsed.scheme.lower() not in {"http", "https"} or not hostname or parsed.username or parsed.password or parsed.params or parsed.query or parsed.fragment or port == 0:
            raise ExternalCredentialError("EVA API URL is invalid")
        host = hostname.lower()
        if ":" in host:
            host = f"[{host}]"
        default_port = 443 if parsed.scheme.lower() == "https" else 80
        netloc = host if port in {None, default_port} else f"{host}:{port}"
        path = parsed.path.rstrip("/")
        scope = f"{parsed.scheme.lower()}://{netloc}{path}"
        if len(scope) > UserExternalCredentialService._MAX_SCOPE_LENGTH:
            raise ExternalCredentialError("EVA API URL is too long")
        return scope

    @classmethod
    def put_eva_wiki_token(cls, user_id: str, api_base_url: str, token: str) -> dict:
        normalized_token = str(token or "").strip()
        if not normalized_token:
            raise ExternalCredentialError("EVA API token is required")
        if len(normalized_token) > 4096:
            raise ExternalCredentialError("EVA API token is too long")
        scope = cls.normalize_http_scope(api_base_url)

        with cls.model._meta.database.atomic():
            timestamp = current_timestamp()
            date = timestamp_to_date(timestamp)
            existing = cls.model.get_or_none(
                cls.model.user_id == user_id,
                cls.model.provider == cls.EVA_WIKI_PROVIDER,
                cls.model.scope == scope,
            )
            version = int(existing.credential_version or 0) + 1 if existing else 1
            encrypted = cls._encrypt(user_id, cls.EVA_WIKI_PROVIDER, scope, normalized_token)
            if existing:
                cls.model.update(
                    encrypted_secret=encrypted,
                    credential_version=version,
                    update_time=timestamp,
                    update_date=date,
                ).where(cls.model.id == existing.id).execute()
            else:
                cls.model.create(
                    id=get_uuid(),
                    user_id=user_id,
                    provider=cls.EVA_WIKI_PROVIDER,
                    scope=scope,
                    encrypted_secret=encrypted,
                    credential_version=version,
                    create_time=timestamp,
                    create_date=date,
                    update_time=timestamp,
                    update_date=date,
                )
        return {"configured": True, "scope": scope, "credential_version": version}

    @classmethod
    def get_eva_wiki_token(cls, user_id: str, api_base_url: str) -> ExternalCredentialValue:
        scope = cls.normalize_http_scope(api_base_url)
        credential = cls.model.get_or_none(
            cls.model.user_id == user_id,
            cls.model.provider == cls.EVA_WIKI_PROVIDER,
            cls.model.scope == scope,
        )
        if credential is None:
            raise ExternalCredentialMissingError("Personal EVA API token is not configured")
        secret = cls._decrypt(user_id, credential.provider, credential.scope, credential.encrypted_secret)
        return ExternalCredentialValue(secret=secret, credential_version=int(credential.credential_version), scope=credential.scope)

    @classmethod
    def delete_eva_wiki_token(cls, user_id: str, api_base_url: str) -> bool:
        scope = cls.normalize_http_scope(api_base_url)
        return (
            cls.model.delete()
            .where(
                cls.model.user_id == user_id,
                cls.model.provider == cls.EVA_WIKI_PROVIDER,
                cls.model.scope == scope,
            )
            .execute()
            > 0
        )

    @classmethod
    def list_eva_wiki_statuses(cls, user_id: str) -> dict[str, dict]:
        rows = cls.model.select().where(
            cls.model.user_id == user_id,
            cls.model.provider == cls.EVA_WIKI_PROVIDER,
        )
        return {
            row.scope: {
                "configured": True,
                "scope": row.scope,
                "credential_version": int(row.credential_version),
                "update_time": row.update_time,
            }
            for row in rows
        }

    @classmethod
    def _encryption_key(cls) -> bytes:
        key_material = os.getenv("RAGFLOW_CREDENTIALS_KEY") or settings.get_secret_key()
        if not key_material:
            raise ExternalCredentialError("Credential encryption key is unavailable")
        return hashlib.sha256(f"ragflow-user-external-credential:{key_material}".encode()).digest()

    @staticmethod
    def _aad(user_id: str, provider: str, scope: str) -> bytes:
        return f"{user_id}\0{provider}\0{scope}".encode()

    @classmethod
    def _encrypt(cls, user_id: str, provider: str, scope: str, secret: str) -> str:
        nonce = os.urandom(12)
        ciphertext = AESGCM(cls._encryption_key()).encrypt(nonce, secret.encode(), cls._aad(user_id, provider, scope))
        payload = base64.urlsafe_b64encode(nonce + ciphertext).decode()
        return f"{cls._ENVELOPE_VERSION}:{payload}"

    @classmethod
    def _decrypt(cls, user_id: str, provider: str, scope: str, envelope: str) -> str:
        try:
            version, payload = str(envelope).split(":", 1)
            if version != cls._ENVELOPE_VERSION:
                raise ValueError("unsupported credential envelope")
            decoded = base64.urlsafe_b64decode(payload.encode())
            nonce, ciphertext = decoded[:12], decoded[12:]
            return AESGCM(cls._encryption_key()).decrypt(nonce, ciphertext, cls._aad(user_id, provider, scope)).decode()
        except Exception as exc:
            raise ExternalCredentialDecryptionError("Personal EVA API token could not be decrypted") from exc
