from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

from asr_service.models.model_manager import ModelManager
from asr_service.models.model_registry import default_registry


def _emit(event: str, **payload: object) -> None:
    line = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "payload": payload,
    }
    print(json.dumps(line, ensure_ascii=False), flush=True)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prefetch ASR model weights")
    parser.add_argument("--models", required=True, help="CSV model keys, e.g. t-one,gigaam-v3-e2e-rnnt")
    parser.add_argument("--timeout-sec", type=int, default=0, help="HuggingFace Hub download timeout in seconds (0 = no limit)")
    return parser


def _parse_models(csv_models: str) -> list[str]:
    return [item.strip() for item in csv_models.split(",") if item.strip()]


def run_prefetch(model_keys: list[str], timeout_sec: int = 0) -> int:
    if timeout_sec > 0:
        os.environ["HF_HUB_TIMEOUT"] = str(timeout_sec)

    registry = default_registry()
    manager = ModelManager()

    known = {item.key: item for item in registry.items}
    unknown = [key for key in model_keys if key not in known]

    _emit("prefetch_start", models=model_keys)

    if unknown:
        _emit("prefetch_error", reason="unknown_models", unknown_models=unknown)
        _emit("prefetch_done", ok=False, prefetched=[], failed=unknown)
        return 2

    failed: list[str] = []
    prefetched: list[str] = []

    for key in model_keys:
        descriptor = known[key]
        _emit("model_prefetch_start", model_key=key, engine_type=descriptor.engine_type)
        if not descriptor.available:
            failed.append(key)
            _emit("model_prefetch_error", model_key=key, error="engine_unavailable")
            continue

        engine = None
        try:
            engine = manager.acquire(descriptor)
            prefetched.append(key)
            _emit("model_prefetch_ok", model_key=key)
        except Exception as exc:  # noqa: BLE001
            failed.append(key)
            _emit("model_prefetch_error", model_key=key, error=str(exc))
        finally:
            if engine is not None:
                try:
                    manager.release(descriptor, engine)
                except Exception as exc:  # noqa: BLE001
                    _emit("model_prefetch_error", model_key=key, error=f"release_failed: {exc}")

    ok = len(failed) == 0
    _emit("prefetch_done", ok=ok, prefetched=prefetched, failed=failed)
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    model_keys = _parse_models(args.models)
    if not model_keys:
        _emit("prefetch_error", reason="empty_models")
        _emit("prefetch_done", ok=False, prefetched=[], failed=[])
        return 2
    return run_prefetch(model_keys, timeout_sec=args.timeout_sec)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
