"""Verify the negative controls of the T3 Docker execution boundary."""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
from pathlib import Path

VERSION = "0.1.0"
FORBIDDEN_CONFIG_MARKERS = (
    "credential",
    "extraheader",
    "include",
    "askpass",
    "proxy",
    "insteadof",
    "sslkey",
    "cookiefile",
)
SENSITIVE_PATHS = (
    "/github",
    "/home/runner/.gitconfig",
    "/home/runner/work/_temp/_runner_file_commands",
)


def _operation_denied(operation) -> bool:
    try:
        operation()
    except OSError:
        return True
    return False


def _network_denied() -> bool:
    try:
        with socket.create_connection(("1.1.1.1", 443), timeout=1):
            return False
    except OSError:
        return True


def _forbidden_environment_absent() -> bool:
    blocked_names = {"NODE_EXTRA_CA_CERTS", "NODE_OPTIONS", "NODE_PATH", "PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP", "PYTEST_ADDOPTS", "PYTEST_PLUGINS"}
    blocked_prefixes = ("ACTIONS_", "ARCHITECTURE_", "GITHUB_")
    secret_markers = ("CREDENTIAL", "PASSWORD", "SECRET", "TOKEN")
    for raw_name in os.environ:
        name = raw_name.upper()
        if name in blocked_names or name.startswith(blocked_prefixes) or "EVIDENCE" in name or any(marker in name for marker in secret_markers):
            return False
    return True


def _git_config_sanitized(workspace: Path) -> bool:
    if (workspace / ".git/objects/info/alternates").exists():
        return False
    environment = {
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "HOME": "/tmp",
        "LANG": "C.UTF-8",
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
    }
    git = ["git", "-c", f"safe.directory={workspace}", "-C", str(workspace)]
    config = subprocess.run(
        [*git, "config", "--local", "--name-only", "--null", "--list"],
        env=environment,
        capture_output=True,
        check=False,
    )
    remotes = subprocess.run(
        [*git, "remote"],
        env=environment,
        capture_output=True,
        check=False,
    )
    if config.returncode != 0 or remotes.returncode != 0 or remotes.stdout.strip():
        return False
    keys = [item.decode("utf-8", errors="replace").lower() for item in config.stdout.split(b"\0") if item]
    return not any(marker in key for key in keys for marker in FORBIDDEN_CONFIG_MARKERS)


def _process_status() -> dict[str, str]:
    return {key: value.strip() for line in Path("/proc/self/status").read_text(encoding="utf-8").splitlines() if ":" in line for key, value in [line.split(":", 1)]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--sentinel", required=True)
    parser.add_argument("--expected-uid", type=int, required=True)
    parser.add_argument("--expected-gid", type=int, required=True)
    args = parser.parse_args(argv)

    workspace = args.workspace.resolve()
    evidence = args.evidence.resolve()
    sentinel = evidence / args.sentinel
    status = _process_status()
    observed = {
        "candidate_write_denied": _operation_denied(lambda: (workspace / ".architecture-sandbox-write-probe").write_text("forbidden", encoding="utf-8")),
        "evidence_read_denied": _operation_denied(lambda: sentinel.read_bytes()),
        "evidence_write_denied": _operation_denied(lambda: (evidence / ".architecture-sandbox-evidence-probe").write_text("forbidden", encoding="utf-8")),
        "network_denied": _network_denied(),
        "forbidden_environment_absent": _forbidden_environment_absent(),
        "sensitive_paths_absent": not any(Path(path).exists() for path in SENSITIVE_PATHS),
        "git_config_sanitized": _git_config_sanitized(workspace),
        "unprivileged_identity": os.geteuid() == args.expected_uid and os.getegid() == args.expected_gid and not os.getgroups(),
        "capabilities_absent": status.get("CapEff") == "0000000000000000",
        "no_new_privileges": status.get("NoNewPrivs") == "1",
    }
    result = {
        "schema_version": 1,
        "tool": {"name": "probe_architecture_sandbox", "version": VERSION},
        "status": "PASS" if all(observed.values()) else "FAIL",
        "checks": observed,
    }
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
