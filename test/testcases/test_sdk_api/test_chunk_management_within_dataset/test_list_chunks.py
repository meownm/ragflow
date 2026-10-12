#
#  Copyright 2025 The InfiniFlow Authors. All Rights Reserved.
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
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest
from common import batch_add_chunks
from utils.engine_utils import get_doc_engine


class TestChunksList:
    @pytest.mark.p1
    @pytest.mark.parametrize(
        "params, expected_page_size, expected_message",
        [
            ({"page": None, "page_size": 2}, 2, ""),
            ({"page": 0, "page_size": 2}, 0, "page must be greater than or equal to 1"),
            ({"page": 2, "page_size": 2}, 2, ""),
            ({"page": 3, "page_size": 2}, 1, ""),
            ({"page": "3", "page_size": 2}, 1, ""),
            ({"page": -1, "page_size": 2}, 0, "page must be greater than or equal to 1"),
            ({"page": "a", "page_size": 2}, 0, "page must be an integer"),
        ],
    )
    def test_page(self, add_chunks, params, expected_page_size, expected_message):
        _, document, _ = add_chunks

        if expected_message:
            with pytest.raises(Exception) as exception_info:
                document.list_chunks(**params)
            assert expected_message in str(exception_info.value), str(exception_info.value)
        else:
            chunks = document.list_chunks(**params)
            assert len(chunks) == expected_page_size, str(chunks)

    @pytest.mark.p1
    @pytest.mark.parametrize(
        "params, expected_page_size, expected_message",
        [
            ({"page_size": None}, 5, ""),
            ({"page_size": 0}, 0, ""),
            ({"page_size": 1}, 1, ""),
            ({"page_size": 6}, 5, ""),
            ({"page_size": "1"}, 1, ""),
            ({"page_size": -1}, 0, "page_size must be greater than or equal to 0"),
            ({"page_size": "a"}, 0, "page_size must be an integer"),
        ],
    )
    def test_page_size(self, add_chunks, params, expected_page_size, expected_message):
        _, document, _ = add_chunks

        if expected_message:
            with pytest.raises(Exception) as exception_info:
                document.list_chunks(**params)
            assert expected_message in str(exception_info.value), str(exception_info.value)
        else:
            chunks = document.list_chunks(**params)
            assert len(chunks) == expected_page_size, str(chunks)

    @pytest.mark.p2
    @pytest.mark.parametrize(
        "params, expected_page_size",
        [
            ({"keywords": None}, 5),
            ({"keywords": ""}, 5),
            ({"keywords": "1"}, 1),
            ({"keywords": "chunk"}, 4),
            ({"keywords": "ragflow"}, 1),
            ({"keywords": "unknown"}, 0),
        ],
    )
    def test_keywords(self, add_chunks, params, expected_page_size):
        _, document, _ = add_chunks
        assert get_doc_engine(document.rag) in {"elasticsearch", "infinity", "opensearch"}, "Document engine prerequisite is missing"
        chunks = document.list_chunks(**params)
        assert len(chunks) == expected_page_size, str(chunks)

    @pytest.mark.p1
    @pytest.mark.parametrize(
        "chunk_id, expected_page_size, expected_message",
        [
            (None, 5, ""),
            ("", 5, ""),
            (lambda r: r[0], 1, ""),
            ("unknown", 0, "Chunk not found:"),
        ],
    )
    def test_id(self, add_chunks, chunk_id, expected_page_size, expected_message):
        _, document, chunks = add_chunks
        chunk_ids = [chunk.id for chunk in chunks]
        if callable(chunk_id):
            params = {"id": chunk_id(chunk_ids)}
        else:
            params = {"id": chunk_id}

        if expected_message:
            with pytest.raises(Exception) as exception_info:
                document.list_chunks(**params)
            assert expected_message in str(exception_info.value), str(exception_info.value)
        else:
            chunks = document.list_chunks(**params)
            if params["id"] in [None, ""]:
                assert len(chunks) == expected_page_size, str(chunks)
            else:
                assert len(chunks) == expected_page_size, str(chunks)
                assert chunks[0].id == params["id"], str(chunks)

    @pytest.mark.p3
    def test_concurrent_list(self, add_chunks):
        _, document, _ = add_chunks
        count = 100
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(document.list_chunks) for _ in range(count)]

        responses = [future.result() for future in as_completed(futures)]
        assert len(responses) == count, responses
        assert all(len(response) == 5 for response in responses)

    @pytest.mark.p1
    def test_default(self, add_document):
        _, document = add_document
        batch_add_chunks(document, 31)

        from time import sleep

        sleep(3)

        chunks = document.list_chunks()
        assert len(chunks) == 30, str(chunks)

    @pytest.mark.p3
    def test_list_chunks_invalid_document_id_raises(self, add_chunks):
        _, document, _ = add_chunks
        invalid_document = document.__class__(
            document.rag,
            {"id": "missing-document-id-for-chunks", "dataset_id": document.dataset_id},
        )
        with pytest.raises(Exception) as exception_info:
            invalid_document.list_chunks()
        assert str(exception_info.value), exception_info
