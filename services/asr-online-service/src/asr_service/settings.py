from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    service_host: str = Field(default="0.0.0.0", alias="ASR_SERVICE_HOST")
    service_port: int = Field(default=9011, alias="ASR_SERVICE_PORT")
    log_level: str = Field(default="INFO", alias="ASR_LOG_LEVEL")
    log_data_mode: Literal["plain", "masked"] = Field(default="plain", alias="LOG_DATA_MODE")
    max_concurrent_jobs: int = Field(default=2, alias="ASR_MAX_CONCURRENT_JOBS")
    job_ttl_seconds: int = Field(default=3600, alias="ASR_JOB_TTL_SECONDS")
    artifacts_dir: str = Field(default="artifacts", alias="ASR_ARTIFACTS_DIR")
    upload_dir: str = Field(default="uploads", alias="ASR_UPLOAD_DIR")
    openai_timeout_seconds: float = Field(default=300.0, gt=0, alias="ASR_OPENAI_TIMEOUT_SECONDS")

    enable_sox_normalize: bool = Field(default=False, alias="ASR_ENABLE_SOX_NORMALIZE")
    ffmpeg_path: str = Field(default="ffmpeg", alias="ASR_FFMPEG_PATH")
    sox_path: str = Field(default="sox", alias="ASR_SOX_PATH")

    ollama_base_url: str = Field(default="http://localhost:11434", alias="ASR_OLLAMA_BASE_URL")
    ollama_model: str = Field(default="", alias="ASR_OLLAMA_MODEL")
    ollama_timeout_seconds: float = Field(default=30.0, alias="ASR_OLLAMA_TIMEOUT_SECONDS")
    ollama_temperature: float = Field(default=0.2, alias="ASR_OLLAMA_TEMPERATURE")
    ollama_top_p: float = Field(default=0.9, alias="ASR_OLLAMA_TOP_P")
    ollama_num_ctx: int = Field(default=4096, alias="ASR_OLLAMA_NUM_CTX")
    ollama_num_predict: int = Field(default=512, alias="ASR_OLLAMA_NUM_PREDICT")
    ollama_stop: str = Field(default="", alias="ASR_OLLAMA_STOP")


settings = Settings()
