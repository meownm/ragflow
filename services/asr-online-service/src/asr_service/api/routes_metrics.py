from fastapi import APIRouter, Response

router = APIRouter(tags=["metrics"])


@router.get("/metrics")
def metrics() -> Response:
    body = "# HELP asr_service_up service status\n# TYPE asr_service_up gauge\nasr_service_up 1\n"
    return Response(content=body, media_type="text/plain; version=0.0.4")
