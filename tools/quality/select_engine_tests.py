#!/usr/bin/env python3
"""Select the two live engine suites when the engine boundary changes."""

from __future__ import annotations

from fnmatch import fnmatchcase
import json
import os
from pathlib import Path
import re
import subprocess
import tomllib
from urllib.parse import urlencode
from urllib.request import Request, urlopen


# These paths own index storage, ingestion, retrieval, or the engine runtime.
# UI and application-only changes keep their own focused checks.
ENGINE_PATHS = (
    "common/doc_store/*",
    "common/settings.py",
    "common/constants.py",
    "common/config_utils.py",
    "rag/utils/*_conn.py",
    "rag/utils/table_es_metadata.py",
    "rag/nlp/*",
    "rag/svr/*",
    "rag/graph/*",
    "memory/utils/*_conn.py",
    "internal/engine/*",
    "internal/ingestion/*",
    "internal/service/nlp/*",
    "internal/service/graph/*",
    "internal/service/dataset*.go",
    "internal/service/search*.go",
    "internal/server/*",
    "internal/entity/*",
    "internal/types/*",
    "api/db/services/document_service.py",
    "api/db/services/knowledgebase_service.py",
    "api/db/services/task_service.py",
    "api/db/services/chunk*",
    "api/db/db_models.py",
    "api/apps/chunk_app.py",
    "api/apps/retrieval_app.py",
    "api/apps/restful_apis/dataset*.py",
    "Dockerfile*",
    "docker/*",
    "conf/*",
    "pyproject.toml",
    "uv.lock",
    "requirements*.txt",
    "go.mod",
    "go.sum",
    "build.sh",
    "ragflow_deps/*",
    "internal/cpp/*",
    "internal/binding/*",
    ".github/workflows/tests.yml",
    ".github/workflows/sep-tests.yml",
    ".github/workflows/release.yml",
    "deployment/runner/*",
    "tools/quality/select_engine_tests.py",
    "test/unit_test/tools/quality/test_engine_test_selection.py",
)


def engine_paths(paths: list[str]) -> list[str]:
    return [path for path in paths if not path.endswith((".md", ".mdx")) and any(fnmatchcase(path, pattern) for pattern in ENGINE_PATHS)]


def _version_only(path: str, base: str) -> bool:
    if path not in {"pyproject.toml", "uv.lock"}:
        return False
    documents = []
    for revision in (base, "HEAD"):
        source = subprocess.check_output(["git", "show", f"{revision}:{path}"], stderr=subprocess.PIPE).decode("utf-8")
        document = tomllib.loads(source)
        if path == "pyproject.toml":
            document.get("project", {}).pop("version", None)
        else:
            for package in document.get("package", []):
                if package.get("source") in ({"virtual": "."}, {"editable": "."}):
                    package.pop("version", None)
        documents.append(document)
    return documents[0] == documents[1]


def successful_base(env: dict[str, str]) -> str:
    """Include engine edits from earlier failed/cancelled pushes in this run."""
    branch = env.get("GITHUB_REF_NAME", "") if env.get("GITHUB_REF", "").startswith("refs/heads/") else env["CI_DEFAULT_BRANCH"]
    query = urlencode({"branch": branch, "status": "success", "per_page": 100})
    url = f"{env.get('GITHUB_API_URL', 'https://api.github.com')}/repos/{env['GITHUB_REPOSITORY']}/actions/workflows/{env['CI_WORKFLOW_FILE']}/runs?{query}"
    request = Request(url, headers={"Authorization": f"Bearer {env['GH_TOKEN']}", "Accept": "application/vnd.github+json"})
    with urlopen(request, timeout=15) as response:
        runs = json.load(response)["workflow_runs"]
    for run in runs:
        if str(run["id"]) != env.get("GITHUB_RUN_ID") and run["event"] in {"push", "schedule"}:
            return run["head_sha"]
    raise ValueError("no successful branch baseline")


def select(env: dict[str, str]) -> dict:
    base = ""
    try:
        is_pr = env.get("GITHUB_EVENT_NAME") == "pull_request"
        base = env.get("CI_PR_BASE", "") if is_pr else successful_base(env)
        if not re.fullmatch(r"[0-9a-f]{40}", base) or base == "0" * 40:
            raise ValueError("invalid baseline")
        subprocess.run(["git", "merge-base", "--is-ancestor", base, "HEAD"], check=True, capture_output=True)
        result = subprocess.run(["git", "diff", "--name-only", "--no-renames", "-z", base, "HEAD"], check=True, capture_output=True)
        paths = [path.decode("utf-8") for path in result.stdout.split(b"\0") if path]
        changed = [path for path in engine_paths(paths) if not _version_only(path, base)]
        return {"required": bool(changed), "reason": "engine-boundary-changed" if changed else "no-engine-changes", "base": base, "paths": changed}
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError):
        # Missing history, Actions permissions or malformed data must not hide changes.
        return {"required": True, "reason": "baseline-unavailable", "base": base, "paths": []}


def main() -> None:
    selection = select(dict(os.environ))
    print(json.dumps(selection))
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
        output.write(f"run_engine_tests={str(selection['required']).lower()}\nengine_test_reason={selection['reason']}\nengine_test_base={selection['base']}\n")


if __name__ == "__main__":
    main()
