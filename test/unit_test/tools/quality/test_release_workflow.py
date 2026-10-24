"""Release event, ownership, identity and fail-closed wiring."""

import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[4]


def workflow(name):
    return yaml.safe_load((ROOT / ".github/workflows" / name).read_text())


def test_publication_requires_same_run_qualification_and_separate_environment():
    jobs = workflow("release.yml")["jobs"]
    assert jobs["qualification"]["uses"] == "./.github/workflows/tests.yml"
    assert jobs["qualification"]["with"]["profile"] == "release"
    publish = jobs["publish"]
    assert set(publish["needs"]) == {"prepare", "qualification", "publish_ready"}
    assert publish["runs-on"] == ["self-hosted", "ragflow-release", "Linux", "ragflow-isolated-vm"]
    assert publish["environment"] == "internal-release"
    assert publish["concurrency"] == {"group": "release-publication", "cancel-in-progress": False, "queue": "max"}
    assert "always()" not in publish["if"]
    scripts = "\n".join(step.get("run", "") for step in publish["steps"])
    assert "RECEIPT_SHA256" in scripts
    assert "release_publish" in scripts
    assert "docker build" not in scripts and "git push" not in scripts
    assert "DOCKERHUB" not in scripts and "twine" not in scripts


def test_publisher_is_rechecked_after_qualification_before_runner_assignment():
    job = workflow("release.yml")["jobs"]["publish_ready"]
    assert set(job["needs"]) == {"prepare", "qualification"}
    assert job["runs-on"] == "ubuntu-24.04"
    assert "always()" not in job["if"]
    probe = next(step for step in job["steps"] if "run" in step)
    assert probe["run"] == "python3 -m tools.quality.release_runtime ready --roles ragflow-release"
    assert probe["env"]["GH_TOKEN"] == "${{ secrets.RUNNER_READ_TOKEN }}"


def test_qualification_is_single_owner_and_release_does_not_select_changes():
    jobs = workflow("tests.yml")["jobs"]
    assert set(jobs["evidence"]["needs"]) >= {"build_image", "frontend", "browser_regression", "package_python", "build_cli", "ragflow_tests_infinity", "ragflow_tests_elasticsearch"}
    assert jobs["evidence"]["if"] == "always()"
    assert jobs["browser_regression"]["with"]["image_digest"] == "${{ needs.build_image.outputs.digest }}"
    for engine in ("infinity", "elasticsearch"):
        lane = jobs["ragflow_tests_" + engine]
        assert lane["env"]["RAGFLOW_IMAGE"] == "${{ needs.build_image.outputs.digest }}"
        assert not any("docker build" in step.get("run", "") for step in lane["steps"])
        prepare = next(step for step in lane["steps"] if step.get("name") == "Prepare isolated stack")
        assert prepare["run"] == "bash ../deployment/runner/prepare-engine.sh"


def test_stable_version_tags_and_manual_candidates_are_explicit():
    release = workflow("release.yml")
    events = release.get("on", release.get(True))
    assert events["push"]["tags"] == ["v*.*.*"]
    assert events["workflow_dispatch"]["inputs"]["dry_run"]["default"] is True
    scripts = "\n".join(step.get("run", "") for step in release["jobs"]["prepare"]["steps"])
    assert set(events) == {"push", "workflow_dispatch"}
    assert "RELEASE_NIGHTLY_ENABLED" not in str(release)
    assert "stable tag/project version mismatch" in scripts
    assert "git tag" not in scripts


def test_queue_watchdog_is_manual_and_push_smoke_remains_enabled():
    assert workflow("runner-watchdog.yml")["on"] == {"workflow_dispatch": {}}
    assert workflow("pr-smoke.yml")["on"]["push"]["branches"] == ["main"]


@pytest.mark.parametrize(
    "event,action,head_repository,labels,label,expected",
    [
        ("pull_request", "labeled", "meownm/ragflow", ["ci"], "ci", True),
        ("pull_request", "labeled", "meownm/ragflow", ["ci", "documentation"], "documentation", False),
        ("pull_request", "labeled", "meownm/ragflow", ["documentation"], "documentation", False),
        ("pull_request", "labeled", "external/ragflow", ["ci"], "ci", False),
        ("pull_request", "opened", "meownm/ragflow", ["ci"], "", True),
        ("pull_request", "synchronize", "meownm/ragflow", ["ci"], "", True),
        ("pull_request", "reopened", "meownm/ragflow", ["ci"], "", True),
        ("pull_request", "synchronize", "meownm/ragflow", [], "", False),
        ("pull_request", "synchronize", "external/ragflow", ["ci"], "", False),
        ("workflow_dispatch", "", "", [], "", True),
        ("push", "", "", [], "", True),
    ],
)
def test_actual_heavy_ci_condition_rejects_unrelated_labels(event, action, head_repository, labels, label, expected):
    condition = workflow("tests.yml")["jobs"]["ready"]["if"].removeprefix("${{").removesuffix("}}")
    values = {
        "github.event_name": event,
        "github.event.action": action,
        "github.event.pull_request.head.repo.full_name": head_repository,
        "github.event.pull_request.labels.*.name": labels,
        "github.event.label.name": label,
        "github.repository": "meownm/ragflow",
    }
    for name, value in values.items():
        condition = condition.replace(name, repr(value))
    # Evaluate the workflow's boolean predicate, not a separately copied policy.
    condition = condition.replace("&&", "and").replace("||", "or")
    assert eval(condition, {"__builtins__": {}, "contains": lambda items, item: item in items}) is expected


@pytest.mark.parametrize(
    "event,ref,dry_run,publish,tag,nightly",
    [
        ("push", "refs/tags/v1.2.3", "", "true", "v1.2.3", "false"),
        ("workflow_dispatch", "refs/heads/main", "true", "false", "nightly-123-1", "true"),
        ("workflow_dispatch", "refs/heads/main", "false", "true", "nightly-123-1", "true"),
        ("schedule", "refs/heads/main", "false", None, None, None),
        ("pull_request", "refs/heads/main", "false", None, None, None),
        ("push", "refs/heads/main", "false", None, None, None),
        ("push", "refs/tags/v1.2.4", "", None, None, None),
        ("push", "refs/tags/v1.2.3-rc1", "", None, None, None),
        ("workflow_dispatch", "refs/heads/feature", "false", None, None, None),
    ],
)
def test_actual_release_event_resolver_preserves_publication_boundaries(tmp_path, event, ref, dry_run, publish, tag, nightly):
    meta = next(step for step in workflow("release.yml")["jobs"]["prepare"]["steps"] if step.get("id") == "meta")
    script = meta["run"].removeprefix("python3 - <<'PY'\n").removesuffix("PY\n")
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "1.2.3"\n', encoding="utf-8")
    output = tmp_path / "output.txt"
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env={
            **os.environ,
            "GITHUB_EVENT_NAME": event,
            "GITHUB_REF": ref,
            "DRY_RUN": dry_run,
            "GITHUB_RUN_ID": "123",
            "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_OUTPUT": str(output),
        },
        capture_output=True,
        text=True,
    )
    if publish is None:
        assert result.returncode != 0
        assert not output.exists()
    else:
        assert result.returncode == 0, result.stderr
        assert dict(line.split("=", 1) for line in output.read_text().splitlines()) == {"publish": publish, "tag": tag, "nightly": nightly}


def test_six_portable_platforms_have_native_execution_without_cgo():
    job = workflow("release-cli.yml")["jobs"]["build_cli"]
    assert job["env"]["CGO_ENABLED"] == "0"
    assert {(item["goos"], item["goarch"]) for item in job["strategy"]["matrix"]["include"]} == {(system, arch) for system in ("linux", "darwin", "windows") for arch in ("amd64", "arm64")}
    assert "build-portable-cli.sh" in str(job) and "portable_cli_probe.py" in str(job)
