#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#

"""Shared RAGFlow Dataset retrieval for OpenMetadata API and Canvas agents."""

from __future__ import annotations

import logging
from typing import Any

from common.misc_utils import thread_pool_exec

LOGGER = logging.getLogger(__name__)


async def retrieve_openmetadata_dataset_hits(
    service: Any,
    question: str,
    user_id: str,
) -> tuple[list[dict] | None, str | None]:
    dataset_id = str(getattr(service.config, "dataset_id", "") or "").strip()
    if not dataset_id or not str(question or "").strip():
        return None, None

    try:
        from api.apps.services import dataset_api_service
        from api.db.services.doc_metadata_service import DocMetadataService

        ok, result = await dataset_api_service.search(
            dataset_id,
            user_id,
            {
                "question": question,
                "page": 1,
                "size": int(getattr(service.config, "dataset_top_n", 20)),
                "top_k": 1024,
                "similarity_threshold": float(getattr(service.config, "dataset_similarity_threshold", 0.05)),
                "vector_similarity_weight": float(getattr(service.config, "dataset_vector_similarity_weight", 0.3)),
            },
        )
        if not ok or not isinstance(result, dict):
            return [], "OpenMetadata Dataset недоступен; использован live-поиск OMD"
        hits = [dict(hit) for hit in result.get("chunks") or [] if isinstance(hit, dict)]
        doc_ids = sorted({str(hit.get("doc_id") or hit.get("document_id") or "") for hit in hits if hit.get("doc_id") or hit.get("document_id")})
        metadata = await thread_pool_exec(
            DocMetadataService.get_metadata_for_documents,
            doc_ids,
            dataset_id,
        )
        for hit in hits:
            doc_id = str(hit.get("doc_id") or hit.get("document_id") or "")
            if doc_id in metadata:
                hit["metadata"] = metadata[doc_id]
        return hits, None
    except Exception:
        LOGGER.exception("OpenMetadata Dataset retrieval failed")
        return [], "OpenMetadata Dataset недоступен; использован live-поиск OMD"
