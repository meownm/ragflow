import sys
from pathlib import Path
from types import SimpleNamespace

from asr_service.models.engines.tone_engine import ToneEngine


class FakePipeline:
    local_model_dir: str | None = None

    @classmethod
    def from_local(cls, model_dir: str):
        cls.local_model_dir = model_dir
        return cls()

    @classmethod
    def from_hugging_face(cls):
        return cls()

    def forward_offline(self, audio):
        assert audio == "decoded-audio"
        return [
            SimpleNamespace(text=" первая фраза ", start_time=0.0, end_time=1.2),
            SimpleNamespace(text="вторая фраза", start_time=1.3, end_time=2.4),
        ]


def test_tone_engine_uses_bundled_model_and_returns_clean_phrases(monkeypatch) -> None:
    monkeypatch.setenv("LOAD_FROM_FOLDER", "/models")
    monkeypatch.setitem(
        sys.modules,
        "tone",
        SimpleNamespace(
            StreamingCTCPipeline=FakePipeline,
            read_audio=lambda audio_path: "decoded-audio",
        ),
    )

    engine = ToneEngine("t-tech/T-one")
    engine.load()
    result = engine.transcribe(Path("audio.wav"), "ru")

    assert FakePipeline.local_model_dir == "/models"
    assert result == {
        "transcript": "первая фраза вторая фраза",
        "segments": [
            {"start": 0.0, "end": 1.2, "text": "первая фраза"},
            {"start": 1.3, "end": 2.4, "text": "вторая фраза"},
        ],
    }
