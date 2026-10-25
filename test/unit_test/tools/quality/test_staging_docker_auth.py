"""Ephemeral staging auth must stay private and cleanup only its own job config."""

import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from tools.quality import artifact_lifecycle as life


ROOT = Path(__file__).resolve().parents[4]
HELPER = ROOT / "deployment/runner/staging-docker-auth.sh"
SCRIPTS = ("staging-docker-auth.sh", "build-qualified-image.sh", "prepare-engine.sh", "test-engine.sh", "cleanup-engine.sh")


def _bash():
    if os.name == "nt":
        candidate = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/bin/bash.exe"
        if candidate.is_file():
            return str(candidate)
    else:
        candidate = shutil.which("bash")
        if candidate:
            return candidate
    pytest.skip("Bash is required to verify Docker auth scripts")


def _path(value):
    if os.name == "nt" and len(value) > 3 and value[0] == "/" and value[2] == "/":
        return Path(value[1].upper() + ":" + value[2:])
    return Path(value)


@pytest.fixture
def scenario(tmp_path):
    binary = tmp_path / "stub-bin"
    binary.mkdir()
    (binary / "python3").write_text(f'#!/usr/bin/env bash\nexec "{Path(sys.executable).as_posix()}" "$@"\n', encoding="utf-8")
    (binary / "sudo").write_text(
        """#!/usr/bin/env bash
[[ "$1" == -n ]] || exit 90
shift
printf '%s\n' "$@" >> "$TEST_SUDO_LOG"
[[ "$1" != chown ]] || exit "${TEST_CHOWN_FAIL:-0}"
exec "$@"
""",
        encoding="utf-8",
    )
    (binary / "docker").write_text(
        """#!/usr/bin/env bash
set -euo pipefail
[[ "$1" == --config && -d "$2" ]] || exit 91
config=$2
shift 2
if [[ "$1" == login ]]; then
  [[ "$2" == "$TEST_EXPECTED_REGISTRY" && "$3" == --username && "$4" == "$TEST_EXPECTED_USER" && "$5" == --password-stdin ]] || exit 92
  IFS= read -r password
  [[ "$password" == fake-registry-password ]] || exit 93
  [[ -z "${NEXUS_STAGING_BUILD_PASSWORD:-}${NEXUS_STAGING_READ_PASSWORD:-}${NEXUS_STAGING_BUILD_USER:-}${NEXUS_STAGING_READ_USER:-}${NEXUS_RELEASE_READ_USER:-}${NEXUS_RELEASE_READ_PASSWORD:-}" ]] || exit 94
  python3 - "$config/config.json" "$2" <<'PY'
import json,sys
from pathlib import Path
path=Path(sys.argv[1])
data=json.loads(path.read_text())
assert data.get('auths') and not data.get('credsStore') and not any(data.get('credHelpers',{}).values())
data.setdefault('auths',{})[sys.argv[2]]={'auth':'fake-auth'}
path.write_text(json.dumps(data))
PY
  echo 'fake-registry-password username diagnostic'
  echo 'fake-registry-password username warning' >&2
  [[ "${TEST_LOGIN_FAIL:-0}" == 0 ]] || exit 95
elif [[ "$1" == compose ]]; then
  printf '%s\n' "$@" >> "$TEST_DOCKER_LOG"
  if [[ " $* " == *' config --format json '* ]]; then
    printf '%s\n' '{"services":{"ragflow":{"image":"synthetic-image"}}}'
  fi
  [[ " $* " != *' up '* ]] || exit 42
else
  exit 96
fi
""",
        encoding="utf-8",
    )
    for path in binary.iterdir():
        path.chmod(0o700)
    temporary = tmp_path / "runner-temp"
    temporary.mkdir()
    github_env = tmp_path / "github-env"
    github_env.touch()
    persistent = tmp_path / "persistent-docker"
    persistent.mkdir()
    (persistent / "config.json").write_text("do-not-touch", encoding="utf-8")
    environment = {
        **os.environ,
        "TEST_BIN": binary.as_posix(),
        "RUNNER_TEMP": temporary.as_posix(),
        "GITHUB_ENV": github_env.as_posix(),
        "GITHUB_REPOSITORY": "meownm/ragflow",
        "GITHUB_RUN_ID": "123",
        "GITHUB_RUN_ATTEMPT": "2",
        "DOC_ENGINE": "infinity",
        "STAGING_IMAGE_REPOSITORY": "registry.example:5443/ragflow",
        "STAGING_IMAGE": "registry.example:5443/ragflow:a-123-2",
        "REPLAY_IMAGE": "",
        "RAGFLOW_IMAGE": "registry.example:5443/ragflow@sha256:" + "a" * 64,
        "NEXUS_STAGING_BUILD_USER": "fake-builder",
        "NEXUS_STAGING_BUILD_PASSWORD": "fake-registry-password",
        "NEXUS_STAGING_READ_USER": "fake-reader",
        "NEXUS_STAGING_READ_PASSWORD": "fake-registry-password",
        "TEST_EXPECTED_USER": "fake-builder",
        "DOCKER_CONFIG": persistent.as_posix(),
        "TEST_EXPECTED_REGISTRY": "registry.example:5443",
        "TEST_CHOWN_FAIL": "0",
        "NEXUS_RELEASE_READ_USER": "",
        "NEXUS_RELEASE_READ_PASSWORD": "",
        "RELEASE_IMAGE_REPOSITORY": "release.example:8443/docker-releases/ragflow",
        "CI_DOCKER_CONFIG": "",
        "CI_DOCKER_AUTH_SCOPE": "",
        "TEST_LOGIN_FAIL": "0",
        "TEST_DOCKER_LOG": (tmp_path / "docker-log").as_posix(),
        "TEST_SUDO_LOG": (tmp_path / "sudo-log").as_posix(),
    }
    return environment, temporary, github_env, persistent


def run(script, args, environment, cwd=None):
    command = 'if command -v cygpath >/dev/null; then TEST_BIN=$(cygpath -u "$TEST_BIN"); fi; export PATH="$TEST_BIN:$PATH"; exec bash "$@"'
    return subprocess.run([_bash(), "--noprofile", "--norc", "-c", command, "auth-test", script.as_posix(), *args], env=environment, cwd=cwd, text=True, capture_output=True, timeout=10, check=False)


def prepared(scenario, role="build"):
    environment, _, github_env, _ = scenario
    environment["TEST_EXPECTED_USER"] = "fake-reader" if role == "engine" else "fake-builder"
    result = run(HELPER, ["prepare", role], environment)
    assert result.returncode == 0, result.stderr
    assert result.stdout == result.stderr == ""
    state = dict(line.split("=", 1) for line in github_env.read_text(encoding="utf-8").splitlines())
    next_step = {**environment, **state}
    for name in ("NEXUS_STAGING_BUILD_USER", "NEXUS_STAGING_BUILD_PASSWORD", "NEXUS_STAGING_READ_USER", "NEXUS_STAGING_READ_PASSWORD"):
        next_step[name] = ""
    return next_step, _path(state["CI_DOCKER_CONFIG"])


@pytest.mark.parametrize("name", SCRIPTS)
def test_auth_consumer_shell_syntax(name):
    subprocess.run([_bash(), "-n", (ROOT / "deployment/runner" / name).as_posix()], check=True, capture_output=True)


@pytest.mark.parametrize("role", ["build", "engine"])
def test_auth_is_private_job_scoped_and_explicit_under_sudo(scenario, role):
    environment, config = prepared(scenario, role)
    assert config.is_dir()
    assert config.parent == scenario[1]
    assert environment["CI_DOCKER_CONFIG"] == environment["DOCKER_CONFIG"]
    assert environment["CI_DOCKER_AUTH_SCOPE"] == ("build" if role == "build" else "engine-infinity")
    assert (config / ".ragflow-owner").read_text().strip() == f"meownm/ragflow:123:2:{environment['CI_DOCKER_AUTH_SCOPE']}"
    assert (scenario[3] / "config.json").read_text() == "do-not-touch"
    commands = Path(environment["TEST_SUDO_LOG"]).read_text().splitlines()
    chown = commands.index("chown")
    assert len(commands[chown + 1].split(":")) == 2
    assert all(part.isdigit() for part in commands[chown + 1].split(":"))
    assert _path(commands[chown + 2]) == config / "config.json"
    assert (config / "config.json").read_text()
    if os.name != "nt":
        assert config.stat().st_mode & 0o777 == 0o700
        assert (config / "config.json").stat().st_mode & 0o777 == 0o600
    result = run(HELPER, ["cleanup"], environment)
    assert result.returncode == 0
    assert not config.exists()
    assert run(HELPER, ["cleanup"], environment).returncode == 0


def test_login_failure_suppresses_auth_and_removes_created_config(scenario):
    environment, temporary, _, _ = scenario
    environment["TEST_LOGIN_FAIL"] = "1"
    result = run(HELPER, ["prepare", "build"], environment)
    assert result.returncode != 0
    assert result.stdout == ""
    assert "Staging Docker login failed" in result.stderr
    assert "fake-registry-password" not in result.stderr
    assert list(temporary.iterdir()) == []


def test_startup_env_file_failure_removes_its_created_config(scenario):
    environment, temporary, _, _ = scenario
    environment["GITHUB_ENV"] = (temporary / "missing-parent" / "environment").as_posix()
    result = run(HELPER, ["prepare", "build"], environment)
    assert result.returncode != 0
    assert list(temporary.iterdir()) == []
    assert "fake-registry-password" not in result.stdout + result.stderr


def test_atomic_login_config_ownership_failure_blocks_and_cleans_auth(scenario):
    environment, temporary, _, _ = scenario
    environment["TEST_CHOWN_FAIL"] = "1"
    result = run(HELPER, ["prepare", "build"], environment)
    assert result.returncode != 0
    assert "config ownership failed" in result.stderr
    assert "fake-registry-password" not in result.stdout + result.stderr
    assert list(temporary.iterdir()) == []


@pytest.mark.parametrize(
    "updates",
    [
        {"NEXUS_STAGING_BUILD_PASSWORD": ""},
        {"STAGING_IMAGE": "foreign.example:5443/ragflow:tag"},
        {"STAGING_IMAGE_REPOSITORY": "https://registry.example:5443/ragflow"},
        {"GITHUB_RUN_ATTEMPT": "0"},
    ],
)
def test_invalid_auth_prerequisite_never_creates_config(scenario, updates):
    environment, temporary, _, _ = scenario
    result = run(HELPER, ["prepare", "build"], {**environment, **updates})
    assert result.returncode != 0
    assert list(temporary.iterdir()) == []
    assert "fake-registry-password" not in result.stdout + result.stderr


@pytest.mark.parametrize("field,value", [("GITHUB_RUN_ID", "124"), ("GITHUB_RUN_ATTEMPT", "3"), ("CI_DOCKER_AUTH_SCOPE", "engine-infinity")])
def test_foreign_identity_cannot_delete_another_job_config(scenario, field, value):
    environment, config = prepared(scenario)
    result = run(HELPER, ["cleanup"], {**environment, field: value})
    assert result.returncode != 0
    assert config.is_dir()


def test_cleanup_cannot_delete_persistent_or_unmarked_config(scenario):
    environment, config = prepared(scenario)
    result = run(HELPER, ["cleanup"], {**environment, "CI_DOCKER_CONFIG": scenario[3].as_posix()})
    assert result.returncode != 0
    assert (scenario[3] / "config.json").read_text() == "do-not-touch"
    (config / ".ragflow-owner").write_text("foreign-owner", encoding="utf-8")
    assert run(HELPER, ["cleanup"], environment).returncode != 0
    assert config.is_dir()


def test_cleanup_rejects_symlink_config_without_deleting_target(scenario):
    environment, config = prepared(scenario)
    link = config.parent / "alias-config"
    try:
        link.symlink_to(config, target_is_directory=True)
    except OSError:
        pytest.skip("creating symlinks requires host permission")
    assert run(HELPER, ["cleanup"], {**environment, "CI_DOCKER_CONFIG": link.as_posix()}).returncode != 0
    assert config.is_dir()


class LifecycleEngine:
    """Docker stand-in for the shell/auth contract; creation stays in the real adapter."""

    identity = "auth-test-engine"
    prefix = ["docker"]

    def __init__(self, **_):
        self.env = dict(os.environ)

    def owned(self, _):
        return []

    def inspect(self, kind, reference):
        assert kind == "image"
        return {"Id": reference}


def test_engine_compose_never_builds_pinned_images(scenario, tmp_path, monkeypatch):
    environment, config = prepared(scenario, "engine")
    env_file = tmp_path / "engine.env"
    env_file.write_text("SVR_HTTP_PORT=9380\nADMIN_SVR_HTTP_PORT=9381\nSVR_WEB_HTTP_PORT=80\n", encoding="utf-8")
    environment.update({"CI_ENV_FILE": env_file.as_posix(), "RUNTIME_IMAGE_OVERRIDE": "owned-override.json", "COMPOSE_PROJECT_NAME": "rf-owned"})
    # Keep generated output isolated while exercising the standard lifecycle
    # CLI with a synthetic Docker backend, rather than bypassing its ownership.
    entrypoint = tmp_path / "tools/quality/artifact_lifecycle.py"
    entrypoint.parent.mkdir(parents=True)
    entrypoint.write_text(
        f"import sys\nsys.path.insert(0, {str(ROOT)!r})\n"
        "from tools.quality import artifact_docker, artifact_lifecycle\n"
        "from test.unit_test.tools.quality.test_staging_docker_auth import LifecycleEngine\n"
        "artifact_docker.Engine = LifecycleEngine\n"
        "import subprocess\n"
        "original_run = subprocess.run\n"
        "def bash_run(arguments, **kwargs):\n"
        f"    return original_run([{_bash()!r}, '--noprofile', '--norc', '-c', 'exec \"$@\"', 'auth-fixture', *arguments], **kwargs)\n"
        "subprocess.run = bash_run\n"
        "raise SystemExit(artifact_lifecycle.main())\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("RAGFLOW_ARTIFACT_STATE", str(tmp_path / "lifecycle"))
    monkeypatch.delenv("RAGFLOW_ARTIFACT_RUN_ID", raising=False)
    with life.Run("shell-auth-contract") as owner:
        owner._engine = LifecycleEngine()
        owner.data["engine"] = owner._engine.identity
        owner.persist()
        environment.update({name: os.environ[name] for name in ("RAGFLOW_ARTIFACT_STATE", "RAGFLOW_ARTIFACT_RUN_ID", "RAGFLOW_ARTIFACT_OWNER_TOKEN")})
        result = run(ROOT / "deployment/runner/test-engine.sh", [], environment, cwd=tmp_path)
        owner.failed = bool(result.returncode)
    assert result.returncode == 42, result.stderr
    assert any(item["kind"] == "container" for item in owner.data["creation_intents"])
    log = Path(environment["TEST_DOCKER_LOG"]).read_text()
    assert "up\n-d\n--no-build\n" in log
    assert config.is_dir()


def test_engine_cleanup_removes_auth_when_stack_was_not_created(scenario, tmp_path):
    environment, config = prepared(scenario, "engine")
    environment.update({"COMPOSE_PROJECT_NAME": "", "CI_ENV_FILE": ""})
    result = run(ROOT / "deployment/runner/cleanup-engine.sh", [], environment, cwd=ROOT)
    assert result.returncode == 0, result.stderr
    assert not config.exists()


def previous_read_environment(environment):
    return {
        **environment,
        "NEXUS_RELEASE_READ_USER": "fake-release-reader",
        "NEXUS_RELEASE_READ_PASSWORD": "fake-registry-password",
        "TEST_EXPECTED_USER": "fake-release-reader",
        "TEST_EXPECTED_REGISTRY": "release.example:8443",
    }


def test_previous_release_read_adds_separate_auth_without_replacing_staging(scenario):
    import json

    environment, config = prepared(scenario)
    environment = previous_read_environment(environment)
    result = run(HELPER, ["previous-read"], environment)
    assert result.returncode == 0, result.stderr
    assert result.stdout == result.stderr == ""
    assert set(json.loads((config / "config.json").read_text())["auths"]) == {"registry.example:5443", "release.example:8443"}
    assert run(HELPER, ["cleanup"], environment).returncode == 0
    assert not config.exists()


def test_sourced_previous_read_unsets_credentials_before_the_probe(scenario, tmp_path):
    environment, _ = prepared(scenario)
    wrapper = tmp_path / "upgrade-step.sh"
    wrapper.write_text(
        f'''#!/usr/bin/env bash
source "{HELPER.as_posix()}"
authenticate_previous_release_read
[[ -z "${{NEXUS_RELEASE_READ_USER:-}}${{NEXUS_RELEASE_READ_PASSWORD:-}}" ]] || exit 97
''',
        encoding="utf-8",
    )
    result = run(wrapper, [], previous_read_environment(environment))
    assert result.returncode == 0, result.stderr
    assert result.stdout == result.stderr == ""


@pytest.mark.parametrize(
    "updates",
    [
        {"RELEASE_IMAGE_REPOSITORY": "registry.example:5443/docker-releases/ragflow"},
        {"RELEASE_IMAGE_REPOSITORY": "https://release.example:8443/docker-releases/ragflow"},
        {"RELEASE_IMAGE_REPOSITORY": "release.example:8443/docker-releases//ragflow"},
        {"NEXUS_RELEASE_READ_PASSWORD": ""},
        {"GITHUB_RUN_ATTEMPT": "3"},
        {"CI_DOCKER_AUTH_SCOPE": "engine-infinity"},
    ],
)
def test_previous_read_rejects_same_authority_bad_path_or_foreign_config(scenario, updates):
    environment, config = prepared(scenario)
    original = (config / "config.json").read_bytes()
    result = run(HELPER, ["previous-read"], {**previous_read_environment(environment), **updates})
    assert result.returncode != 0
    assert (config / "config.json").read_bytes() == original
    assert "fake-registry-password" not in result.stdout + result.stderr


def test_previous_read_auth_failure_is_generic_and_job_cleanup_removes_all_auth(scenario):
    environment, config = prepared(scenario)
    environment = {**previous_read_environment(environment), "TEST_LOGIN_FAIL": "1"}
    result = run(HELPER, ["previous-read"], environment)
    assert result.returncode != 0
    assert result.stdout == ""
    assert "Job registry login failed" in result.stderr
    assert "fake-registry-password" not in result.stderr
    assert run(HELPER, ["cleanup"], environment).returncode == 0
    assert not config.exists()


def test_previous_read_cannot_write_to_persistent_credential_helper(scenario):
    import json

    environment, config = prepared(scenario)
    data = json.loads((config / "config.json").read_text())
    data["credsStore"] = "persistent-store"
    (config / "config.json").write_text(json.dumps(data))
    before = Path(environment["TEST_SUDO_LOG"]).read_bytes()
    result = run(HELPER, ["previous-read"], previous_read_environment(environment))
    assert result.returncode != 0
    assert "Invalid job registry auth store" in result.stderr
    assert Path(environment["TEST_SUDO_LOG"]).read_bytes() == before


def test_previous_read_cannot_follow_symlink_auth_file(scenario):
    environment, config = prepared(scenario)
    target = scenario[3] / "config.json"
    (config / "config.json").unlink()
    try:
        (config / "config.json").symlink_to(target)
    except OSError:
        pytest.skip("creating symlinks requires host permission")
    result = run(HELPER, ["previous-read"], previous_read_environment(environment))
    assert result.returncode != 0
    assert target.read_text() == "do-not-touch"
