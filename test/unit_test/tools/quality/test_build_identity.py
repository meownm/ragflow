"""VERSION compatibility without copying Git history into a Docker context."""

from pathlib import Path
import subprocess
import sys

import pytest
import yaml

from tools.quality.write_build_identity import write


ROOT = Path(__file__).resolve().parents[4]


@pytest.fixture
def project(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nversion="1.2.3"\n', encoding="utf-8")
    return tmp_path


def test_standalone_metadata_explicitly_unverified(project):
    assert write(project) == ("1.2.3-unverified", "unverified")
    assert (project / "VERSION").read_bytes() == b"1.2.3-unverified\n"
    assert (project / "SOURCE_REVISION").read_bytes() == b"unverified\n"


def test_ci_supplied_git_describe_value_preserved(project):
    for args in (["init", "-q"], ["config", "user.name", "Synthetic"], ["config", "user.email", "qa@example.invalid"], ["add", "."], ["commit", "-qm", "baseline"], ["tag", "v1.2.3"]):
        subprocess.run(["git", "-C", str(project), *args], check=True, capture_output=True)
    version = subprocess.check_output(["git", "-C", str(project), "describe", "--tags", "--match=v*", "--first-parent", "--always"], text=True).strip()
    revision = subprocess.check_output(["git", "-C", str(project), "rev-parse", "HEAD"], text=True).strip()
    assert write(project, version, revision) == (version, revision)
    assert (project / "VERSION").read_text().strip() == "v1.2.3"


@pytest.mark.parametrize("version", ["v1.2.3", "v1.2.3-4-gabcdef12", "abcdef12", "1.2.3+build.1", "a" * 128])
def test_allowed_version_forms(project, version):
    assert write(project, version, "A" * 40) == (version, "a" * 40)


@pytest.mark.parametrize("version", ["bad\nversion", "../version", "$(secret)", "v 1", "-bad", "версия", "x" * 129])
def test_invalid_version_refused_before_writes(project, version):
    with pytest.raises(ValueError, match="version"):
        write(project, version)
    assert not (project / "VERSION").exists()
    assert not (project / "SOURCE_REVISION").exists()


@pytest.mark.parametrize("revision", ["abc123", "g" * 40, "a" * 41, "a" * 40 + "\n"])
def test_invalid_revision_refused_before_writes(project, revision):
    with pytest.raises(ValueError, match="revision"):
        write(project, "v1.2.3", revision)
    assert not (project / "VERSION").exists()


def test_cli_default_without_git(project):
    result = subprocess.run([sys.executable, str(ROOT / "tools/quality/write_build_identity.py"), "--root", str(project)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "metadata only" in result.stdout
    assert "1.2.3-unverified" in result.stdout


def test_dockerfile_preserves_runtime_files_without_git():
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "COPY .git" not in dockerfile
    assert "git describe" not in dockerfile
    assert "ARG RAGFLOW_BUILD_VERSION" in dockerfile
    assert "ARG RAGFLOW_SOURCE_REVISION" in dockerfile
    assert "COPY --from=builder /ragflow/VERSION /ragflow/VERSION" in dockerfile
    assert "COPY --from=builder /ragflow/SOURCE_REVISION /ragflow/SOURCE_REVISION" in dockerfile
    assert ".git" in (ROOT / ".dockerignore").read_text().splitlines()


def test_one_qualified_image_build_supplies_and_checks_source_identity():
    jobs = yaml.safe_load((ROOT / ".github/workflows/tests.yml").read_text())["jobs"]
    builds = [step for job in jobs.values() for step in job.get("steps", []) if "build-qualified-image.sh" in step.get("run", "")]
    assert len(builds) == 1
    assert builds[0] in jobs["build_image"]["steps"]
    assert jobs["build_image"]["env"]["SOURCE_SHA"] == "${{ inputs.source_sha || github.sha }}"
    script = (ROOT / "deployment/runner/build-qualified-image.sh").read_text()
    assert '--build-arg "RAGFLOW_BUILD_VERSION=${version}"' in script
    assert '--build-arg "RAGFLOW_SOURCE_REVISION=${SOURCE_SHA}"' in script
    assert 'cat "$digest" /ragflow/SOURCE_REVISION)" = "$SOURCE_SHA"' in script


@pytest.mark.parametrize("workflow,profile", [("sep-tests.yml", "targeted"), ("release.yml", "release")])
def test_release_and_targeted_callers_delegate_to_qualification(workflow, profile):
    jobs = yaml.safe_load((ROOT / ".github/workflows" / workflow).read_text())["jobs"]
    qualified = jobs["qualification"]
    assert qualified["uses"] == "./.github/workflows/tests.yml"
    assert qualified["with"]["profile"] == profile
    assert not any("docker build " in step.get("run", "") for job in jobs.values() for step in job.get("steps", []))


@pytest.mark.parametrize("engine", ["infinity", "elasticsearch"])
def test_engine_stack_runs_qualified_digest_with_isolated_postgres(engine):
    jobs = yaml.safe_load((ROOT / ".github/workflows/tests.yml").read_text())["jobs"]
    job = jobs[f"ragflow_tests_{engine}"]
    assert job["env"]["RAGFLOW_IMAGE"] == "${{ needs.build_image.outputs.digest }}"
    scripts = "\n".join(step.get("run", "") for step in job["steps"])
    assert "bash deployment/runner/test-engine.sh" in scripts
    assert "runtime_image_lock materialize" in scripts
    assert "docker build " not in scripts
    script = (ROOT / "deployment/runner/test-engine.sh").read_text()
    assert "-f docker/docker-compose.ci-postgres.yml" in script
    assert '"${compose[@]}" up -d --no-build' in script
    assert '"${RUNTIME_IMAGE_OVERRIDE:?}"' in script
    assert 'test "$actual" = "$expected"' in script


def test_ci_postgres_overlay_disables_mysql_and_gates_ragflow_on_postgres():
    overlay = (ROOT / "docker/docker-compose.ci-postgres.yml").read_text()
    assert "postgres:16-alpine" in overlay
    assert "pg_isready" in overlay
    services = yaml.load(overlay, Loader=yaml.BaseLoader)["services"]
    assert "mysql" not in yaml.safe_load((ROOT / "docker/docker-compose-base.yml").read_text())["services"]
    assert "mysql" not in services["ragflow-cpu"]["depends_on"]
    assert services["ragflow-cpu"]["depends_on"]["postgres"]["condition"] == "service_healthy"
    assert "condition: service_healthy" in overlay
    assert 'REGISTER_ENABLED: "1"' in overlay


def test_all_api_suites_use_local_models_and_record_case_evidence():
    script = (ROOT / "deployment/runner/test-engine.sh").read_text()
    assert "for suite in test_sdk_api restful_api test_web_api test_admin_api; do" in script
    assert '"test/testcases/${suite}"' in script
    assert "pytest --model-profile=local" in script
    assert '--api-case-suite "$suite" --api-case-evidence' in script
    assert "python -m tools.quality.api_case_evidence" in script
