"""Instrument only explicitly opted-in disposable pytest processes, not application code."""

import os
import subprocess

import pytest

from tools.quality.artifact_lifecycle import Run, docker_args, note


OPT_INS = ("RAGFLOW_ARTIFACT_MANAGED_QA", "RAGFLOW_TEST_DISPOSABLE", "RAGFLOW_MINIO_BACKUP_TEST", "RAGFLOW_COORDINATED_BACKUP_TEST", "RAGFLOW_SANDBOX_IMAGE_TEST")


def pytest_sessionstart(session):
    if not any(os.environ.get(name) == "1" for name in OPT_INS):
        return
    run = Run("pytest-disposable", days=0)
    run.__enter__()
    original = subprocess.Popen
    children = []

    class OwnedPopen(original):
        def __init__(self, args, *arguments, **options):
            super().__init__(docker_args(args, cwd=options.get("cwd")), *arguments, **options)
            children.append(self)

    session._artifact_lifecycle = (run, original, children)
    subprocess.Popen = OwnedPopen


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session, exitstatus):
    state = getattr(session, "_artifact_lifecycle", None)
    if state is None:
        return
    run, original, children = state
    try:
        cleanup = {"run_id": run.data["run_id"], "errors": [], "blocked": []}
        if any(process.poll() is None for process in children):
            raise RuntimeError("QA still has active child processes")
        if run.data["engine"]:
            result = run.retire_owned()
            cleanup = result
            if result["errors"] or result["blocked"]:
                raise RuntimeError("Disposable QA cleanup incomplete; lifecycle recovery retained")
        if directory := os.environ.get("PW_ARTIFACTS_DIR"):
            from pathlib import Path

            note(Path(directory))
        run.failed = bool(exitstatus)
        if not run.failed:
            run.close_consumption("pytest-disposable", exit_code=int(exitstatus), cleanup=cleanup)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        run.preserve = True
        run.data["cleanup_failure"] = type(error).__name__
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
        if reporter := session.config.pluginmanager.getplugin("terminalreporter"):
            reporter.write_line("Artifact cleanup incomplete; see lifecycle receipt (" + type(error).__name__ + ")", red=True)
    finally:
        subprocess.Popen = original
        run.__exit__(None, None, None)
