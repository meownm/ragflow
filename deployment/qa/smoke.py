"""Exercise one owned dataset through the persistent QA API, then remove it."""

from __future__ import annotations

import base64
import json
import os
import secrets
import time
from urllib.request import Request, urlopen

from Cryptodome.Cipher import PKCS1_v1_5
from Cryptodome.PublicKey import RSA


BASE = "http://127.0.0.1:80"


def api(path: str, token: str | None = None, method: str = "GET", body: dict | None = None, *, data: bytes | None = None, content_type: str = "application/json"):
    headers = {"Content-Type": content_type}
    if token:
        headers["Authorization"] = token
    with urlopen(Request(BASE + path, method=method, headers=headers, data=data if data is not None else json.dumps(body).encode() if body is not None else None), timeout=180) as response:
        result = json.load(response)
        if result.get("code") != 0:
            raise RuntimeError(f"{method} {path} returned code {result.get('code')}")
        return result.get("data"), response.headers.get("Authorization")


password = base64.b64encode(os.environ["DEFAULT_SUPERUSER_PASSWORD"].encode())
public_key = RSA.import_key(open("/ragflow/conf/public.pem", encoding="utf-8").read(), "Welcome")
encrypted = base64.b64encode(PKCS1_v1_5.new(public_key).encrypt(password)).decode()
user, token = api("/api/v1/auth/login", method="POST", body={"email": os.environ["DEFAULT_SUPERUSER_EMAIL"], "password": encrypted})
if not token or not user.get("is_superuser"):
    raise RuntimeError("QA superuser login failed")

dataset_id = None
result = {"status": "incomplete", "dataset_deleted": False, "retrieval_hit": False}
try:
    dataset, _ = api("/api/v1/datasets", token, "POST", {"name": "qa-smoke-" + secrets.token_hex(5), "embedding_model": "bge-m3:latest@QA@Ollama", "chunk_method": "naive"})
    dataset_id = dataset["id"]
    boundary = "qa-smoke-" + secrets.token_hex(8)
    content = "Надёжная проверка стенда: контрольный срок 37 минут."
    upload = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="qa-smoke.txt"\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n{content}\r\n--{boundary}--\r\n').encode()
    documents, _ = api(f"/api/v1/datasets/{dataset_id}/documents", token, "POST", data=upload, content_type=f"multipart/form-data; boundary={boundary}")
    if len(documents) != 1:
        raise RuntimeError("QA smoke upload did not create one document")
    document_id = documents[0]["id"]
    api(f"/api/v1/datasets/{dataset_id}/documents/parse", token, "POST", {"document_ids": [document_id]})
    deadline = time.monotonic() + 240
    while True:
        listing, _ = api(f"/api/v1/datasets/{dataset_id}/documents?page=1&page_size=10", token)
        rows = listing["docs"]
        if listing["total"] != 1 or len(rows) != 1 or rows[0]["id"] != document_id:
            raise RuntimeError("QA dataset contains unexpected documents")
        if rows[0]["run"] == "DONE" and rows[0]["chunk_count"] > 0:
            break
        if rows[0]["run"] == "FAIL" or time.monotonic() > deadline:
            raise RuntimeError("QA smoke document indexing failed")
        time.sleep(2)
    found, _ = api("/api/v1/retrieval", token, "POST", {"dataset_ids": [dataset_id], "question": "Какой контрольный срок проверки стенда?", "similarity_threshold": 0.0, "top_k": 10})
    result["retrieval_hit"] = any(chunk.get("document_id", chunk.get("doc_id")) == document_id for chunk in found.get("chunks", []))
    if not result["retrieval_hit"]:
        raise RuntimeError("QA retrieval did not return the indexed test document")
    result["status"] = "pass"
finally:
    if dataset_id:
        api("/api/v1/datasets", token, "DELETE", {"ids": [dataset_id]})
        result["dataset_deleted"] = True
    print(json.dumps(result))
