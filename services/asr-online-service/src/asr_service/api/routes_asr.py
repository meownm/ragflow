from enum import Enum
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel

from asr_service.jobs.job_models import CreateJobRequest, CreateJobResponse, Job, JobStatus
from asr_service.jobs.job_queue import JobQueue
from asr_service.jobs.job_store import JobStore
from asr_service.models.model_registry import RegistryView
from asr_service.settings import settings

router = APIRouter(prefix="/v1/asr", tags=["asr"])


class EngineType(str, Enum):
    whisper = "whisper"
    t_one = "t_one"
    gigam = "gigam"


class ModelDescriptor(BaseModel):
    key: str
    engine_type: EngineType
    engine_model_id: str
    supported_languages: list[str]
    available: bool
    revision: str | None = None


class ModelsResponse(BaseModel):
    models: list[ModelDescriptor]


class UploadResponse(BaseModel):
    source_uri: str


class LanguagesResponse(BaseModel):
    languages: list[str]


class JobResultResponse(BaseModel):
    job_id: str
    result: dict
    artifacts: dict[str, str]


def _source_uri_validation_error(source_uri: str, reason: str) -> HTTPException:
    return HTTPException(
        status_code=422,
        detail={
            "error_code": "Q-ASR-SOURCE-URI-INVALID",
            "message": "Invalid source_uri",
            "details": {"source_uri": source_uri, "reason": reason},
        },
    )


def validate_source_uri(source_uri: str) -> str:
    if source_uri.startswith("memory://"):
        name = source_uri[len("memory://") :]
        if not name or Path(name).name != name:
            raise _source_uri_validation_error(source_uri, "memory_basename_required")
        if any(part in name for part in ("..", "/", "\\", ":")):
            raise _source_uri_validation_error(source_uri, "memory_name_contains_forbidden_chars")
        return source_uri

    upload_root = Path(settings.upload_dir).resolve()
    src = Path(source_uri).resolve()

    if not src.exists() or not src.is_file():
        raise _source_uri_validation_error(source_uri, "file_not_found")

    if src != upload_root and upload_root not in src.parents:
        raise _source_uri_validation_error(source_uri, "outside_upload_dir")

    return str(src)


def get_job_store(request: Request) -> JobStore:
    return request.app.state.job_store


def get_job_queue(request: Request) -> JobQueue:
    return request.app.state.job_queue


def get_registry(request: Request) -> RegistryView:
    return request.app.state.registry


@router.post("/uploads", response_model=UploadResponse, status_code=status.HTTP_201_CREATED)
def upload_audio(file: UploadFile = File(...)) -> UploadResponse:
    upload_dir = Path(settings.upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    safe_name = Path(file.filename or "").name
    if not safe_name:
        raise HTTPException(
            status_code=422,
            detail={
                "error_code": "Q-ASR-UPLOAD-INVALID-FILENAME",
                "message": "Upload filename is required",
                "details": {},
            },
        )
    target = upload_dir / safe_name
    with target.open("wb") as out:
        while True:
            chunk = file.file.read(1024 * 1024)
            if not chunk:
                break
            out.write(chunk)
    return UploadResponse(source_uri=str(target))


@router.get("/languages", response_model=LanguagesResponse)
def get_languages(registry: RegistryView = Depends(get_registry)) -> LanguagesResponse:
    langs = sorted({lang for model in registry.items for lang in model.supported_languages})
    return LanguagesResponse(languages=langs)


@router.get("/models", response_model=ModelsResponse)
def get_models(registry: RegistryView = Depends(get_registry)) -> ModelsResponse:
    return ModelsResponse(models=[ModelDescriptor(**item.__dict__) for item in registry.items])


@router.post("/jobs", response_model=CreateJobResponse, status_code=status.HTTP_201_CREATED)
def create_job(
    request: CreateJobRequest,
    store: JobStore = Depends(get_job_store),
    queue: JobQueue = Depends(get_job_queue),
    registry: RegistryView = Depends(get_registry),
) -> CreateJobResponse:
    descriptor = registry.by_key(request.model_key)
    if descriptor is None:
        raise HTTPException(status_code=404, detail={"error_code": "Q-ASR-MODEL-NOT-FOUND", "message": "Model not found", "details": {"model_key": request.model_key}})
    if request.language not in descriptor.supported_languages:
        raise HTTPException(status_code=422, detail={"error_code": "Q-ASR-LANGUAGE-NOT-SUPPORTED", "message": "Language not supported", "details": {"language": request.language}})
    if not descriptor.available:
        raise HTTPException(status_code=409, detail={"error_code": "Q-ASR-ENGINE-NOT-AVAILABLE", "message": "Engine is not available", "details": {"model_key": request.model_key}})

    validated_source_uri = validate_source_uri(request.source_uri)
    job_request = request.model_copy(update={"source_uri": validated_source_uri})
    job = store.create(job_request)
    queue.put(job.id)
    return CreateJobResponse(job_id=job.id, status=job.status)


@router.get("/jobs/{job_id}", response_model=Job)
def get_job(job_id: str, store: JobStore = Depends(get_job_store)) -> Job:
    job = store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail={"error_code": "Q-ASR-JOB-NOT-FOUND", "message": "Job not found", "details": {"job_id": job_id}})
    return job

@router.get("/jobs/{job_id}/result", response_model=JobResultResponse)
def get_result(job_id: str, store: JobStore = Depends(get_job_store)) -> JobResultResponse:
    job = store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail={"error_code": "Q-ASR-JOB-NOT-FOUND", "message": "Job not found", "details": {"job_id": job_id}})
    if job.status != JobStatus.done:
        raise HTTPException(status_code=409, detail={"error_code": "Q-ASR-JOB-NOT-DONE", "message": "Job is not completed", "details": {"status": job.status}})
    return JobResultResponse(job_id=job.id, result=job.result or {}, artifacts=job.artifacts)


@router.get("/jobs/{job_id}/artifacts/{kind}")
def download_artifact(job_id: str, kind: str, store: JobStore = Depends(get_job_store)) -> FileResponse:
    job = store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail={"error_code": "Q-ASR-JOB-NOT-FOUND", "message": "Job not found", "details": {"job_id": job_id}})
    path = job.artifacts.get(kind)
    if not path:
        raise HTTPException(status_code=404, detail={"error_code": "Q-ASR-ARTIFACT-NOT-FOUND", "message": "Artifact not found", "details": {"kind": kind}})
    return FileResponse(path=path, filename=Path(path).name)


@router.delete("/jobs/{job_id}", response_model=Job)
def cancel_job(job_id: str, store: JobStore = Depends(get_job_store)) -> Job:
    job = store.cancel(job_id)
    if not job:
        raise HTTPException(status_code=404, detail={"error_code": "Q-ASR-JOB-NOT-FOUND", "message": "Job not found", "details": {"job_id": job_id}})
    return job
