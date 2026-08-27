from enum import Enum

from pydantic import BaseModel, Field


class JobStatus(str, Enum):
    queued = "queued"
    processing = "processing"
    done = "done"
    error = "error"
    canceled = "canceled"
    expired = "expired"


class EnrichOptions(BaseModel):
    enabled: bool = False


class OutputOptions(BaseModel):
    artifact_formats: list[str] = Field(default_factory=lambda: ["result_json"])
    include_segments: bool = True


class JobOptions(BaseModel):
    enrich: EnrichOptions = Field(default_factory=EnrichOptions)
    output: OutputOptions = Field(default_factory=OutputOptions)


class CreateJobRequest(BaseModel):
    model_key: str
    language: str
    source_uri: str = Field(default="memory://sample.wav")
    options: JobOptions = Field(default_factory=JobOptions)


class Job(BaseModel):
    id: str
    model_key: str
    language: str
    source_uri: str
    status: JobStatus = JobStatus.queued
    stage: str = "queued"
    percent: int = 0
    cancel_requested: bool = False
    options: JobOptions = Field(default_factory=JobOptions)
    result: dict | None = None
    artifacts: dict[str, str] = Field(default_factory=dict)
    error: dict | None = None


class CreateJobResponse(BaseModel):
    job_id: str = Field(..., examples=["job_123"])
    status: JobStatus
