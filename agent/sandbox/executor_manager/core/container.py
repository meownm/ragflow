#
#  Copyright 2025 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
import asyncio
import json
import os
import re
import socket
import uuid
from queue import Empty, Queue

from models.enums import SupportLanguage
from util import env_setting_enabled, is_valid_memory_limit
from utils.common import async_run_command

from core.logger import logger

_CONTAINER_QUEUES: dict[SupportLanguage, Queue] = {}
_CONTAINER_LOCK: asyncio.Lock = asyncio.Lock()
_CONTAINER_EXECUTION_SEMAPHORES: dict[SupportLanguage, asyncio.Semaphore] = {}
_CHILDREN: dict[str, dict] = {}
_POOL_ID = uuid.uuid4().hex
_OWNER: tuple[str, str] | None = None
_OWNER_LOCK = asyncio.Lock()


async def _owner() -> tuple[str, str]:
    global _OWNER
    async with _OWNER_LOCK:
        if _OWNER is None:
            manager = os.getenv("SANDBOX_MANAGER_CONTAINER") or socket.gethostname()
            code, output, _ = await async_run_command("docker", "inspect", manager, timeout=10)
            if code:
                raise RuntimeError("Cannot identify sandbox manager container")
            info = json.loads(output)[0]
            if not re.fullmatch(r"[0-9a-f]{64}", info["Id"]) or not info["State"].get("StartedAt"):
                raise RuntimeError("Invalid sandbox manager generation")
            if info.get("Config", {}).get("Hostname") != socket.gethostname():
                raise RuntimeError("Sandbox manager identity does not match this container")
            _OWNER = info["Id"], info["State"]["StartedAt"]
        return _OWNER


async def remove_container(name: str):
    """Remove only an exact child generation created by this manager process."""
    entry = _CHILDREN.get(name)
    if entry is None:
        raise RuntimeError("Sandbox child has no creation ownership")
    code, output, error = await async_run_command("docker", "inspect", name, timeout=10)
    if code:
        if "no such" not in error.lower():
            raise RuntimeError("Cannot verify sandbox child before removal")
    else:
        info = json.loads(output)[0]
        labels = info["Config"].get("Labels") or {}
        if any(labels.get(k) != v for k, v in entry["labels"].items()) or (entry.get("id") and info["Id"] != entry["id"]):
            raise RuntimeError("Sandbox child generation or ownership changed")
        code, _, error = await async_run_command("docker", "rm", "-f", info["Id"], timeout=15)
        if code and "no such" not in error.lower():
            raise RuntimeError("Sandbox child removal failed")
    if _CHILDREN.get(name) is entry:
        _CHILDREN.pop(name)


async def init_containers(size: int) -> tuple[int, int]:
    global _CONTAINER_QUEUES
    await teardown_containers()
    _CONTAINER_QUEUES = {SupportLanguage.PYTHON: Queue(), SupportLanguage.NODEJS: Queue()}

    async with _CONTAINER_LOCK:
        while not _CONTAINER_QUEUES[SupportLanguage.PYTHON].empty():
            _CONTAINER_QUEUES[SupportLanguage.PYTHON].get_nowait()
        while not _CONTAINER_QUEUES[SupportLanguage.NODEJS].empty():
            _CONTAINER_QUEUES[SupportLanguage.NODEJS].get_nowait()

    for language in SupportLanguage:
        _CONTAINER_EXECUTION_SEMAPHORES[language] = asyncio.Semaphore(size)

    create_tasks = []
    owner, _ = await _owner()
    prefix = f"{os.getenv('SANDBOX_CONTAINER_PREFIX', 'sandbox')}_{owner[:12]}_{_POOL_ID[:12]}"
    for i in range(size):
        name = f"{prefix}_python_{i}"
        logger.info(f"🛠️ Creating Python container {i + 1}/{size}")
        create_tasks.append(_prepare_container(name, SupportLanguage.PYTHON))

        name = f"{prefix}_nodejs_{i}"
        logger.info(f"🛠️ Creating Node.js container {i + 1}/{size}")
        create_tasks.append(_prepare_container(name, SupportLanguage.NODEJS))

    tasks = [asyncio.create_task(task) for task in create_tasks]
    try:
        results = await asyncio.gather(*tasks, return_exceptions=True)
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await teardown_containers()
        raise
    success_count = sum(1 for r in results if r is True)
    total_task_count = len(create_tasks)
    return success_count, total_task_count


async def teardown_containers():
    async with _CONTAINER_LOCK:
        for queue in _CONTAINER_QUEUES.values():
            while not queue.empty():
                queue.get_nowait()
        results = await asyncio.gather(*(remove_container(name) for name in list(_CHILDREN)), return_exceptions=True)
        if any(isinstance(result, BaseException) for result in results):
            raise RuntimeError("Sandbox teardown incomplete; owned child generations retained for retry")


async def _prepare_container(name: str, language: SupportLanguage) -> bool:
    """Prepare a single container"""
    if await create_container(name, language):
        _CONTAINER_QUEUES[language].put(name)
        return True
    return False


async def create_container(name: str, language: SupportLanguage, *, network_disabled: bool = False) -> bool:
    """Asynchronously create a container"""
    owner, epoch = await _owner()
    if name in _CHILDREN:
        raise RuntimeError("Sandbox child name is already owned")
    labels = {
        "org.ragflow.artifact.project": "ragflow",
        "org.ragflow.artifact.class": "disposable",
        "org.ragflow.artifact.object": uuid.uuid4().hex,
        "org.ragflow.sandbox.manager": owner,
        "org.ragflow.sandbox.epoch": epoch,
        "org.ragflow.sandbox.pool": _POOL_ID,
        "org.ragflow.sandbox.kind": "private" if network_disabled else "pool",
        "org.ragflow.sandbox.cleanup": "owner-lifetime",
    }
    _CHILDREN[name] = {"labels": labels}
    create_args = [
        "docker",
        "run",
        "--rm",
        "-d",
        *[part for key, value in labels.items() for part in ("--label", f"{key}={value}")],
        f"--runtime={os.getenv('SANDBOX_PRIVATE_RUNTIME', 'runsc') if network_disabled else 'runsc'}",
        "--name",
        name,
        "--read-only",
        "--tmpfs",
        "/workspace:rw,exec,size=100M,uid=65534,gid=65534",
        "--tmpfs",
        "/tmp:rw,exec,size=50M",
        "--user",
        "nobody",
        "--workdir",
        "/workspace",
    ]
    if network_disabled:
        create_args.extend(["--network", "none"])
        create_args.extend(["--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--pids-limit", "64"])
    if os.getenv("SANDBOX_MAX_MEMORY"):
        memory_limit = os.getenv("SANDBOX_MAX_MEMORY") or "256m"
        if is_valid_memory_limit(memory_limit):
            logger.info(f"SANDBOX_MAX_MEMORY: {os.getenv('SANDBOX_MAX_MEMORY')}")
        else:
            logger.info("Invalid SANDBOX_MAX_MEMORY, using default value: 256m")
            memory_limit = "256m"
        create_args.extend(["--memory", memory_limit])
    else:
        logger.info("Set default SANDBOX_MAX_MEMORY: 256m")
        create_args.extend(["--memory", "256m"])

    if env_setting_enabled("SANDBOX_ENABLE_SECCOMP", "false"):
        logger.info(f"SANDBOX_ENABLE_SECCOMP: {os.getenv('SANDBOX_ENABLE_SECCOMP')}")
        create_args.extend(["--security-opt", "seccomp=/app/seccomp-profile-default.json"])

    if language == SupportLanguage.PYTHON:
        create_args.append(os.getenv("SANDBOX_BASE_PYTHON_IMAGE", "sandbox-base-python:latest"))
    elif language == SupportLanguage.NODEJS:
        create_args.append(os.getenv("SANDBOX_BASE_NODEJS_IMAGE", "sandbox-base-nodejs:latest"))

    logger.info(f"Sandbox config:\n\t {create_args}")

    ready = False
    try:
        creation = asyncio.create_task(async_run_command(*create_args, timeout=10))
        try:
            return_code, output, stderr = await asyncio.shield(creation)
        except asyncio.CancelledError:
            # Docker may finish creating the child after its client is cancelled.
            await asyncio.gather(creation, return_exceptions=True)
            raise
        if return_code != 0:
            logger.error(f"❌ Container creation failed {name}: {stderr}")
            return False
        identifier = output.strip()
        if not re.fullmatch(r"[0-9a-f]{64}", identifier):
            raise RuntimeError("Invalid sandbox child identity")
        _CHILDREN[name]["id"] = identifier

        if language == SupportLanguage.NODEJS:
            copy_cmd = ["docker", "exec", name, "bash", "-c", "cp -a /app/node_modules /workspace/"]
            return_code, _, stderr = await async_run_command(*copy_cmd, timeout=10)
            if return_code != 0:
                logger.error(f"❌ Failed to prepare dependencies for {name}: {stderr}")
                return False

        ready = await container_is_running(name)
        return ready
    except Exception as e:
        logger.error(f"❌ Container creation exception {name}: {e!s}")
        return False
    finally:
        if not ready:
            await asyncio.shield(remove_container(name))


async def recreate_container(name: str, language: SupportLanguage) -> bool:
    """Asynchronously recreate a container"""
    logger.info(f"🛠️ Recreating container: {name}")
    try:
        await remove_container(name)

        return await create_container(name, language)
    except Exception as e:
        logger.error(f"❌ Container {name} recreation failed: {e!s}")
        return False


async def release_container(name: str, language: SupportLanguage):
    """Asynchronously release a container"""
    async with _CONTAINER_LOCK:
        if await container_is_running(name):
            _CONTAINER_QUEUES[language].put(name)
            logger.info(f"🟢 Released container: {name} (remaining available: {_CONTAINER_QUEUES[language].qsize()})")
        else:
            logger.warning(f"⚠️ Container {name} has crashed, attempting to recreate...")
            if await recreate_container(name, language):
                _CONTAINER_QUEUES[language].put(name)
                logger.info(f"✅ Container {name} successfully recreated and returned to queue")


async def allocate_container_blocking(language: SupportLanguage, timeout=10) -> str:
    """Asynchronously allocate an available container"""
    start_time = asyncio.get_running_loop().time()
    while asyncio.get_running_loop().time() - start_time < timeout:
        try:
            name = _CONTAINER_QUEUES[language].get_nowait()
            async with _CONTAINER_LOCK:
                if not await container_is_running(name) and not await recreate_container(name, language):
                    continue

                return name
        except Empty:
            await asyncio.sleep(0.1)

    return ""


async def container_is_running(name: str) -> bool:
    """Asynchronously check the container status"""
    try:
        return_code, stdout, _ = await async_run_command("docker", "inspect", name, timeout=2)
        if return_code:
            return False
        info = json.loads(stdout)[0]
        entry = _CHILDREN.get(name)
        labels = info["Config"].get("Labels") or {}
        return bool(entry and info["Id"] == entry.get("id") and all(labels.get(k) == v for k, v in entry["labels"].items()) and info["State"].get("Running"))
    except Exception:
        return False
