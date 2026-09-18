import io
import importlib.util
from contextlib import nullcontext
from pathlib import Path


def _load_sequence2txt_module():
    path = Path(__file__).resolve().parents[4] / "rag" / "llm" / "sequence2txt_model.py"
    spec = importlib.util.spec_from_file_location("sequence2txt_model_under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _StreamingResponse:
    headers = {"Content-Type": "text/event-stream; charset=utf-8"}

    def raise_for_status(self):
        return None

    def iter_lines(self, chunk_size=None, decode_unicode=False):
        assert chunk_size == 1
        assert decode_unicode is True
        yield 'data: {"event":"progress","percent":10}'
        yield 'data: {"event":"delta","text":"Привет","transcript":"Привет"}'
        yield 'data: {"event":"final","text":"Привет","transcript":"Привет"}'


def test_new_api_stream_transcription_forwards_sse_events(monkeypatch, tmp_path):
    module = _load_sequence2txt_module()
    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"RIFF")
    captured = {}

    def _post(url, **kwargs):
        captured.update(url=url, **kwargs)
        return nullcontext(_StreamingResponse())

    monkeypatch.setattr(module.requests, "post", _post)
    monkeypatch.setattr(module, "OpenAI", lambda **_kwargs: object())

    model = module.NewAPISeq2txt("secret", model_name="t-one___local", base_url="http://t-one-asr:9011/v1/")
    events = list(model.stream_transcription(str(audio_path)))

    assert captured["url"] == "http://t-one-asr:9011/v1/audio/transcriptions"
    assert captured["data"]["stream"] == "true"
    assert captured["timeout"] == (10, 120.0)
    assert events[-1] == {"event": "final", "text": "Привет", "transcript": "Привет"}


def test_new_api_sync_contract_consumes_stream_without_total_deadline(monkeypatch, tmp_path):
    module = _load_sequence2txt_module()
    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"RIFF")
    monkeypatch.setattr(module, "OpenAI", lambda **_kwargs: object())
    model = module.NewAPISeq2txt("", model_name="t-one", base_url="http://t-one-asr:9011/v1")
    monkeypatch.setattr(
        model,
        "stream_transcription",
        lambda _path: iter(
            [
                {"event": "heartbeat"},
                {"event": "delta", "text": "Привет", "transcript": "Привет"},
                {"event": "final", "text": "Привет мир", "transcript": "Привет мир"},
            ]
        ),
    )

    text, tokens = model.transcription(str(audio_path))

    assert text == "Привет мир"
    assert tokens > 0


class _JsonResponse:
    headers = {"Content-Type": "application/json"}

    def raise_for_status(self):
        return None

    def json(self):
        return {"text": "fallback"}


def test_new_api_stream_transcription_falls_back_to_json(monkeypatch, tmp_path):
    module = _load_sequence2txt_module()
    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(io.BytesIO(b"RIFF").getvalue())
    monkeypatch.setattr(module.requests, "post", lambda *_args, **_kwargs: nullcontext(_JsonResponse()))
    monkeypatch.setattr(module, "OpenAI", lambda **_kwargs: object())

    model = module.NewAPISeq2txt("", model_name="t-one", base_url="http://t-one-asr:9011/v1")

    assert list(model.stream_transcription(str(audio_path))) == [{"event": "final", "text": "fallback", "transcript": "fallback"}]


class _UnsupportedStreamResponse:
    status_code = 422
    headers = {"Content-Type": "application/json"}

    def raise_for_status(self):
        import requests

        raise requests.HTTPError(response=self)


def test_new_api_stream_transcription_falls_back_when_provider_rejects_stream(monkeypatch, tmp_path):
    module = _load_sequence2txt_module()
    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"RIFF")
    monkeypatch.setattr(module.requests, "post", lambda *_args, **_kwargs: nullcontext(_UnsupportedStreamResponse()))
    monkeypatch.setattr(module, "OpenAI", lambda **_kwargs: object())

    model = module.NewAPISeq2txt("", model_name="other-asr", base_url="http://provider/v1")
    monkeypatch.setattr(module.GPTSeq2txt, "transcription", lambda _self, _path: ("sync fallback", 2))

    assert list(model.stream_transcription(str(audio_path))) == [
        {"event": "final", "text": "sync fallback", "transcript": "sync fallback"}
    ]
