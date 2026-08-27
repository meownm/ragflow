from __future__ import annotations

from dataclasses import dataclass

from asr_service.tools import prefetch_models


@dataclass
class Descriptor:
    key: str
    engine_type: str = "dummy"
    available: bool = True


@dataclass
class Registry:
    items: list[Descriptor]


class DummyEngine:
    pass


class DummyManager:
    def __init__(self, fail_key: str | None = None) -> None:
        self.fail_key = fail_key
        self.acquired: list[str] = []
        self.released: list[str] = []

    def acquire(self, descriptor: Descriptor) -> DummyEngine:
        self.acquired.append(descriptor.key)
        if descriptor.key == self.fail_key:
            raise RuntimeError("boom")
        return DummyEngine()

    def release(self, descriptor: Descriptor, engine: DummyEngine) -> None:
        self.released.append(descriptor.key)


def test_run_prefetch_success(monkeypatch, capsys) -> None:
    manager = DummyManager()
    monkeypatch.setattr(prefetch_models, "default_registry", lambda: Registry(items=[Descriptor("t-one")]))
    monkeypatch.setattr(prefetch_models, "ModelManager", lambda: manager)

    exit_code = prefetch_models.run_prefetch(["t-one"])
    out = capsys.readouterr().out

    assert exit_code == 0
    assert '"event": "model_prefetch_ok"' in out
    assert '"event": "prefetch_done"' in out
    assert manager.acquired == ["t-one"]
    assert manager.released == ["t-one"]


def test_run_prefetch_unknown_model(monkeypatch, capsys) -> None:
    monkeypatch.setattr(prefetch_models, "default_registry", lambda: Registry(items=[Descriptor("t-one")]))

    exit_code = prefetch_models.run_prefetch(["missing-model"])
    out = capsys.readouterr().out

    assert exit_code == 2
    assert '"event": "prefetch_error"' in out
    assert '"unknown_models": ["missing-model"]' in out


def test_run_prefetch_unavailable_and_failure(monkeypatch, capsys) -> None:
    manager = DummyManager(fail_key="gigaam-v3-e2e-rnnt")
    registry = Registry(items=[Descriptor("t-one", available=False), Descriptor("gigaam-v3-e2e-rnnt")])
    monkeypatch.setattr(prefetch_models, "default_registry", lambda: registry)
    monkeypatch.setattr(prefetch_models, "ModelManager", lambda: manager)

    exit_code = prefetch_models.run_prefetch(["t-one", "gigaam-v3-e2e-rnnt"])
    out = capsys.readouterr().out

    assert exit_code == 1
    assert '"event": "model_prefetch_error"' in out
    assert '"model_key": "t-one"' in out
    assert '"error": "engine_unavailable"' in out
    assert '"error": "boom"' in out


def test_main_rejects_empty_models(capsys) -> None:
    exit_code = prefetch_models.main(["--models", "   "])
    out = capsys.readouterr().out

    assert exit_code == 2
    assert '"reason": "empty_models"' in out
