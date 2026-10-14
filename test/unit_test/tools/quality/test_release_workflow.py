"""Release event, ownership, identity and fail-closed wiring."""

from pathlib import Path
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


def test_stable_only_version_tags_and_nightly_does_not_trigger_itself():
    release = workflow("release.yml")
    events = release.get("on", release.get(True))
    assert events["push"]["tags"] == ["v*.*.*"]
    assert events["workflow_dispatch"]["inputs"]["dry_run"]["default"] is True
    scripts = "\n".join(step.get("run", "") for step in release["jobs"]["prepare"]["steps"])
    assert "RELEASE_NIGHTLY_ENABLED" in str(release)
    assert "stable tag/project version mismatch" in scripts
    assert "git tag" not in scripts


def test_six_portable_platforms_have_native_execution_without_cgo():
    job = workflow("release-cli.yml")["jobs"]["build_cli"]
    assert job["env"]["CGO_ENABLED"] == "0"
    assert {(item["goos"], item["goarch"]) for item in job["strategy"]["matrix"]["include"]} == {(system, arch) for system in ("linux", "darwin", "windows") for arch in ("amd64", "arm64")}
    assert "build-portable-cli.sh" in str(job) and "portable_cli_probe.py" in str(job)
