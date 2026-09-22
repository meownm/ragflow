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


@pytest.mark.parametrize("workflow,count", [("tests.yml", 3), ("sep-tests.yml", 2), ("release.yml", 1)])
def test_all_main_image_ci_build_callers_supply_checkout_identity(workflow, count):
    document = yaml.safe_load((ROOT / ".github/workflows" / workflow).read_text())
    builds = []
    for job in document["jobs"].values():
        for step in job.get("steps", []):
            script = step.get("run", "")
            if "docker build " in script and "-f Dockerfile " in script:
                builds.append(script)
                assert "git describe --tags --match='v*' --first-parent --always" in script
                assert "git rev-parse HEAD" in script
                assert '--build-arg "RAGFLOW_BUILD_VERSION=${build_version}"' in script
                assert '--build-arg "RAGFLOW_SOURCE_REVISION=${source_revision}"' in script
    assert len(builds) == count


@pytest.mark.parametrize("workflow", ["tests.yml", "sep-tests.yml"])
def test_compose_ci_uses_isolated_postgres_overlay(workflow):
    document = yaml.safe_load((ROOT / ".github/workflows" / workflow).read_text())
    prepare_steps = [
        step["run"]
        for job in document["jobs"].values()
        for step in job.get("steps", [])
        if step.get("name") == "Prepare function test environment"
    ]

    assert len(prepare_steps) == 2
    for script in prepare_steps:
        assert "/^DB_TYPE=/d" not in script
        assert 'echo "DB_TYPE=mysql"' not in script

    compose_commands = [
        step["run"]
        for job in document["jobs"].values()
        for step in job.get("steps", [])
        if "sudo docker compose " in step.get("run", "")
    ]
    assert compose_commands
    assert all("-f docker/docker-compose.ci-postgres.yml" in script for script in compose_commands)


def test_ci_postgres_overlay_disables_mysql_and_gates_ragflow_on_postgres():
    overlay = (ROOT / "docker/docker-compose.ci-postgres.yml").read_text()
    assert "postgres:16-alpine" in overlay
    assert "pg_isready" in overlay
    assert "mysql-disabled" in overlay
    assert "condition: service_healthy" in overlay
    assert 'REGISTER_ENABLED: "1"' in overlay


@pytest.mark.parametrize("workflow", ["tests.yml", "sep-tests.yml"])
def test_live_api_ci_uses_provider_free_model_profile(workflow):
    document = yaml.safe_load((ROOT / ".github/workflows" / workflow).read_text())
    test_roots = (
        "test/testcases/test_sdk_api",
        "test/testcases/restful_api",
        "test/testcases/test_web_api",
        "test/testcases/test_admin_api",
    )
    commands = [
        step["run"]
        for job in document["jobs"].values()
        for step in job.get("steps", [])
        if "pytest " in step.get("run", "") and any(root in step["run"] for root in test_roots)
    ]

    assert len(commands) == 8
    assert all("--model-profile=local" in command for command in commands)
