import gc
import os
from collections.abc import Iterator
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

    def stream_transcribe(self, audio_path: Path, language: str) -> Iterator[dict]:
        """Decode one native T-One chunk at a time while preserving model state."""

        if not self._loaded or self._pipeline is None:
            raise RuntimeError("engine is not loaded")

        import numpy as np
        from tone import read_audio

        audio = read_audio(audio_path)
        audio = np.pad(audio, (self._pipeline.PADDING, self._pipeline.PADDING))
        audio = np.pad(audio, (0, -len(audio) % self._pipeline.CHUNK_SIZE))
        chunks = np.split(audio, len(audio) // self._pipeline.CHUNK_SIZE)

        state = None
        segments: list[dict] = []
        transcript_parts: list[str] = []
        for index, audio_chunk in enumerate(chunks):
            phrases, state = self._pipeline.forward(audio_chunk, state, is_last=index == len(chunks) - 1)
            new_segments = [
                {
                    "start": phrase.start_time,
                    "end": phrase.end_time,
                    "text": phrase.text.strip(),
                }
                for phrase in phrases
                if getattr(phrase, "text", "").strip()
            ]
            if new_segments:
                segments.extend(new_segments)
                transcript_parts.extend(segment["text"] for segment in new_segments)
                yield {
                    "transcript": " ".join(transcript_parts),
                    "segments": list(segments),
                    "percent": round((index + 1) * 100 / len(chunks)),
                }

        if not segments:
            yield {"transcript": "", "segments": [], "percent": 100}

    def unload(self) -> None:
        self._pipeline = None
        self._loaded = False
        gc.collect()
