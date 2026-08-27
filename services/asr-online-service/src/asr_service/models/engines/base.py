from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class AsrEngine(Protocol):
    engine_type: str

    def load(self) -> None: ...

    def transcribe(self, audio_path: Path, language: str) -> dict: ...

    def unload(self) -> None: ...


@dataclass
class EngineDescriptor:
    key: str
    engine_type: str
    engine_model_id: str
    supported_languages: list[str]
    available: bool = True
    revision: str | None = None
