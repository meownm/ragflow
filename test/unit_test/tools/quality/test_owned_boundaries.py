"""Small executable checks for owned application boundaries."""

from __future__ import annotations

import ast
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[4]
PURE_ROOTS = (
    ROOT / "business_documents" / "domain",
    ROOT / "business_documents" / "application",
    ROOT / "business_documents" / "sql_query",
)
PURE_FILES = (
    ROOT / "api" / "source_workbench" / "processing.py",
    ROOT / "api" / "source_workbench" / "service.py",
)
INFRA_IMPORTS = (
    "api.db",
    "api.apps",
    "rag",
    "common",
    "quart",
    "peewee",
    "requests",
    "httpx",
    "sqlalchemy",
    "redis",
    "boto3",
    "pymysql",
    "psycopg",
    "litellm",
    "tiktoken",
)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    modules: set[str] = set()
    package = path.relative_to(ROOT).with_suffix("").parts[:-1]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = package[: len(package) - node.level + 1] if node.level else ()
            if node.module:
                modules.add(".".join((*base, *node.module.split("."))))
            else:
                modules.update(".".join((*base, alias.name)) for alias in node.names)
    return modules


def test_owned_domain_and_application_do_not_import_infrastructure():
    files = [path for root in PURE_ROOTS for path in root.rglob("*.py")]
    files.extend(PURE_FILES)
    assert files
    violations = [f"{path.relative_to(ROOT)}: {module}" for path in files for module in _imports(path) if any(module == prefix or module.startswith(prefix + ".") for prefix in INFRA_IMPORTS)]
    assert not violations, "\n".join(violations)


def test_owned_layers_import_only_inward():
    forbidden = {
        "business_documents/domain": ("business_documents.application", "business_documents.sql_query"),
        "business_documents/application": ("business_documents.sql_query",),
        "business_documents/sql_query": ("business_documents.application",),
        "api/source_workbench/processing.py": ("api.source_workbench.service", "api.source_workbench.adapters"),
        "api/source_workbench/service.py": ("api.source_workbench.adapters",),
    }
    violations = []
    for location, modules in forbidden.items():
        target = ROOT / location
        paths = target.rglob("*.py") if target.is_dir() else [target]
        for path in paths:
            for module in _imports(path):
                if any(module == prefix or module.startswith(prefix + ".") for prefix in modules):
                    violations.append(f"{path.relative_to(ROOT)}: {module}")
    assert not violations, "\n".join(violations)


def test_source_processing_import_does_not_initialize_tokenizer_cache():
    script = (
        "import os, sys; "
        "before = os.environ.get('TIKTOKEN_CACHE_DIR'); "
        "import api.source_workbench.processing; "
        "assert 'common.token_utils' not in sys.modules; "
        "assert os.environ.get('TIKTOKEN_CACHE_DIR') == before"
    )
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(ROOT)}
    result = subprocess.run([sys.executable, "-B", "-c", script], cwd=ROOT, env=env, capture_output=True, text=True, timeout=20, check=False)
    assert result.returncode == 0, result.stderr


def test_business_document_api_and_worker_share_composed_application_scenarios():
    api_imports = _imports(ROOT / "api" / "apps" / "restful_apis" / "business_document_api.py")
    worker_imports = _imports(ROOT / "api" / "apps" / "business_documents" / "worker.py")
    owner = "api.apps.business_documents.runtime"
    assert owner in api_imports and owner in worker_imports
    composition = _imports(ROOT / "api" / "apps" / "business_documents" / "runtime.py")
    assert {
        "business_documents.application.change_application",
        "business_documents.application.commands",
        "business_documents.application.job_completion",
        "business_documents.application.queries",
        "business_documents.application.documents",
        "business_documents.application.access",
        "business_documents.application.eva_sync",
    } <= composition
    assert "api.apps.business_documents.service" not in worker_imports
    assert "api.apps.business_documents.service" not in api_imports
