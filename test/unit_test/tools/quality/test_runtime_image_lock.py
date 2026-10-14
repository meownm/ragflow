"""Supporting image evidence must bind both engines to identical immutable image bytes."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from tools.quality import runtime_image_lock as images


CURRENT = {"repository": "meownm/ragflow", "sha": "a" * 40, "run_id": "123", "attempt": 2}
CANDIDATE = "registry.example:5443/ragflow@sha256:" + "b" * 64
PINNED = "docker.io/library/postgres@sha256:" + "c" * 64
IMAGE_ID = "sha256:" + "d" * 64
RESOLVED = {"image": PINNED, "image_id": IMAGE_ID, "platform": "linux/amd64"}


def fixture_lock():
    return {
        "schema": 1,
        "purpose": "qualification-runtime-images",
        **CURRENT,
        "candidate_image": CANDIDATE,
        "platform": "linux/amd64",
        "services": {engine: {"postgres": {"source_image": "postgres:16-alpine", **RESOLVED}} for engine in images.ENGINES},
    }


def verified(lock):
    content = images.serialize(lock)
    return images.verify_lock(content, hashlib.sha256(content).hexdigest(), CURRENT, CANDIDATE)


def test_compose_selection_drops_credentials_and_inactive_profiles():
    config = {
        "services": {
            "ragflow-cpu": {"image": CANDIDATE, "profiles": ["cpu"]},
            "ragflow-gpu": {"image": "unused:latest", "profiles": ["gpu"]},
            "postgres": {"image": "postgres:16-alpine", "environment": {"PASSWORD": "do-not-emit"}},
            "infinity": {"image": "infiniflow/infinity:v0.7.0", "profiles": ["infinity"]},
            "elasticsearch": {"image": "elasticsearch:8", "profiles": ["elasticsearch"]},
            "mysql": {"image": "mysql:8", "profiles": ["mysql-disabled"]},
        }
    }
    result = images.supporting_images(config, "infinity", CANDIDATE)
    assert result == {"infinity": "infiniflow/infinity:v0.7.0", "postgres": "postgres:16-alpine"}
    assert "do-not-emit" not in json.dumps(result)


def test_both_engine_lanes_resolve_shared_image_only_once(monkeypatch):
    requested = []
    monkeypatch.setattr(images, "compose_images", lambda *_: {"postgres": "postgres:16-alpine"})

    def resolve(reference, *_):
        requested.append(reference)
        return RESOLVED

    monkeypatch.setattr(images, "resolve_image", resolve)
    lock = images.build_lock(CURRENT, Path("private.env"), CANDIDATE, ["docker"])
    assert requested == ["postgres:16-alpine"]
    assert verified(lock) == fixture_lock()
    assert images.serialize(lock) == images.serialize(fixture_lock())


def test_build_rejects_shared_service_that_changes_between_engines(monkeypatch):
    monkeypatch.setattr(images, "compose_images", lambda engine, *_: {"postgres": f"postgres:{engine}"})
    monkeypatch.setattr(images, "resolve_image", lambda *_: deepcopy(RESOLVED))
    with pytest.raises(ValueError, match="shared supporting service changed"):
        images.build_lock(CURRENT, Path("private.env"), CANDIDATE, ["docker"])


@pytest.mark.parametrize(
    "key,value",
    [
        ("repository", "foreign/ragflow"),
        ("sha", "e" * 40),
        ("run_id", "124"),
        ("attempt", 3),
        ("attempt", True),
        ("schema", True),
        ("candidate_image", "registry.example:5443/ragflow@sha256:" + "f" * 64),
        ("platform", "windows/amd64"),
        ("purpose", "different"),
    ],
)
def test_current_identity_and_purpose_are_required(key, value):
    lock = fixture_lock()
    lock[key] = value
    with pytest.raises(ValueError):
        verified(lock)


def test_file_tampering_fails_even_with_valid_json():
    content = images.serialize(fixture_lock())
    with pytest.raises(ValueError, match="file digest mismatch"):
        images.verify_lock(content.replace(b"16-alpine", b"17-alpine"), hashlib.sha256(content).hexdigest(), CURRENT, CANDIDATE)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda lock: lock["services"].pop("infinity"),
        lambda lock: lock["services"]["infinity"].clear(),
        lambda lock: lock["services"]["infinity"]["postgres"].update(image="postgres:16-alpine"),
        lambda lock: lock["services"]["infinity"]["postgres"].update(image_id="unknown"),
        lambda lock: lock["services"]["infinity"]["postgres"].update(image=None),
        lambda lock: lock["services"]["infinity"]["postgres"].update(environment={"PASSWORD": "secret"}),
        lambda lock: lock["services"]["infinity"]["postgres"].update(image="other/postgres@sha256:" + "c" * 64),
        lambda lock: lock["services"]["infinity"]["postgres"].update(image=PINNED.replace("c" * 64, "e" * 64)),
        lambda lock: lock["services"]["infinity"]["postgres"].update(source_image="postgres:17-alpine"),
        lambda lock: lock["services"]["infinity"].update({"ragflow-cpu": lock["services"]["infinity"]["postgres"]}),
    ],
)
def test_missing_digest_extra_fields_or_changed_engine_images_fail(mutation):
    lock = fixture_lock()
    mutation(lock)
    with pytest.raises(ValueError):
        verified(lock)


@pytest.mark.parametrize("reference", ["https://registry/image", "user:password@registry/image", "image@sha256:unknown", "image\nsecret", "image name"])
def test_invalid_or_credential_bearing_image_reference_is_rejected(reference):
    with pytest.raises(ValueError):
        images.repository_name(reference)


@pytest.mark.parametrize(
    "reference,repository",
    [
        ("postgres:16-alpine", "docker.io/library/postgres"),
        ("docker.io/postgres:16-alpine", "docker.io/library/postgres"),
        ("index.docker.io/library/postgres:16", "docker.io/library/postgres"),
        ("infiniflow/infinity:v0.7.0", "docker.io/infiniflow/infinity"),
        ("registry.example:5443/path/image:tag", "registry.example:5443/path/image"),
    ],
)
def test_repository_normalization(reference, repository):
    assert images.repository_name(reference) == repository


def test_remote_resolution_pulls_only_digest_and_keeps_image_id(monkeypatch):
    calls = []

    def command(arguments, **_):
        calls.append(arguments)
        if arguments[1:4] == ["manifest", "inspect", "--verbose"]:
            return json.dumps({"Descriptor": {"digest": "sha256:" + "c" * 64}}).encode()
        if arguments[1] == "pull":
            return b"pulled privately"
        return json.dumps([{"Id": IMAGE_ID, "Os": "linux", "Architecture": "amd64", "RepoDigests": [PINNED], "Config": {"Env": ["secret=not-persisted"]}}]).encode()

    monkeypatch.setattr(images, "_command", command)
    assert images.resolve_image("postgres:16-alpine", ["docker"], "linux/amd64") == RESOLVED
    assert calls[1] == ["docker", "pull", "--platform", "linux/amd64", PINNED]
    assert all("tag" not in command and "push" not in command for command in calls)


@pytest.mark.parametrize(
    "details",
    [
        [{"Id": IMAGE_ID, "Os": "linux", "Architecture": "amd64", "RepoDigests": []}],
        [{"Id": IMAGE_ID, "Os": "linux", "Architecture": "arm64", "RepoDigests": [PINNED]}],
        [{"Id": IMAGE_ID, "Os": "linux", "Architecture": "amd64", "RepoDigests": [PINNED.replace("c" * 64, "e" * 64)]}],
        [],
    ],
)
def test_actual_digest_and_platform_are_required(details):
    with pytest.raises(ValueError):
        images.inspected_image(details, PINNED, "linux/amd64")


def test_multiarch_manifest_selects_requested_compatible_platform():
    manifest = [{"Descriptor": {"digest": "sha256:" + letter * 64, "platform": {"os": "linux", "architecture": arch}}} for letter, arch in [("a", "amd64"), ("b", "arm64")]]
    assert images.manifest_digest(manifest, "linux/amd64") == "sha256:" + "a" * 64
    with pytest.raises(ValueError):
        images.manifest_digest(manifest, "linux/s390x")
    with pytest.raises(ValueError):
        images.manifest_digest({}, "linux/amd64")


def test_materialized_override_has_only_images_and_checks_runtime_bytes(monkeypatch):
    lock = verified(fixture_lock())
    monkeypatch.setattr(images, "compose_images", lambda *_: {"postgres": "postgres:16-alpine"})
    pulled = []
    monkeypatch.setattr(images, "resolve_image", lambda reference, *_: pulled.append(reference) or deepcopy(RESOLVED))
    override = images.materialize(lock, "infinity", Path("private.env"), ["docker"])
    assert json.loads(override) == {"services": {"postgres": {"image": PINNED}}}
    assert pulled == [PINNED]
    monkeypatch.setattr(images, "resolve_image", lambda *_: {**RESOLVED, "image_id": "sha256:" + "e" * 64})
    with pytest.raises(ValueError, match="bytes differ"):
        images.materialize(lock, "infinity", Path("private.env"), ["docker"])


def test_materialize_checks_exact_active_service_set_before_pulling(monkeypatch):
    monkeypatch.setattr(images, "compose_images", lambda *_: {"postgres": "postgres:17-alpine"})
    monkeypatch.setattr(images, "resolve_image", lambda *_: pytest.fail("foreign config pulled an image"))
    with pytest.raises(ValueError, match="set differs"):
        images.materialize(verified(fixture_lock()), "infinity", Path("private.env"), ["docker"])


def test_compose_configuration_is_captured_privately_with_explicit_engine(monkeypatch, tmp_path):
    def command(arguments, **options):
        assert arguments[-3:] == ["config", "--format", "json"]
        assert options["env"]["DOC_ENGINE"] == "infinity"
        assert options["env"]["COMPOSE_PROFILES"] == "infinity,cpu,tei-cpu,deepdoc"
        assert options["env"]["RAGFLOW_IMAGE"] == CANDIDATE
        return json.dumps(
            {
                "services": {
                    "ragflow-cpu": {"image": CANDIDATE},
                    "postgres": {"image": "postgres:16-alpine", "environment": {"PASSWORD": "private"}},
                }
            }
        ).encode()

    monkeypatch.setattr(images, "_command", command)
    assert images.compose_images("infinity", tmp_path / "private.env", CANDIDATE, ["docker"]) == {"postgres": "postgres:16-alpine"}


def test_command_failure_never_exposes_stdout_stderr(monkeypatch, capsys):
    from types import SimpleNamespace

    monkeypatch.setattr(
        images.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1,
            stdout=b"secret environment",
            stderr=b"registry credentials",
        ),
    )
    with pytest.raises(ValueError, match="prerequisite command failed") as failure:
        images._command(["docker", "compose", "config"])
    assert "secret" not in str(failure.value) and "credentials" not in str(failure.value)
    assert capsys.readouterr().out == ""
