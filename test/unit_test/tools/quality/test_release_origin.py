"""Tampering and foreign attempts fail before files are extracted."""

import hashlib
import io
import json
import zipfile

import pytest

from tools.quality.release_origin import acquire_producers, extract_verified, qualification_producers, validate_artifact, verify_release_download


def test_paginated_status_query_keeps_filter_and_reads_final_page(monkeypatch):
    from tools.quality import release_origin as origin_module

    calls = []

    def api(path):
        calls.append(path)
        return {"workflow_runs": list(range(100)) if path.endswith("page=1") else [100]}

    monkeypatch.setattr(origin_module, "api", api)
    assert origin_module.api_items("repos/meownm/ragflow/actions/runs?status=queued", "workflow_runs") == list(range(101))
    assert calls == [f"repos/meownm/ragflow/actions/runs?status=queued&per_page=100&page={page}" for page in (1, 2)]


@pytest.fixture
def origin():
    run = {"id": 123, "run_attempt": 2, "head_sha": "a" * 40, "repository": {"id": 1}, "head_repository": {"id": 1}}
    artifact = {
        "id": 10,
        "name": "qualification-123-2",
        "expired": False,
        "digest": "sha256:" + "b" * 64,
        "created_at": "2026-10-11T10:01:00Z",
        "workflow_run": {"id": 123, "head_sha": "a" * 40, "repository_id": 1, "head_repository_id": 1},
    }
    jobs = [{"name": "qualification / evidence", "run_attempt": 2, "head_sha": "a" * 40, "conclusion": "success", "started_at": "2026-10-11T10:00:00Z", "completed_at": "2026-10-11T10:02:00Z"}]
    return artifact, run, jobs


def test_another_successful_job_cannot_claim_producer_origin(origin):
    artifact, run, jobs = origin
    jobs[0]["name"] = "qualification / frontend"
    with pytest.raises(ValueError, match="successful current-attempt"):
        validate_artifact(artifact, run, jobs, "qualification-123-2")


@pytest.mark.parametrize("field,value", [("name", "qualification-123-1"), ("expired", True), ("digest", "bad"), ("id", 0), ("created_at", "2026-10-10T10:01:00Z")])
def test_foreign_metadata_rejected(origin, field, value):
    artifact, run, jobs = origin
    validate_artifact(artifact, run, jobs, "qualification-123-2")
    artifact[field] = value
    with pytest.raises(ValueError):
        validate_artifact(artifact, run, jobs, "qualification-123-2")


@pytest.mark.parametrize("field", ["id", "head_sha", "repository_id", "head_repository_id"])
def test_foreign_artifact_origin_rejected(origin, field):
    artifact, run, jobs = origin
    artifact["workflow_run"][field] = "foreign"
    with pytest.raises(ValueError):
        validate_artifact(artifact, run, jobs, "qualification-123-2")


@pytest.mark.parametrize("name", ["../escape", "/absolute", "C:/file", "a\\escape", "./file", "a//file"])
def test_archive_paths_rejected_before_extraction(tmp_path, name):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        entry = zipfile.ZipInfo()
        entry.filename = name
        archive.writestr(entry, "foreign")
    data = stream.getvalue()
    with pytest.raises(ValueError, match="unsafe"):
        extract_verified(data, "sha256:" + hashlib.sha256(data).hexdigest(), tmp_path / "out")
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("foreign", [False, True])
def test_producer_set_is_validated_before_download(origin, tmp_path, monkeypatch, foreign):
    from copy import deepcopy
    import tools.quality.release_origin as module

    artifact, run, jobs = origin
    run.update(repository={"id": 1, "full_name": "meownm/ragflow"}, event="schedule", path=".github/workflows/release.yml")
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("payload.txt", "verified bytes")
    content = stream.getvalue()
    expected = qualification_producers("123", 2)
    artifacts = []
    producer_jobs = []
    for identifier, (name, producer) in enumerate(expected.items(), start=10):
        item = deepcopy(artifact)
        item.update(id=identifier, name=name, digest="sha256:" + hashlib.sha256(content).hexdigest())
        artifacts.append(item)
        job = deepcopy(jobs[0])
        job["name"] = "qualification / " + producer
        producer_jobs.append(job)
    if foreign:
        artifacts[-1]["workflow_run"]["head_sha"] = "c" * 40
    downloaded = []

    def api(path, raw=False):
        if path.endswith("/attempts/2"):
            return run
        if "/jobs?" in path:
            return {"jobs": producer_jobs}
        if "/artifacts?" in path:
            return {"artifacts": artifacts}
        assert path.endswith("/zip") and raw
        downloaded.append(path)
        return content

    monkeypatch.setattr(module, "api", api)
    destination = tmp_path / "incoming"
    if foreign:
        with pytest.raises(ValueError, match="foreign"):
            acquire_producers("meownm/ragflow", "123", 2, run["head_sha"], destination)
        assert not downloaded and not destination.exists()
    else:
        assert acquire_producers("meownm/ragflow", "123", 2, run["head_sha"], destination) == artifacts
        assert len(downloaded) == len(expected)
        assert {path.name for path in destination.iterdir()} == set(expected)


def test_archive_hash_checked_before_extraction(tmp_path):
    with pytest.raises(ValueError, match="digest"):
        extract_verified(b"not archive", "sha256:" + "a" * 64, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_local_receipt_requires_github_asset_digest(tmp_path, monkeypatch):
    import tools.quality.release_origin as origin

    proof = {"repository": "meownm/ragflow", "release_tag": "v1.0.0", "bundle_id": "a", "qualification_sha256": "b"}
    (tmp_path / "release-manifest.json").write_text(json.dumps(proof))
    (tmp_path / "publication-receipt.json").write_text(json.dumps(proof))
    (tmp_path / "candidate-receipt.json").write_text("original")
    assets = [{"name": p.name, "digest": "sha256:" + hashlib.sha256(p.read_bytes()).hexdigest()} for p in tmp_path.iterdir()]
    monkeypatch.setattr(origin, "api", lambda _: {"draft": False, "tag_name": "v1.0.0", "assets": assets})
    verify_release_download(tmp_path, proof)
    (tmp_path / "candidate-receipt.json").write_text("substituted")
    with pytest.raises(ValueError, match="GitHub origin"):
        verify_release_download(tmp_path, proof)
