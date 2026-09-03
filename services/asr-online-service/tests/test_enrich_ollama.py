from asr_service.pipeline.enrich_ollama import enrich_text
from asr_service.settings import Settings


class _Response:
    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict[str, str]:
        return {"response": "enriched"}


def test_enrich_text_disables_ollama_thinking(monkeypatch):
    captured = {}

    def fake_post(url, *, json, timeout):
        captured.update(url=url, json=json, timeout=timeout)
        return _Response()

    monkeypatch.setattr("asr_service.pipeline.enrich_ollama.httpx.post", fake_post)
    settings = Settings(
        ASR_OLLAMA_BASE_URL="http://ollama.test",
        ASR_OLLAMA_MODEL="qwen3.8:latest",
    )

    assert enrich_text(settings, "source") == "enriched"
    assert captured["url"] == "http://ollama.test/api/generate"
    assert captured["json"]["think"] is False
