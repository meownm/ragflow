"""Register the project's Ollama proxy models for the private QA tenant."""

import base64
import json
import os
from urllib.request import Request, urlopen

from Cryptodome.Cipher import PKCS1_v1_5
from Cryptodome.PublicKey import RSA


BASE = "http://127.0.0.1:80"
MODELS = (("t-tech/T-lite-it-2.1:q8_0", "chat"), ("qwen3.8:latest", "chat"), ("bge-m3:latest", "embedding"))


def api(path, method="GET", payload=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = token
    data = json.dumps(payload).encode() if payload is not None else None
    with urlopen(Request(BASE + path, method=method, headers=headers, data=data), timeout=240) as response:
        result = json.load(response)
        if result.get("code") != 0:
            raise RuntimeError(f"{method} {path} returned code {result.get('code')}")
        return result.get("data"), response.headers.get("Authorization")


password = base64.b64encode(os.environ["DEFAULT_SUPERUSER_PASSWORD"].encode())
public_key = RSA.import_key(open("/ragflow/conf/public.pem", encoding="utf-8").read(), "Welcome")
encrypted = base64.b64encode(PKCS1_v1_5.new(public_key).encrypt(password)).decode()
user, token = api("/api/v1/auth/login", "POST", {"email": os.environ["DEFAULT_SUPERUSER_EMAIL"], "password": encrypted})
if not token or not user.get("is_superuser"):
    raise RuntimeError("QA superuser authentication failed")
proxy = os.environ["QA_OLLAMA_URL"]
for name, kind in MODELS:
    api("/v1/llm/add_llm", "POST", {"llm_factory": "Ollama", "llm_name": name, "model_type": kind, "api_base": proxy, "max_tokens": 4096}, token)
api("/api/v1/providers", "PUT", {"provider_name": "Ollama"}, token)
instances, _ = api("/api/v1/providers/Ollama/instances", token=token)
if any(item.get("instance_name") == "QA" for item in instances):
    api("/api/v1/providers/Ollama/instances", "DELETE", {"instances": ["QA"]}, token)
api(
    "/api/v1/providers/Ollama/instances",
    "POST",
    {"instance_name": "QA", "api_key": "", "base_url": proxy, "region": "default", "model_info": [{"model_name": name, "model_type": [kind], "max_tokens": 4096} for name, kind in MODELS]},
    token,
)
for name, kind in (MODELS[0], MODELS[-1]):
    api("/api/v1/models/default", "PATCH", {"model_provider": "Ollama", "model_instance": "QA", "model_name": name, "model_type": kind}, token)
tenant, _ = api("/api/v1/users/me/models", token=token)
api(
    "/api/v1/users/me/models",
    "PATCH",
    {"tenant_id": tenant["tenant_id"], "llm_id": f"{MODELS[0][0]}@QA@Ollama", "embd_id": f"{MODELS[-1][0]}@QA@Ollama", "img2txt_id": "", "asr_id": "", "rerank_id": "", "tts_id": ""},
    token,
)
print("Configured dedicated QA tenant models")
