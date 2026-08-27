import json
import logging
from datetime import datetime, timezone


class JsonFormatter(logging.Formatter):
    def __init__(self, data_mode: str) -> None:
        super().__init__()
        self.data_mode = data_mode

    def _mask_value(self, value):
        if self.data_mode != "masked":
            return value
        if isinstance(value, str):
            return {"masked": True, "length": len(value)}
        if isinstance(value, dict):
            return {key: self._mask_value(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._mask_value(item) for item in value]
        return value

    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": getattr(record, "event", message),
            "request_id": getattr(record, "request_id", "-"),
            "message": self._mask_value(message),
        }
        extra_payload = getattr(record, "payload", None)
        if isinstance(extra_payload, dict):
            payload.update(self._mask_value(extra_payload))
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(log_level: str, data_mode: str) -> None:
    formatter = JsonFormatter(data_mode=data_mode)
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(log_level.upper())
