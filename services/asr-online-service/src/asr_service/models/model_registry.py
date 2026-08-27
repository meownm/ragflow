from dataclasses import dataclass

from .engines.base import EngineDescriptor
from .engines.gigaam_engine import ENGINE_TYPE as GIGAAM, GigaAmEngine
from .engines.tone_engine import ENGINE_TYPE as TONE, ToneEngine
from .engines.whisper_engine import ENGINE_TYPE as WHISPER, WhisperEngine


@dataclass
class RegistryView:
    items: list[EngineDescriptor]
    _index: dict[str, EngineDescriptor] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self._index = {item.key: item for item in self.items}

    def by_key(self, key: str) -> EngineDescriptor | None:
        return self._index.get(key)


ENGINE_AVAILABILITY = {
    WHISPER: WhisperEngine.is_available,
    TONE: ToneEngine.is_available,
    GIGAAM: GigaAmEngine.is_available,
}


def default_registry() -> RegistryView:
    items = [
        EngineDescriptor(
            key="whisper-large-v3",
            engine_type=WHISPER,
            engine_model_id="openai/whisper-large-v3",
            supported_languages=["ru", "en"],
            available=ENGINE_AVAILABILITY[WHISPER](),
        ),
        EngineDescriptor(
            key="t-one",
            engine_type=TONE,
            engine_model_id="t-tech/T-one",
            supported_languages=["ru"],
            available=ENGINE_AVAILABILITY[TONE](),
        ),
        EngineDescriptor(
            key="gigaam-v3-e2e-rnnt",
            engine_type=GIGAAM,
            engine_model_id="ai-sage/GigaAM-v3",
            revision="e2e_rnnt",
            supported_languages=["ru"],
            available=ENGINE_AVAILABILITY[GIGAAM](),
        ),
    ]
    return RegistryView(items=items)
