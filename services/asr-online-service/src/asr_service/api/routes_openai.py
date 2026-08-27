import time
import uuid
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from asr_service.api.routes_asr import get_job_queue, get_job_store, get_registry
from asr_service.jobs.job_models import CreateJobRequest, JobStatus
from asr_service.jobs.job_queue import JobQueue
from asr_service.jobs.job_store import JobStore
from asr_service.models.model_registry import RegistryView
from asr_service.settings import settings

router = APIRouter(prefix="/v1", tags=["openai-compatible"])


class OpenAIModel(BaseModel):
    id: str
    object: str = "model"
    owned_by: str = "t-tech"


class OpenAIModelsResponse(BaseModel):
    object: str = "list"
    data: list[OpenAIModel]


class TranscriptionResponse(BaseModel):
    text: str


def _get_registry(request: Request) -> RegistryView:
    return get_registry(request)


def _model_key(model: str) -> str:
    aliases = {
        "t_one": "t-one",
        "t-tech/t-one": "t-one",
    }
    return aliases.get(model.lower(), model)


def _save_upload(file: UploadFile) -> Path:
    upload_dir = Path(settings.upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(file.filename or "audio.wav").suffix[:16]
    target = upload_dir / f"openai-{uuid.uuid4().hex}{suffix}"
    with target.open("wb") as output:
        while chunk := file.file.read(1024 * 1024):
            output.write(chunk)
    return target


@router.get("/models", response_model=OpenAIModelsResponse)
def list_models(registry: RegistryView = Depends(_get_registry)) -> OpenAIModelsResponse:
    models = [OpenAIModel(id=item.key) for item in registry.items if item.available]
    return OpenAIModelsResponse(data=models)


@router.post("/audio/transcriptions", response_model=TranscriptionResponse)
def create_transcription(
    file: UploadFile = File(...),
    model: str = Form(default="t-one"),
    language: str | None = Form(default=None),
    response_format: Literal["json", "text"] = Form(default="json"),
    store: JobStore = Depends(get_job_store),
    queue: JobQueue = Depends(get_job_queue),
    registry: RegistryView = Depends(_get_registry),
):
    model_key = _model_key(model)
    descriptor = registry.by_key(model_key)
    if descriptor is None:
        raise HTTPException(status_code=404, detail={"error": {"code": "model_not_found", "message": f"Model '{model}' was not found."}})
    if not descriptor.available:
        raise HTTPException(status_code=409, detail={"error": {"code": "model_not_available", "message": f"Model '{model}' is not available."}})

    selected_language = language or descriptor.supported_languages[0]
    if selected_language not in descriptor.supported_languages:
        raise HTTPException(
            status_code=422,
            detail={"error": {"code": "language_not_supported", "message": f"Language '{selected_language}' is not supported by '{model}'."}},
        )

    upload_path = _save_upload(file)
    job = store.create(
        CreateJobRequest(
            model_key=model_key,
            language=selected_language,
            source_uri=str(upload_path),
            options={"output": {"include_segments": False}},
        )
    )
    queue.put(job.id)

    deadline = time.monotonic() + settings.openai_timeout_seconds
    current = job
    try:
        while time.monotonic() < deadline:
            current = store.get(job.id) or current
            if current.status == JobStatus.done:
                text = str((current.result or {}).get("transcript", "")).strip()
                if response_format == "text":
                    return PlainTextResponse(text)
                return TranscriptionResponse(text=text)
            if current.status in {JobStatus.error, JobStatus.canceled, JobStatus.expired}:
                error = current.error or {}
                raise HTTPException(
                    status_code=500,
                    detail={
                        "error": {
                            "code": error.get("error_code", "transcription_failed"),
                            "message": error.get("message", f"Transcription ended with status '{current.status.value}'."),
                        }
                    },
                )
            time.sleep(0.05)

        current = store.cancel(job.id) or current
        raise HTTPException(
            status_code=504,
            detail={"error": {"code": "transcription_timeout", "message": "Transcription timed out."}},
        )
    finally:
        if current.status in {JobStatus.done, JobStatus.error, JobStatus.canceled, JobStatus.expired}:
            upload_path.unlink(missing_ok=True)
