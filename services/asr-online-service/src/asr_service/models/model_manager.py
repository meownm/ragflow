import logging

from asr_service.models.engines.base import AsrEngine, EngineDescriptor
from asr_service.models.engines.gigaam_engine import GigaAmEngine
from asr_service.models.engines.tone_engine import ToneEngine
from asr_service.models.engines.whisper_engine import WhisperEngine

control_logger = logging.getLogger("asr.control")


class ModelManager:
    def _build_engine(self, descriptor: EngineDescriptor) -> AsrEngine:
        if descriptor.engine_type == "whisper":
            return WhisperEngine(model_id=descriptor.engine_model_id)
        if descriptor.engine_type == "t_one":
            return ToneEngine(model_id=descriptor.engine_model_id)
        if descriptor.engine_type == "gigam":
            return GigaAmEngine(model_id=descriptor.engine_model_id, revision=descriptor.revision)
        raise ValueError(f"Unsupported engine type {descriptor.engine_type}")

    def acquire(self, descriptor: EngineDescriptor) -> AsrEngine:
        control_logger.info(
            "model_load_start",
            extra={"event": "model_load_start", "payload": {"model_key": descriptor.key, "engine_type": descriptor.engine_type}},
        )
        engine = self._build_engine(descriptor)
        engine.load()
        control_logger.info(
            "model_load_done",
            extra={"event": "model_load_done", "payload": {"model_key": descriptor.key, "engine_type": descriptor.engine_type}},
        )
        return engine

    def release(self, descriptor: EngineDescriptor, engine: AsrEngine) -> None:
        control_logger.info(
            "model_unload_start",
            extra={"event": "model_unload_start", "payload": {"model_key": descriptor.key, "engine_type": descriptor.engine_type}},
        )
        engine.unload()
        control_logger.info(
            "model_unload_done",
            extra={"event": "model_unload_done", "payload": {"model_key": descriptor.key, "engine_type": descriptor.engine_type}},
        )
