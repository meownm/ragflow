"""Synchronous entry points for Business Documents async worker operations."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any, TypeVar

_ResultT = TypeVar("_ResultT")


def run_in_worker_loop(operation: Coroutine[Any, Any, _ResultT]) -> _ResultT:
    """Run one worker operation and always close its private event loop.

    RAGFlow loads ``nest_asyncio`` in some process profiles. Its patched
    ``asyncio.run`` reuses a policy loop and does not close a loop it creates in
    a worker thread. ``Runner`` retains the standard shutdown semantics even in
    those profiles, including cleanup of async generators and the default
    executor.
    """

    with asyncio.Runner() as runner:
        return runner.run(operation)
