"""Measure real Source Workbench retrieval on the disposable live regression stack."""

from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from test.evals.source_workbench.evaluate import GOLDEN, score_suite, validate_gold


BASE = "http://127.0.0.1:19382"
PROJECT_PREFIX = "ragflow-t1-live-20260906-b-"
EMBEDDING_MODEL = "bge-m3:latest@QA@Ollama"


def _guard() -> tuple[str, Path]:
    if os.environ.get("RAGFLOW_BASE_URL", "").rstrip("/") != BASE:
        raise RuntimeError("Source quality requires the dedicated loopback live runner")
    if not os.environ.get("QA_DISPOSABLE_PROJECT", "").startswith(PROJECT_PREFIX):
        raise RuntimeError("Source quality requires an explicitly disposable project")
    token = os.environ.get("QA_LIVE_TOKEN", "")
    evidence = os.environ.get("QA_LIVE_EVIDENCE", "")
    if not token or not evidence:
        raise RuntimeError("Live runner credentials and evidence directory are required")
    identity = Path(evidence) / "identity.json"
    if not identity.is_file():
        raise RuntimeError("Live runner identity.json is required")
    if json.loads(identity.read_text(encoding="utf-8")).get("project") != os.environ["QA_DISPOSABLE_PROJECT"]:
        raise RuntimeError("Live runner identity does not match the disposable project")
    return token, Path(evidence)


def _api(path: str, token: str, method: str = "GET", body: dict | None = None, *, multipart: bytes | None = None, boundary: str = ""):
    if not path.startswith("/api/v1/"):
        raise ValueError("Only local API v1 paths are permitted")
    data = multipart if multipart is not None else json.dumps(body).encode("utf-8") if body is not None else None
    content_type = f"multipart/form-data; boundary={boundary}" if multipart is not None else "application/json"
    request = Request(BASE + path, method=method, data=data, headers={"Authorization": token, "Content-Type": content_type})
    try:
        with urlopen(request, timeout=180) as response:
            payload = json.load(response)
    except HTTPError as error:
        raise RuntimeError(f"{method} {path} returned HTTP {error.code}") from error
    except URLError as error:
        raise RuntimeError(f"{method} {path} was unavailable ({type(error).__name__})") from error
    if not isinstance(payload, dict) or payload.get("code") != 0:
        raise RuntimeError(f"{method} {path} returned a non-success API code")
    return payload.get("data")


def _upload_body(gold: dict, suffix: str) -> tuple[bytes, str]:
    boundary = "source-quality-" + suffix
    parts = []
    for document in gold["documents"].values():
        filename = f"{document['title']}.txt"
        header = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n').encode("utf-8")
        parts.extend((header, document["text"].encode("utf-8"), b"\r\n"))
    parts.append(f"--{boundary}--\r\n".encode("ascii"))
    return b"".join(parts), boundary


def _source_revision(identity: dict) -> tuple[str, bool]:
    source = Path(identity["source"]).resolve()
    manifest_path = source.parent / "candidate.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("source_id") != identity["source_id"]:
            raise RuntimeError("Candidate manifest does not match runner source identity")
        revision = manifest["identity"]["head"]
        dirty = manifest["identity"]["dirty"]
    else:
        revision = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
        dirty = bool(subprocess.check_output(["git", "-C", str(source), "status", "--porcelain=v1", "--untracked-files=all"]))
    if not re.fullmatch(r"[0-9a-f]{40}", revision) or type(dirty) is not bool:
        raise RuntimeError("Source revision or dirty state is invalid")
    if not manifest_path.is_file() and identity["source_id"] != "development:" + revision:
        raise RuntimeError("Development source identity differs from the Git revision")
    return revision, dirty


def test_source_workbench_retrieval_quality_on_disposable_stack():
    token, evidence_dir = _guard()
    gold_bytes = GOLDEN.read_bytes()
    gold = json.loads(gold_bytes)
    validate_gold(gold)
    identity = json.loads((evidence_dir / "identity.json").read_text(encoding="utf-8"))
    revision, dirty = _source_revision(identity)
    suffix = secrets.token_hex(6)
    report = {
        "status": "incomplete",
        "corpus_sha256": sha256(gold_bytes).hexdigest(),
        "source_revision": revision,
        "source_dirty": dirty,
        "model": {"embedding": EMBEDDING_MODEL, "weights": identity.get("ollama_models", {}).get("bge-m3:latest")},
        "index": {"state": "not_created", "backend": "Elasticsearch", "backend_image_id": identity.get("images", {}).get("es01"), "documents": {}},
        "workspace_cleanup": "disposable runner tears down the whole stack; no workspace DELETE API exists",
    }
    if (Path(identity["source"]).resolve().parent / "candidate.json").is_file():
        report["source_id"] = identity["source_id"]
    report_path = evidence_dir / "source-workbench-quality.json"
    dataset_id = None
    failure = None
    try:
        dataset = _api(
            "/api/v1/datasets",
            token,
            "POST",
            {
                "name": f"eval-source-workbench-{suffix}",
                "chunk_method": "naive",
                "parse_type": 1,
                "embedding_model": EMBEDDING_MODEL,
                "parser_config": {"chunk_token_num": 256, "auto_keywords": 0, "auto_questions": 0},
            },
        )
        dataset_id = dataset.get("id") or dataset.get("kb_id")
        if not dataset_id:
            raise RuntimeError("Dataset creation omitted its ID")
        report["dataset_id"] = dataset_id
        dataset_info = _api(f"/api/v1/datasets/{dataset_id}", token)
        report["model"]["dataset_embedding"] = dataset_info.get("embedding_model") or dataset_info.get("embd_id")
        if report["model"]["dataset_embedding"] != EMBEDDING_MODEL:
            raise RuntimeError("Dataset embedding model differs from live runner model")
        upload, boundary = _upload_body(gold, suffix)
        docs_path = f"/api/v1/datasets/{dataset_id}/documents"
        uploaded = _api(docs_path, token, "POST", multipart=upload, boundary=boundary)
        if not isinstance(uploaded, list) or len(uploaded) != len(gold["documents"]):
            raise RuntimeError("Upload did not create exactly four documents")
        expected_names = {f"{document['title']}.txt": alias for alias, document in gold["documents"].items()}
        if {item.get("name") for item in uploaded} != set(expected_names):
            raise RuntimeError("Uploaded document names differ from gold aliases")
        document_ids = {expected_names[item["name"]]: item["id"] for item in uploaded}
        if len(set(document_ids.values())) != len(document_ids):
            raise RuntimeError("Uploaded document IDs are not unique")
        report["index"]["state"] = "parsing"
        _api(docs_path + "/parse", token, "POST", {"document_ids": list(document_ids.values())})
        deadline = time.monotonic() + 360
        while True:
            listing = _api(docs_path + "?page=1&page_size=100", token)
            rows = listing.get("docs", [])
            if listing.get("total") != 4 or {row.get("id") for row in rows} != set(document_ids.values()):
                raise RuntimeError("Dedicated dataset contains unexpected documents")
            by_id = {row["id"]: row for row in rows}
            report["index"]["documents"] = {
                alias: {"run": by_id[doc_id].get("run"), "chunk_count": by_id[doc_id].get("chunk_count", by_id[doc_id].get("chunk_num", 0))} for alias, doc_id in document_ids.items()
            }
            if all(item["run"] == "DONE" and item["chunk_count"] > 0 for item in report["index"]["documents"].values()):
                break
            if any(item["run"] == "FAIL" for item in report["index"]["documents"].values()):
                raise RuntimeError("A gold document failed indexing")
            if time.monotonic() > deadline:
                raise RuntimeError("Gold document indexing timed out")
            time.sleep(2)
        report["index"]["state"] = "ready"
        title = f"eval-source-workbench-{suffix}"
        workspace = _api("/api/v1/source-workspaces", token, "POST", {"title": title, "dataset_ids": [dataset_id]})
        workspace_id = workspace.get("id")
        if not workspace_id or workspace.get("dataset_ids") != [dataset_id]:
            raise RuntimeError("Dedicated Source Workbench workspace was not created")
        report["workspace_id"] = workspace_id
        bindings = {"disposable": True, "workspace_id": workspace_id, "workspace_title": title, "dataset_id": dataset_id, "document_ids": document_ids}
        observations = {}
        for case in gold["cases"]:
            result = _api(f"/api/v1/source-workspaces/{workspace_id}/search", token, "POST", {"query": case["query"], "page": 1})
            if result.get("query") != case["query"] or result.get("page") != 1 or not isinstance(result.get("candidates"), list):
                raise RuntimeError(f"Unexpected search response for {case['id']}")
            observations[case["id"]] = result["candidates"]
        report["observations"] = {case_id: [{"document_id": item.get("document_id"), "similarity": item.get("similarity")} for item in candidates] for case_id, candidates in observations.items()}
        report.update(score_suite(gold, bindings, observations))
        if report["status"] != "pass":
            failure = "Measured retrieval missed the declared baseline threshold"
    except Exception as error:
        report["status"] = "incomplete"
        failure = f"{type(error).__name__}: {error}"
    finally:
        if dataset_id:
            try:
                _api("/api/v1/datasets", token, "DELETE", {"ids": [dataset_id]})
                try:
                    _api(f"/api/v1/datasets/{dataset_id}", token)
                except RuntimeError as error:
                    if "HTTP 404" not in str(error) and "non-success API code" not in str(error):
                        raise
                else:
                    raise RuntimeError("Dedicated dataset remains after deletion")
                report["dataset_cleanup"] = "deleted"
            except Exception as error:
                report["dataset_cleanup"] = "failed"
                failure = f"Dataset cleanup failed: {type(error).__name__}"
                report["status"] = "incomplete"
        if failure:
            report["failure"] = failure
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    assert report["status"] == "pass", f"Source Workbench quality {report['status']}: {failure}; report={report_path}"
