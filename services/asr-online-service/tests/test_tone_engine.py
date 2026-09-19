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


class FakeStreamingPipeline:
    CHUNK_SIZE = 4
    PADDING = 0

    def forward(self, audio_chunk, state, *, is_last=False):
        index = 0 if state is None else state
        phrase = SimpleNamespace(text=f" фраза {index + 1} ", start_time=float(index), end_time=float(index + 1))
        return [phrase], index + 1


def test_tone_engine_streams_cumulative_native_decoder_results(monkeypatch) -> None:
    fake_numpy = SimpleNamespace(
        pad=lambda audio, padding: list(audio) + [0] * padding[1],
        split=lambda audio, count: [audio[index * len(audio) // count : (index + 1) * len(audio) // count] for index in range(count)],
    )
    monkeypatch.setitem(sys.modules, "numpy", fake_numpy)
    monkeypatch.setitem(sys.modules, "tone", SimpleNamespace(read_audio=lambda _path: list(range(8))))
    engine = ToneEngine("t-tech/T-one")
    engine._loaded = True
    engine._pipeline = FakeStreamingPipeline()

    events = list(engine.stream_transcribe(Path("audio.wav"), "ru"))

    assert [event["transcript"] for event in events] == ["фраза 1", "фраза 1 фраза 2"]
    assert events[-1]["percent"] == 100
