import os
import subprocess
import sys
from pathlib import Path

from watchfiles import watch


def run_server(service_root: Path) -> subprocess.Popen:
    port = os.getenv("ASR_SERVICE_PORT", "9011")
    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "asr_service.main:app",
        "--host",
        "0.0.0.0",
        "--port",
        port,
    ]
    return subprocess.Popen(cmd, cwd=str(service_root))


def main() -> None:
    service_root = Path(__file__).resolve().parents[2]
    process = run_server(service_root)
    try:
        for _ in watch(service_root / "src", service_root / ".env"):
            process.terminate()
            process.wait(timeout=10)
            process = run_server(service_root)
    finally:
        process.terminate()


if __name__ == "__main__":
    main()
