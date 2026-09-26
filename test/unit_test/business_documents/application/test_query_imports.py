"""Document scenarios are importable without server or persistence bootstrap."""

import os
from pathlib import Path
import subprocess
import sys


def test_document_scenarios_import_without_api_database_or_settings():
    root = Path(__file__).resolve().parents[4]
    script = """
import sys
from business_documents.application.queries import DocumentQueries
from business_documents.application.change_application import ApplyChanges
from business_documents.application.job_completion import JobCompletion
from business_documents.application.commands import DocumentCommands
from business_documents.application.documents import CreateDocument, DeleteDocument
from business_documents.application.access import ChangeUserRole
from business_documents.application.eva_sync import EvaSynchronization
from business_documents.application.errors import BusinessDocumentError
for name in sys.modules:
    assert not name.startswith(('api.', 'common.', 'peewee', 'quart', 'rag.')), name
assert DocumentQueries.__module__ == 'business_documents.application.queries'
assert ApplyChanges.__module__ == 'business_documents.application.change_application'
assert JobCompletion.__module__ == 'business_documents.application.job_completion'
assert DocumentCommands.__module__ == 'business_documents.application.commands'
assert DeleteDocument.__module__ == 'business_documents.application.documents'
assert CreateDocument.__module__ == 'business_documents.application.documents'
assert ChangeUserRole.__module__ == 'business_documents.application.access'
assert EvaSynchronization.__module__ == 'business_documents.application.eva_sync'
assert BusinessDocumentError('EXAMPLE', 'message', 409).status == 409
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", script],
        cwd=root,
        env={**os.environ, "PYTHONPATH": str(root), "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
