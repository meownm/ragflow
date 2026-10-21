"""The direct API launcher must resolve shared packages from the repository root."""

import ast
import json
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("script_directory", ["api", "."])
def test_api_startup_resolves_shared_exchange_package(script_directory, tmp_path):
    root = Path(__file__).resolve().parents[2]
    server = root / "api/ragflow_server.py"
    tree = ast.parse(server.read_text(encoding="utf-8"))
    first_app_import = next(index for index, node in enumerate(tree.body) if isinstance(node, ast.ImportFrom) and node.module == "api.apps")
    bootstrap = ast.unparse(ast.Module(body=tree.body[:first_app_import], type_ignores=[]))
    script = (
        "import sys,json; "
        f"sys.path.insert(0, {str(root / script_directory)!r}); "
        f"__file__ = {str(server)!r}; "
        f"exec({bootstrap!r}); "
        "import document_exchange.identity; "
        "print(json.dumps(document_exchange.identity.__file__))"
    )
    result = subprocess.run(
        [sys.executable, "-B", "-c", script],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
        timeout=15,
    )
    imported = Path(json.loads(result.stdout.splitlines()[-1])).resolve()
    assert imported == root / "document_exchange/identity.py"
