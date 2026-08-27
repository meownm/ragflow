from pathlib import Path

ENGINE_TYPE = "whisper"


class WhisperEngine:
    engine_type = ENGINE_TYPE

    def __init__(self, model_id: str) -> None:
        self.model_id = model_id
        self._loaded = False
        self._pipeline = None
        self._torch = None

    @staticmethod
    def is_available() -> bool:
        try:
            import transformers  # noqa: F401
            import torch  # noqa: F401
        except ImportError:
            return False
        return True

    def load(self) -> None:
        if not self.is_available():
            raise RuntimeError("Q-ASR-ENGINE-NOT-AVAILABLE")

        from transformers import pipeline
        import torch

        self._torch = torch
        device = 0 if torch.cuda.is_available() else -1
        self._pipeline = pipeline("automatic-speech-recognition", model=self.model_id, device=device)
        self._loaded = True

    def transcribe(self, audio_path: Path, language: str) -> dict:
        if not self._loaded or self._pipeline is None:
            raise RuntimeError("engine is not loaded")

        output = self._pipeline(str(audio_path), generate_kwargs={"language": language, "task": "transcribe"}, return_timestamps=True)
        segments = []
        chunks = output.get("chunks") or []
        for chunk in chunks:
            timestamp = chunk.get("timestamp") or [None, None]
            segments.append({"start": timestamp[0], "end": timestamp[1], "text": chunk.get("text", "")})
        return {"transcript": output.get("text", "").strip(), "segments": segments}

    def unload(self) -> None:
        self._pipeline = None
        self._loaded = False
        if self._torch is not None and self._torch.cuda.is_available():
            self._torch.cuda.empty_cache()
