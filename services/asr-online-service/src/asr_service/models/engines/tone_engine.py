import gc
import os
from pathlib import Path

ENGINE_TYPE = "t_one"


class ToneEngine:
    engine_type = ENGINE_TYPE

    def __init__(self, model_id: str) -> None:
        self.model_id = model_id
        self._loaded = False
        self._pipeline = None

    @staticmethod
    def is_available() -> bool:
        try:
            from tone import StreamingCTCPipeline  # noqa: F401
        except ImportError:
            return False
        return True

    def load(self) -> None:
        if not self.is_available():
            raise RuntimeError("Q-ASR-ENGINE-NOT-AVAILABLE")

        from tone import StreamingCTCPipeline

        model_dir = os.getenv("TONE_MODEL_DIR") or os.getenv("LOAD_FROM_FOLDER")
        if model_dir:
            self._pipeline = StreamingCTCPipeline.from_local(model_dir)
        else:
            self._pipeline = StreamingCTCPipeline.from_hugging_face()
        self._loaded = True

    def transcribe(self, audio_path: Path, language: str) -> dict:
        if not self._loaded or self._pipeline is None:
            raise RuntimeError("engine is not loaded")

        from tone import read_audio

        output = self._pipeline.forward_offline(read_audio(audio_path))

        if isinstance(output, dict):
            transcript = str(output.get("text") or output.get("transcript") or "").strip()
            segments = output.get("segments") or []
            return {"transcript": transcript, "segments": segments}

        if isinstance(output, (list, tuple)):
            segments = [
                {
                    "start": phrase.start_time,
                    "end": phrase.end_time,
                    "text": phrase.text.strip(),
                }
                for phrase in output
                if getattr(phrase, "text", "").strip()
            ]
            transcript = " ".join(segment["text"] for segment in segments)
            return {"transcript": transcript, "segments": segments}

        return {"transcript": str(output).strip(), "segments": []}

    def unload(self) -> None:
        self._pipeline = None
        self._loaded = False
        gc.collect()
