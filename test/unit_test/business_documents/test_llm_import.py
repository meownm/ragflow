"""The shared adapter can be loaded without bootstrapping application infrastructure."""

import subprocess
import sys


def test_llm_adapter_import_does_not_bootstrap_api_or_load_clients():
    subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            """
import importlib.abc
import sys

class RejectInfrastructure(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname in {'api.apps', 'api.db', 'peewee', 'httpx'}:
            raise AssertionError('Unexpected infrastructure import: ' + fullname)

sys.meta_path.insert(0, RejectInfrastructure())
from business_documents.adapters.llm import RAGFlowLLMAdapter
from business_documents.async_runtime import run_in_worker_loop
assert callable(RAGFlowLLMAdapter) and callable(run_in_worker_loop)
""",
        ],
        check=True,
    )
