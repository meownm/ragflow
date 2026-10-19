"""The test policy keeps standard loop ownership after runtime imports."""

import asyncio

import pytest


def test_test_policy_does_not_create_an_implicit_loop(event_loop_policy, monkeypatch):
    def patched_base(_policy):
        raise AssertionError("Runtime policy replacement must not own test loops")

    monkeypatch.setattr(asyncio.DefaultEventLoopPolicy, "get_event_loop", patched_base)
    policy = type(event_loop_policy)()
    policy.set_event_loop(None)
    with pytest.raises(RuntimeError):
        policy.get_event_loop()
    loop = policy.new_event_loop()
    try:
        policy.set_event_loop(loop)
        assert policy.get_event_loop() is loop
    finally:
        loop.close()
        policy.set_event_loop(None)
