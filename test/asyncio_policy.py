"""Explicit ownership of asyncio policies in tests that import RAG runtimes."""

import asyncio

import pytest

_STANDARD_GET_EVENT_LOOP = asyncio.DefaultEventLoopPolicy.get_event_loop


@pytest.fixture(scope="session")
def event_loop_policy():
    """Keep pytest-asyncio's previous-loop probe from creating an unowned loop."""
    previous = asyncio.get_event_loop_policy()

    class TestEventLoopPolicy(asyncio.DefaultEventLoopPolicy):
        # Runtime imports can patch the base policy to create unowned loops.
        get_event_loop = _STANDARD_GET_EVENT_LOOP

    policy = TestEventLoopPolicy()
    policy.set_event_loop(None)
    asyncio.set_event_loop_policy(policy)
    try:
        yield policy
    finally:
        asyncio.set_event_loop_policy(previous)
