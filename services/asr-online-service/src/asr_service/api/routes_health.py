from fastapi import APIRouter, Request

from asr_service.settings import settings

router = APIRouter(tags=["health"])


@router.get("/health/live")
def live() -> dict:
    return {"status": "ok"}


@router.get("/health/ready")
def ready(request: Request) -> dict:
    checks = getattr(request.app.state, "health_checks", None)
    if checks is None:
        checks = {
            "ffmpeg": {"ok": False, "details": {"resolved_path": None, "checked_at": None, "error": "not initialized"}},
            "sox": {"ok": not settings.enable_sox_normalize, "details": {"resolved_path": None, "checked_at": None, "error": "not initialized"}},
        }
    is_ready = checks["ffmpeg"]["ok"] and (checks["sox"]["ok"] if settings.enable_sox_normalize else True)
    return {"status": "ready" if is_ready else "not_ready", "checks": checks}
