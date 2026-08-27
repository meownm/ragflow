import sys
import types

import pytest

from asr_service.models.engines.gigaam_engine import GigaAmEngine


class _ModelDict:
    def transcribe(self, path: str) -> dict:
        return {"text": "hello", "segments": [{"start": 0.0, "end": 0.1, "text": "hello"}]}


class _ModelStr:
    def transcribe(self, path: str) -> str:
        return "hello world"


def test_gigaam_load_calls_only_gigaam(monkeypatch):
    captured = {}

    def _load_model(name: str):
        captured["name"] = name
        return _ModelDict()

    fake_gigaam = types.SimpleNamespace(load_model=_load_model)
    monkeypatch.setitem(sys.modules, "gigaam", fake_gigaam)

    class _TrapAutoModel:
        @staticmethod
        def from_pretrained(*args, **kwargs):
            raise AssertionError("AutoModel.from_pretrained must not be called")

    monkeypatch.setitem(sys.modules, "transformers", types.SimpleNamespace(AutoModel=_TrapAutoModel))

    engine = GigaAmEngine(model_id="gigaam-v3", revision="ctc")
    engine.load()

    assert captured["name"] == "v3_ctc"


def test_gigaam_transcribe_schema_stable(monkeypatch):
    fake_gigaam = types.SimpleNamespace(load_model=lambda _: _ModelDict())
    monkeypatch.setitem(sys.modules, "gigaam", fake_gigaam)
    engine = GigaAmEngine(model_id="gigaam-v3")
    engine.load()

    out = engine.transcribe("sample.wav", language="ru")
    assert out["transcript"] == "hello"
    assert isinstance(out["segments"], list)

    engine._model = _ModelStr()
    out2 = engine.transcribe("sample.wav", language="ru")
    assert out2 == {"transcript": "hello world", "segments": []}


def test_gigaam_transcribe_errors(monkeypatch):
    fake_gigaam = types.SimpleNamespace(load_model=lambda _: types.SimpleNamespace())
    monkeypatch.setitem(sys.modules, "gigaam", fake_gigaam)
    engine = GigaAmEngine(model_id="gigaam-v3")

    with pytest.raises(RuntimeError, match="engine is not loaded"):
        engine.transcribe("sample.wav", language="ru")
    engine.load()
    with pytest.raises(RuntimeError, match="Q-ASR-ENGINE-NOT-AVAILABLE"):
        engine.transcribe("sample.wav", language="ru")
