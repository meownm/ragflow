"""Run durable export cleanup after the document deletion has committed."""

import logging
from collections.abc import Sequence

from api.apps.business_documents.exports import BusinessDocumentExportService


class ExportCleanup:
    def __init__(self, storage=None):
        self._storage = storage

    def after_delete(self, document_id: str, stage_ids: Sequence[str]) -> int:
        failures = 0
        for stage_id in stage_ids:
            try:
                cleaned = BusinessDocumentExportService.cleanup_stage(stage_id, storage=self._storage)
            except Exception:
                cleaned = False
                logging.exception("Unable to reconcile business document export stage %s", stage_id)
            if not cleaned:
                failures += 1
        try:
            BusinessDocumentExportService.reconcile_staging(storage=self._storage, document_id=document_id)
        except Exception:
            logging.exception("Unable to reconcile abandoned export stages for deleted business document %s", document_id)
        return failures
