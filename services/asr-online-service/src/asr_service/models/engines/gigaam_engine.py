ENGINE_TYPE = "gigam"


class GigaAmEngine:
    engine_type = ENGINE_TYPE

    def __init__(self, model_id: str, revision: str | None = None) -> None:
        self.model_id = model_id
        self.revision = revision
        self._loaded = False
        self._model = None

    @staticmethod
    def is_available() -> bool:
        try:
            import gigaam  # noqa: F401
        except ImportError:
            return False
        return True

    def _normalized_model_name(self) -> str:
        base = self.model_id.lower()
        if "gigaam-v3" in base and self.revision:
            return f"v3_{self.revision}"
        if "gigaam-v2" in base and self.revision:
            return f"v2_{self.revision}"
        if self.revision:
            return f"{self.model_id}:{self.revision}"
        return self.model_id

    def load(self) -> None:
        if not self.is_available():
            raise RuntimeError("Q-ASR-ENGINE-NOT-AVAILABLE")

        import gigaam

        self._model = gigaam.load_model(self._normalized_model_name())
        self._loaded = True

    def transcribe(self, audio_path, language: str) -> dict:
        if not self._loaded or self._model is None:
            raise RuntimeError("engine is not loaded")

        if not hasattr(self._model, "transcribe"):
            raise RuntimeError("Q-ASR-ENGINE-NOT-AVAILABLE")

        output = self._model.transcribe(str(audio_path))
        if isinstance(output, dict):
            transcript = str(output.get("text") or output.get("transcript") or "").strip()
            segments = output.get("segments") or []
            return {"transcript": transcript, "segments": segments}

        transcript = str(output).strip()
        return {"transcript": transcript, "segments": []}

    def unload(self) -> None:
        self._model = None
        self._loaded = False
