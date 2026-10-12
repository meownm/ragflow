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
from ragflow_sdk.modules.chunk import ChunkUpdateError
from utils import wait_for


class TestUpdatedChunk:
    @pytest.mark.p1
    @pytest.mark.parametrize(
        "payload, expected_message",
        [
            ({"content": None}, ""),
            ({"content": ""}, "`content` is required"),
            ({"content": 1}, "`content` must be a string"),
            ({"content": "update chunk"}, ""),
            ({"content": " "}, "`content` is required"),
            ({"content": "\n!?。；！？\"'"}, ""),
        ],
    )
    def test_content(self, add_chunks, payload, expected_message):
        _, _, chunks = add_chunks
        chunk = chunks[0]

        if expected_message:
            with pytest.raises(ChunkUpdateError) as exception_info:
                chunk.update(payload)
            assert exception_info.value.code == 102
            assert expected_message in str(exception_info.value), str(exception_info.value)
        else:
            chunk.update(payload)

    @pytest.mark.p2
    @pytest.mark.parametrize(
        "payload, expected_message",
        [
            ({"important_keywords": ["a", "b", "c"]}, ""),
            ({"important_keywords": [""]}, ""),
            ({"important_keywords": [1]}, "`important_keywords` must be a list of strings"),
            ({"important_keywords": ["a", "a"]}, ""),
            ({"important_keywords": "abc"}, "`important_keywords` should be a list"),
            ({"important_keywords": 123}, "`important_keywords` should be a list"),
        ],
    )
    def test_important_keywords(self, add_chunks, payload, expected_message):
        _, _, chunks = add_chunks
        chunk = chunks[0]

        if expected_message:
            with pytest.raises(ChunkUpdateError) as exception_info:
                chunk.update(payload)
            assert exception_info.value.code == 102
            assert expected_message in str(exception_info.value), str(exception_info.value)
        else:
            chunk.update(payload)

    @pytest.mark.p2
    @pytest.mark.parametrize(
        "payload, expected_message",
        [
            ({"questions": ["a", "b", "c"]}, ""),
            ({"questions": [""]}, ""),
            ({"questions": [1]}, "`questions` must be a list of strings"),
            ({"questions": ["a", "a"]}, ""),
            ({"questions": "abc"}, "`questions` should be a list"),
            ({"questions": 123}, "`questions` should be a list"),
        ],
    )
    def test_questions(self, add_chunks, payload, expected_message):
        _, _, chunks = add_chunks
        chunk = chunks[0]

        if expected_message:
            with pytest.raises(ChunkUpdateError) as exception_info:
                chunk.update(payload)
            assert exception_info.value.code == 102
            assert expected_message in str(exception_info.value), str(exception_info.value)
        else:
            chunk.update(payload)

    @pytest.mark.p2
    @pytest.mark.parametrize(
        "payload, expected_message",
        [
            ({"tag_kwd": ["tag1", "tag2"]}, ""),
            ({"tag_kwd": [""]}, ""),
            ({"tag_kwd": [1]}, "`tag_kwd` must be a list of strings"),
            ({"tag_kwd": ["tag", "tag"]}, ""),
            ({"tag_kwd": "tag"}, "`tag_kwd` should be a list"),
            ({"tag_kwd": 123}, "`tag_kwd` should be a list"),
        ],
    )
    def test_tag_kwd(self, add_chunks, payload, expected_message):
        _, _, chunks = add_chunks
        chunk = chunks[0]

        if expected_message:
            with pytest.raises(ChunkUpdateError) as exception_info:
                chunk.update(payload)
            assert exception_info.value.code == 102
            assert expected_message in str(exception_info.value), str(exception_info.value)
        else:
            chunk.update(payload)

    @pytest.mark.p2
    @pytest.mark.parametrize(
        "payload, expected_message",
        [
            ({"available": True}, ""),
            ({"available": "True"}, "`available` must be a boolean or 0 or 1"),
            ({"available": 1}, ""),
            ({"available": False}, ""),
            ({"available": "False"}, "`available` must be a boolean or 0 or 1"),
            ({"available": 0}, ""),
        ],
    )
    def test_available(self, add_chunks, payload, expected_message):
        _, _, chunks = add_chunks
        chunk = chunks[0]

        if expected_message:
            with pytest.raises(ChunkUpdateError) as exception_info:
                chunk.update(payload)
            assert exception_info.value.code == 102
            assert expected_message in str(exception_info.value), str(exception_info.value)
        else:
            chunk.update(payload)

    @pytest.mark.p3
    def test_repeated_update_chunk(self, add_chunks):
        _, _, chunks = add_chunks
        chunk = chunks[0]

        chunk.update({"content": "chunk test 1"})
        chunk.update({"content": "chunk test 2"})

    @pytest.mark.p3
    def test_concurrent_update_chunk(self, add_chunks):
        count = 50
        _, document, chunks = add_chunks
        initial_ids = {chunk.id for chunk in document.list_chunks(page_size=100)}
        assert {chunk.id for chunk in chunks}.issubset(initial_ids)
        expected = {chunk.id: set() for chunk in chunks[:4]}
        for index in range(count):
            expected[chunks[index % 4].id].add(f"update chunk test {index}")

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(chunks[i % 4].update, {"content": f"update chunk test {i}"}) for i in range(count)]
        responses = [future.result() for future in as_completed(futures)]
        assert len(responses) == count, responses

        @wait_for(30, 1, "Concurrent chunk updates did not become visible")
        def visible():
            actual = document.list_chunks(page_size=100)
            by_id = {chunk.id: chunk for chunk in actual}
            return len(actual) == len(initial_ids) and set(by_id) == initial_ids and all(by_id[chunk_id].content in contents for chunk_id, contents in expected.items())

        visible()

    @pytest.mark.p3
    def test_update_chunk_to_deleted_document(self, add_chunks):
        dataset, document, chunks = add_chunks
        dataset.delete_documents(ids=[document.id])

        with pytest.raises(Exception) as exception_info:
            chunks[0].update({})
        message = str(exception_info.value)
        ownership_message = f"You don't own the document {chunks[0].document_id}"
        assert message.rstrip(".") == ownership_message or message == f"Can't find this chunk {chunks[0].id}", message
