"""Sandbox ownership, partial-start cleanup and shutdown of allocated children."""

import asyncio
import importlib
import json
import socket
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def pool(monkeypatch):
    prefixes = ("core", "models", "utils", "util")
    saved = {k: v for k, v in sys.modules.items() if k.split(".")[0] in prefixes}
    for name in saved:
        del sys.modules[name]
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[4] / "agent/sandbox/executor_manager"))
    monkeypatch.setenv("SANDBOX_MANAGER_CONTAINER", "manager")
    try:
        yield importlib.import_module("core.container")
    finally:
        for name in list(sys.modules):
            if name.split(".")[0] in prefixes:
                del sys.modules[name]
        sys.modules.update(saved)


class Docker:
    def __init__(self):
        self.children = {}
        self.next_id = 0
        self.calls = []
        self.fail_copy = False
        self.fail_remove = False
        self.created = asyncio.Event()
        self.pause_create = False
        self.late_create = False
        self.creation_started = asyncio.Event()
        self.resume_create = asyncio.Event()

    async def command(self, *args, **_):
        self.calls.append(args)
        if args[:2] == ("docker", "run"):
            name = args[args.index("--name") + 1]
            labels = dict(args[i + 1].split("=", 1) for i, value in enumerate(args) if value == "--label")
            if self.pause_create and self.late_create:
                self.creation_started.set()
                await self.resume_create.wait()
            self.next_id += 1
            self.children[name] = {"Id": f"{self.next_id:064x}", "Config": {"Labels": labels}, "State": {"Running": True}}
            self.created.set()
            if self.pause_create and not self.late_create:
                self.creation_started.set()
                await self.resume_create.wait()
            return 0, self.children[name]["Id"] + "\n", ""
        if args[:2] == ("docker", "inspect"):
            if args[2] == "manager":
                return 0, json.dumps([{"Id": "1" * 64, "Config": {"Hostname": socket.gethostname()}, "State": {"StartedAt": "2026-10-29T00:00:00Z"}}]), ""
            child = self.children.get(args[2])
            return (0, json.dumps([child]), "") if child else (1, "", "No such container")
        if args[:2] == ("docker", "exec"):
            return (1, "", "dependency copy failed") if self.fail_copy else (0, "", "")
        if args[:3] == ("docker", "rm", "-f"):
            if self.fail_remove:
                return 1, "", "Docker unavailable"
            for name, child in list(self.children.items()):
                if child["Id"] == args[3]:
                    del self.children[name]
                    return 0, "", ""
            raise AssertionError("Removal used a name or foreign identity")
        raise AssertionError(args)


@pytest.mark.asyncio
async def test_wrong_manager_container_cannot_create_children(pool, monkeypatch):
    async def other(*_, **__):
        return 0, json.dumps([{"Id": "1" * 64, "Config": {"Hostname": "another-manager"}, "State": {"StartedAt": "2026-10-29T00:00:00Z"}}]), ""

    monkeypatch.setattr(pool, "async_run_command", other)
    with pytest.raises(RuntimeError, match="does not match"):
        await pool.create_container("child", pool.SupportLanguage.PYTHON)
    assert not pool._CHILDREN


@pytest.mark.asyncio
async def test_shutdown_removes_allocated_children_and_labels_all_generations(pool, monkeypatch):
    docker = Docker()
    monkeypatch.setattr(pool, "async_run_command", docker.command)
    assert await pool.init_containers(2) == (4, 4)
    allocated = await pool.allocate_container_blocking(pool.SupportLanguage.PYTHON)
    assert allocated and allocated in docker.children
    for child in docker.children.values():
        labels = child["Config"]["Labels"]
        assert labels["org.ragflow.sandbox.manager"] == "1" * 64
        assert labels["org.ragflow.sandbox.epoch"] == "2026-10-29T00:00:00Z"
    assert all("--rm" in args for args in docker.calls if args[:2] == ("docker", "run"))
    await pool.teardown_containers()
    assert not docker.children and not pool._CHILDREN


@pytest.mark.asyncio
async def test_partial_node_preparation_is_retired(pool, monkeypatch):
    docker = Docker()
    docker.fail_copy = True
    monkeypatch.setattr(pool, "async_run_command", docker.command)
    assert not await pool.create_container("own-node", pool.SupportLanguage.NODEJS)
    assert not docker.children and not pool._CHILDREN


@pytest.mark.asyncio
@pytest.mark.parametrize("late_create", [False, True])
async def test_cancelled_create_waits_for_inflight_cli_then_cleans_child(pool, monkeypatch, late_create):
    docker = Docker()
    docker.pause_create = True
    docker.late_create = late_create
    monkeypatch.setattr(pool, "async_run_command", docker.command)
    task = asyncio.create_task(pool.init_containers(1))
    await docker.creation_started.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    docker.resume_create.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not docker.children and not pool._CHILDREN


@pytest.mark.asyncio
async def test_failed_teardown_retains_ownership_for_retry(pool, monkeypatch):
    docker = Docker()
    monkeypatch.setattr(pool, "async_run_command", docker.command)
    assert await pool.create_container("private", pool.SupportLanguage.PYTHON, network_disabled=True)
    assert pool._CHILDREN["private"]["labels"]["org.ragflow.sandbox.kind"] == "private"
    docker.fail_remove = True
    with pytest.raises(RuntimeError, match="incomplete"):
        await pool.teardown_containers()
    assert "private" in pool._CHILDREN
    docker.fail_remove = False
    await pool.teardown_containers()
    assert not pool._CHILDREN


@pytest.mark.asyncio
async def test_replaced_child_cannot_be_allocated_or_removed(pool, monkeypatch):
    docker = Docker()
    monkeypatch.setattr(pool, "async_run_command", docker.command)
    assert await pool.create_container("child", pool.SupportLanguage.PYTHON)
    docker.children["child"]["Id"] = "f" * 64
    assert not await pool.container_is_running("child")
    with pytest.raises(RuntimeError, match="incomplete"):
        await pool.teardown_containers()
    assert docker.children and not any(args[:2] == ("docker", "rm") for args in docker.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("startup_fails", [True, False])
async def test_lifespan_finalizes_when_startup_or_body_raises(pool, monkeypatch, startup_fails):
    # FastAPI is an image dependency; this unit exercises only its lifecycle hook.
    monkeypatch.setitem(sys.modules, "fastapi", SimpleNamespace(FastAPI=object))
    config = importlib.import_module("core.config")
    docker = Docker()
    monkeypatch.setattr(pool, "async_run_command", docker.command)

    async def startup(_):
        assert await pool.create_container("partial", pool.SupportLanguage.PYTHON)
        if startup_fails:
            raise RuntimeError("Startup interrupted")
        return 1, 1

    monkeypatch.setattr(config, "init_containers", startup)
    with pytest.raises(RuntimeError, match="interrupted"):
        async with config._lifespan(None):
            assert not startup_fails
            raise RuntimeError("Body interrupted")
    assert not docker.children


@pytest.mark.asyncio
async def test_subprocess_cancellation_kills_and_drains_client(pool, monkeypatch):
    common = importlib.import_module("utils.common")
    started = asyncio.Event()

    class Process:
        returncode = None
        killed = False

        async def communicate(self):
            if not self.killed:
                started.set()
                await asyncio.Future()
            return b"", b""

        def kill(self):
            self.killed = True
            self.returncode = -9

    process = Process()

    async def launch(*_, **__):
        return process

    monkeypatch.setattr(common.asyncio, "create_subprocess_exec", launch)
    task = asyncio.create_task(common.async_run_command("client"))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert process.killed
