"""Bootstrap must remain opt-in even when launched by an administrator."""

import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[4]
SCRIPT = ROOT / "deployment/runner/bootstrap-release.sh"


def _bash() -> str:
    if os.name == "nt":
        # system32/bash.exe is a WSL launcher and does not resolve Windows paths.
        git_bash = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/bin/bash.exe"
        if git_bash.is_file():
            return str(git_bash)
    else:
        executable = shutil.which("bash")
        if executable:
            return executable
    pytest.skip("Bash is required to verify infrastructure bootstrap")


def test_release_bootstrap_shell_syntax():
    subprocess.run([_bash(), "-n", SCRIPT.as_posix()], check=True, capture_output=True)


def test_release_bootstrap_without_operator_confirmation_never_provisions():
    result = subprocess.run(
        [_bash(), SCRIPT.as_posix()],
        env={**os.environ, "RELEASE_VM_CONFIRMED": ""},
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 1
    assert result.stdout == ""
    assert "Run bootstrap-release.sh as the VM administrator." in result.stderr or "Set RELEASE_VM_CONFIRMED=yes" in result.stderr


@pytest.mark.parametrize(
    "listing,query_exit,expected",
    [
        ("User ragflow-release is not allowed to run sudo on guest.", 0, "DENIED"),
        ("User ragflow-release is not allowed to run sudo on guest.", 1, "DENIED"),
        ("User ragflow-release may run the following commands on guest:\n    (ALL) ALL", 0, "ALLOWED"),
        ("User ragflow-release may run the following commands on guest:\n    (root) /usr/bin/cat", 0, "ALLOWED"),
        ("sudo: unable to initialize policy plugin", 1, None),
        ("", 0, None),
    ],
)
def test_publisher_sudo_policy_uses_permissions_not_query_exit(listing, query_exit, expected):
    source = SCRIPT.read_text(encoding="utf-8")
    function = "_publisher_has_sudo() {" + source.split("_publisher_has_sudo() {", 1)[1].split("\n}", 1)[0] + "\n}"
    probe = (
        """
fail() { printf '%s\\n' "$*" >&2; exit 1; }
sudo() { printf '%s\\n' "$SUDO_LISTING"; return "$QUERY_EXIT"; }
"""
        + function
        + "\nif _publisher_has_sudo; then echo ALLOWED; else echo DENIED; fi\n"
    )
    result = subprocess.run(
        [_bash(), "-c", probe],
        env={**os.environ, "RUNNER_USER": "ragflow-release", "SUDO_LISTING": listing, "QUERY_EXIT": str(query_exit)},
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if expected is None:
        assert result.returncode == 1
        assert "Cannot determine publisher sudo policy" in result.stderr
    else:
        assert result.returncode == 0
        assert result.stdout.strip() == expected


@pytest.mark.parametrize(
    "confirmed,virt_exit,user,error",
    [
        ("", "0", "ragflow-test", "Set TEST_VM_CONFIRMED=yes"),
        ("yes", "1", "ragflow-test", "only inside its dedicated VM"),
        ("yes", "0", "apt", "dedicated test guest account"),
        ("yes", "0", "root", "dedicated test guest account"),
    ],
)
def test_test_runner_registration_refuses_unverified_host_or_shared_account(confirmed, virt_exit, user, error):
    installer = ROOT / "deployment/runner/install-actions-runner.sh"
    probe = 'systemd-detect-virt() { return "$VIRT_EXIT"; }; export -f systemd-detect-virt; source "$INSTALLER"'
    result = subprocess.run(
        [_bash(), "-c", probe],
        env={**os.environ, "TEST_VM_CONFIRMED": confirmed, "VIRT_EXIT": virt_exit, "RUNNER_USER": user, "INSTALLER": installer.as_posix()},
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 1
    assert result.stdout == ""
    assert error in result.stderr


@pytest.mark.parametrize(
    "confirmed,virt_exit,user,error",
    [
        ("", "0", "ragflow-test", "Set TEST_VM_CONFIRMED=yes"),
        ("yes", "1", "ragflow-test", "only inside their dedicated VM"),
        ("yes", "0", "apt", "dedicated test guest account"),
        ("yes", "0", "root", "dedicated test guest account"),
    ],
)
def test_test_bootstrap_refuses_host_or_retired_identity_before_provisioning(confirmed, virt_exit, user, error):
    bootstrap = ROOT / "deployment/runner/bootstrap-ubuntu.sh"
    probe = 'systemd-detect-virt() { return "$VIRT_EXIT"; }; export -f systemd-detect-virt; source "$BOOTSTRAP"'
    result = subprocess.run(
        [_bash(), "-c", probe],
        env={**os.environ, "TEST_VM_CONFIRMED": confirmed, "VIRT_EXIT": virt_exit, "RUNNER_USER": user, "BOOTSTRAP": bootstrap.as_posix()},
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 1
    assert result.stdout == ""
    assert error in result.stderr
