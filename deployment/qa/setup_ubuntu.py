"""Create or update the persistent Ubuntu QA stack without deleting its volumes."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import time
from urllib.request import urlopen


MODELS = ("qwen3.8:latest", "qwen3.6:27b", "bge-m3:latest")
IMAGE = re.compile(r"^.+/ragflow:([0-9a-f]{40})$")
ROOT = Path(__file__).resolve().parent


def run(*args: str, input_text: str | None = None) -> str:
    result = subprocess.run(args, input=input_text, text=True, capture_output=True, timeout=600, check=False)
    if result.returncode:
        raise RuntimeError(f"{' '.join(args[:3])} failed with exit {result.returncode}")
    return result.stdout.strip()


def settings(image: str, ollama_url: str) -> dict[str, str]:
    path = ROOT / ".env"
    if path.exists():
        values = dict(line.split("=", 1) for line in path.read_text(encoding="utf-8").splitlines() if "=" in line)
    else:
        values = {
            "QA_STORAGE_PASSWORD": secrets.token_urlsafe(32),
            "QA_ADMIN_EMAIL": "ragflow-qa@example.invalid",
            "QA_ADMIN_PASSWORD": secrets.token_urlsafe(32),
        }
    values["RAGFLOW_IMAGE"] = image
    values["QA_OLLAMA_URL"] = ollama_url
    temporary = path.with_suffix(".env.tmp")
    temporary.write_text("".join(f"{key}={value}\n" for key, value in values.items()), encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(path)
    return values


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, help="Registry image tagged with a full Git SHA")
    parser.add_argument("--ollama-url", required=True, help="Project Ollama proxy URL reachable from the QA container")
    args = parser.parse_args()
    match = IMAGE.fullmatch(args.image)
    if not match:
        parser.error("image must be a registry ragflow image tagged with a full Git SHA")
    if not args.ollama_url.startswith("http://192.168.1."):
        parser.error("Ollama proxy must use the checked LAN endpoint")
    with urlopen(args.ollama_url.rstrip("/") + "/api/tags", timeout=10) as response:
        model_rows = {row["name"]: row for row in json.load(response)["models"]}
    if any(name not in model_rows or model_rows[name].get("size", 0) <= 1_000_000 for name in MODELS):
        raise RuntimeError("QA Ollama proxy lacks a required real model")
    actual_revision = run("docker", "run", "--rm", "--entrypoint", "cat", args.image, "/ragflow/SOURCE_REVISION")
    if actual_revision != match.group(1):
        raise RuntimeError("candidate image SOURCE_REVISION differs from its tag")
    run("docker", "run", "--rm", "--entrypoint", "/ragflow/.venv/bin/python", args.image, "-c", "import business_documents")
    values = settings(args.image, args.ollama_url)
    env = {**os.environ, **values}
    compose = ["docker", "compose", "--env-file", str(ROOT / ".env"), "-f", str(ROOT / "compose.yml")]
    subprocess.run([*compose, "config", "--quiet"], env=env, check=True, timeout=30)
    subprocess.run([*compose, "up", "-d"], env=env, check=True, timeout=600)
    deadline = time.monotonic() + 360
    while True:
        try:
            with urlopen("http://127.0.0.1:19382/api/v1/system/healthz", timeout=5) as response:
                health = json.load(response)
            if health.get("status") == "ok":
                break
        except (OSError, ValueError):
            pass
        if time.monotonic() > deadline:
            raise RuntimeError("QA RAGFlow did not become healthy within 360 seconds")
        time.sleep(5)
    configured_path = ROOT / "models-configured.json"
    expected_configuration = {
        "image": args.image,
        "ollama_url": args.ollama_url,
        "models": {name: model_rows[name]["digest"] for name in MODELS},
    }
    configured = json.loads(configured_path.read_text(encoding="utf-8")) if configured_path.exists() else None
    if configured != expected_configuration:
        sources = (ROOT / "configure_models.py",)
        if configured is None:
            sources = (ROOT / "seed_catalog.py", *sources)
        for source in sources:
            script = source.read_text(encoding="utf-8")
            subprocess.run([*compose, "exec", "-T", "app", "/ragflow/.venv/bin/python", "-"], input=script, text=True, env=env, check=True, timeout=600)
        configured_path.write_text(json.dumps(expected_configuration, indent=2) + "\n", encoding="utf-8")
    report = {"schema": 1, "project": "ragflow-qa", "image": args.image, "source_revision": actual_revision, "health": health, "models": {name: model_rows[name]["digest"] for name in MODELS}}
    (ROOT / "setup.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"project": report["project"], "source_revision": actual_revision, "health": health.get("status")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
