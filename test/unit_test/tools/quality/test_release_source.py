"""Source qualification fails before registry access for an altered checkout."""

import os
from pathlib import Path
import subprocess

import pytest

from test.unit_test.tools.quality.test_release_bootstrap import _bash

ROOT = Path(__file__).resolve().parents[4]


@pytest.mark.parametrize("state", ["clean", "tracked", "staged", "untracked", "wrong-sha"])
def test_build_rejects_source_changes_before_registry_auth(tmp_path, state):
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=tmp_path, text=True).strip()

    git("init", "--quiet")
    (tmp_path / "source.py").write_text("original\n")
    git("add", "source.py")
    git("-c", "user.name=Fixture", "-c", "user.email=fixture@invalid", "commit", "--quiet", "-m", "fixture")
    sha = git("rev-parse", "HEAD")
    if state in ("tracked", "staged"):
        (tmp_path / "source.py").write_text("modified\n")
        if state == "staged":
            git("add", "source.py")
    if state == "untracked":
        (tmp_path / "extra.py").write_text("unexpected source\n")
    if state == "wrong-sha":
        sha = "f" * 40
    auth = tmp_path / "deployment/runner/staging-docker-auth.sh"
    auth.parent.mkdir(parents=True)
    auth.write_text("echo AUTH_REACHED; exit 0\n", newline="\n")
    source = (ROOT / "deployment/runner/build-qualified-image.sh").read_text()
    preflight = source.split("\nversion=", 1)[0] + "\n"
    probe = tmp_path / "preflight.sh"
    probe.write_text(preflight, newline="\n")
    with (tmp_path / ".git/info/exclude").open("a") as exclude:
        exclude.write("/preflight.sh\n/deployment/runner/staging-docker-auth.sh\n")
    result = subprocess.run([_bash(), probe.as_posix()], cwd=tmp_path, env={**os.environ, "SOURCE_SHA": sha}, capture_output=True, text=True, timeout=15, check=False)
    if state == "clean":
        assert result.returncode == 0
        assert result.stdout.strip() == "AUTH_REACHED"
    else:
        assert result.returncode != 0
        assert "AUTH_REACHED" not in result.stdout
