import logging

import httpx

from asr_service.settings import Settings

control_logger = logging.getLogger("asr.control")
data_logger = logging.getLogger("asr.data")


def enrich_text(settings: Settings, prompt: str) -> str:
    stop_tokens = [item.strip() for item in settings.ollama_stop.split(",") if item.strip()]
    payload = {
        "model": settings.ollama_model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": settings.ollama_temperature,
            "top_p": settings.ollama_top_p,
            "num_ctx": settings.ollama_num_ctx,
            "num_predict": settings.ollama_num_predict,
            "stop": stop_tokens,
        },
    }
    control_logger.info(
        "ollama_call_start",
        extra={"event": "ollama_call_start", "payload": {"base_url": settings.ollama_base_url, "model": settings.ollama_model}},
    )
    data_logger.info(
        "ollama_call_start",
        extra={"event": "ollama_call_start", "payload": {"prompt": prompt}},
    )
    response = httpx.post(
        f"{settings.ollama_base_url}/api/generate",
        json=payload,
        timeout=settings.ollama_timeout_seconds,
    )
    response.raise_for_status()
    enriched = response.json().get("response", "")
    data_logger.info("ollama_call_done", extra={"event": "ollama_call_done", "payload": {"response": enriched}})
    control_logger.info("ollama_call_done", extra={"event": "ollama_call_done"})
    return enriched
