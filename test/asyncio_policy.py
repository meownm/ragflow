"""Explicit ownership of asyncio policies in tests that import RAG runtimes."""

import asyncio

import pytest

_STANDARD_GET_EVENT_LOOP = asyncio.DefaultEventLoopPolicy.get_event_loop


@pytest.fixture(scope="session")
def event_loop_policy():
    """Keep pytest-asyncio's previous-loop probe from creating an unowned loop."""
    previous = asyncio.get_event_loop_policy()
    # Runtime imports may have installed a policy loop during collection.
    try:
        collection_loop = previous.get_event_loop()
    except RuntimeError:
        collection_loop = None

    class TestEventLoopPolicy(asyncio.DefaultEventLoopPolicy):
        # Runtime imports can patch the base policy to create unowned loops.
        get_event_loop = _STANDARD_GET_EVENT_LOOP

    policy = TestEventLoopPolicy()
    policy.set_event_loop(None)
    asyncio.set_event_loop_policy(policy)
    try:
        yield policy
    finally:
        if collection_loop is not None and not collection_loop.is_running() and not collection_loop.is_closed():
            collection_loop.close()
            previous.set_event_loop(None)
        asyncio.set_event_loop_policy(previous)


@pytest.fixture(autouse=True)
def owned_policy_loop(event_loop_policy):
    """Own the policy loop reused by nest_asyncio's process-wide run wrapper.

    Importing Message patches asyncio.run during collection. Synchronous tests
    still need a current loop after pytest-asyncio or Runner has cleared it.
    Runner shuts down pending tasks, generators and executors after each test.
    """
    with asyncio.Runner() as runner:
        runner.get_loop()
        yield
