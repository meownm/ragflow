# Configuration

## Core
- `ASR_SERVICE_HOST`, `ASR_SERVICE_PORT`
- `ASR_LOG_LEVEL`, `LOG_DATA_MODE=plain|masked`
- `ASR_MAX_CONCURRENT_JOBS`, `ASR_JOB_TTL_SECONDS`
- `ASR_ARTIFACTS_DIR`, `ASR_UPLOAD_DIR`

The synchronous OpenAI-compatible transcription endpoint has no independent
wall-clock timeout. It waits until the job reaches a terminal state; use
`ASR_JOB_TTL_SECONDS` as the upper bound for abandoned or stuck jobs.
- `ASR_STREAM_POLL_SECONDS` — job-state polling interval for SSE (default `0.1`).
- `ASR_STREAM_HEARTBEAT_SECONDS` — maximum quiet period between SSE events
  (default `15` seconds).

The RAGFlow `New API` adapter uses `RAGFLOW_ASR_STREAM_IDLE_TIMEOUT_SECONDS`
as its SSE read-idle timeout (default `120` seconds). Heartbeats reset this
idle timer; it is not a total transcription deadline.

## Audio preprocess
- `ASR_FFMPEG_PATH` — ffmpeg binary used for conversion to WAV PCM s16le mono 16k.
- `ASR_ENABLE_SOX_NORMALIZE` — enables optional post-convert normalization.
- `ASR_SOX_PATH` — sox binary for normalization stage.

## Ollama enrich
- `ASR_OLLAMA_BASE_URL`
- `ASR_OLLAMA_MODEL` (required when `options.enrich.enabled=true`)
- `ASR_OLLAMA_TIMEOUT_SECONDS`
- `ASR_OLLAMA_TEMPERATURE`
- `ASR_OLLAMA_TOP_P`
- `ASR_OLLAMA_NUM_CTX`
- `ASR_OLLAMA_NUM_PREDICT`
- `ASR_OLLAMA_STOP` (CSV stop words)

If enrich is enabled and model is empty, job fails with `W-ASR-OLLAMA-NOT-CONFIGURED`.

## Job output options
- `options.output.artifact_formats` supports `result_json` (always persisted), `txt`, `docx`, `normalized_wav`.
- Artifact files are downloaded via `GET /v1/asr/jobs/{job_id}/artifacts/{kind}`.
