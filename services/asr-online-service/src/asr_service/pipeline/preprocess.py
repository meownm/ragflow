import logging
import subprocess
import time
from pathlib import Path

from asr_service.settings import Settings


data_logger = logging.getLogger("asr.data")


class ToolInvocationError(RuntimeError):
    def __init__(
        self,
        *,
        tool: str,
        tool_path: str,
        cmd: list[str],
        returncode: int | None,
        stderr: str,
        cause: Exception,
    ) -> None:
        self.tool = tool
        self.tool_path = tool_path
        self.cmd = cmd
        self.returncode = returncode
        self.stderr = stderr
        self.cause = cause
        super().__init__(str(cause))


def _run_tool(tool: str, cmd: list[str]) -> None:
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise ToolInvocationError(
            tool=tool,
            tool_path=cmd[0],
            cmd=cmd,
            returncode=None,
            stderr="",
            cause=exc,
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise ToolInvocationError(
            tool=tool,
            tool_path=cmd[0],
            cmd=cmd,
            returncode=exc.returncode,
            stderr=exc.stderr or "",
            cause=exc,
        ) from exc


def preprocess_audio(source_uri: str, settings: Settings, output_dir: Path) -> tuple[Path, Path | None]:
    if source_uri.startswith("memory://"):
        path = Path(source_uri.replace("memory://", ""))
        return path, None

    src = Path(source_uri)
    if not src.exists() or not src.is_file():
        raise RuntimeError("W-ASR-SOURCE-URI-INVALID")

    output_dir.mkdir(parents=True, exist_ok=True)
    normalized_wav = output_dir / "normalized.wav"
    base_wav = output_dir / "prepared.wav"

    start = time.perf_counter()
    data_logger.info("audio_convert_start", extra={"event": "audio_convert_start", "payload": {"source_uri": str(src)}})
    _run_tool(
        "ffmpeg",
        [settings.ffmpeg_path, "-y", "-i", str(src), "-ac", "1", "-ar", "16000", "-acodec", "pcm_s16le", str(base_wav)],
    )
    duration_ms = int((time.perf_counter() - start) * 1000)
    data_logger.info("audio_convert_done", extra={"event": "audio_convert_done", "payload": {"duration_ms": duration_ms, "output_path": str(base_wav)}})

    if settings.enable_sox_normalize:
        nstart = time.perf_counter()
        data_logger.info("audio_normalize_start", extra={"event": "audio_normalize_start", "payload": {"input_path": str(base_wav)}})
        _run_tool("sox", [settings.sox_path, str(base_wav), str(normalized_wav), "gain", "-n", "-3"])
        nduration_ms = int((time.perf_counter() - nstart) * 1000)
        data_logger.info("audio_normalize_done", extra={"event": "audio_normalize_done", "payload": {"duration_ms": nduration_ms, "output_path": str(normalized_wav)}})
        return normalized_wav, normalized_wav

    return base_wav, None
