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
import random

import pytest
from ragflow_sdk import RAGFlow, Memory
from configs import INVALID_API_TOKEN, HOST_ADDRESS
from utils.engine_utils import get_doc_engine


class TestAuthorization:
    @pytest.mark.p2
    @pytest.mark.parametrize(
        "invalid_auth, expected_message",
        [
            (None, "<Unauthorized '401: Unauthorized'>"),
            (INVALID_API_TOKEN, "<Unauthorized '401: Unauthorized'>"),
        ],
    )
    def test_auth_invalid(self, invalid_auth, expected_message):
        client = RAGFlow(invalid_auth, HOST_ADDRESS)
        with pytest.raises(Exception) as exception_info:
            memory = Memory(client, {"id": "empty_memory_id"})
            memory.list_memory_messages()
        assert str(exception_info.value) == expected_message, str(exception_info.value)


@pytest.mark.usefixtures("add_memory_with_5_raw_message_func")
class TestMessageList:
    @pytest.mark.p2
    def test_params_unset(self, client):
        memory_id = self.memory_id
        memory = Memory(client, {"id": memory_id})
        res = memory.list_memory_messages()
        assert len(res["messages"]["message_list"]) == 5, str(res)

    @pytest.mark.p2
    def test_params_empty(self, client):
        memory_id = self.memory_id
        memory = Memory(client, {"id": memory_id})
        res = memory.list_memory_messages(**{})
        assert len(res["messages"]["message_list"]) == 5, str(res)

    @pytest.mark.p1
    @pytest.mark.parametrize(
        "params, expected_page_size",
        [
            ({"page": 1, "page_size": 10}, 5),
            ({"page": 2, "page_size": 10}, 0),
            ({"page": 1, "page_size": 2}, 2),
            ({"page": 3, "page_size": 2}, 1),
            ({"page": 5, "page_size": 10}, 0),
        ],
        ids=["normal_first_page", "beyond_max_page", "normal_last_partial_page", "normal_middle_page", "full_data_single_page"],
    )
    def test_page_size(self, client, params, expected_page_size):
        # have added 5 messages in fixture
        memory_id = self.memory_id
        memory = Memory(client, {"id": memory_id})
        res = memory.list_memory_messages(**params)
        assert len(res["messages"]["message_list"]) == expected_page_size, str(res)

    @pytest.mark.p2
    def test_filter_agent_id(self, client):
        memory_id = self.memory_id
        agent_ids = self.agent_ids
        agent_id = random.choice(agent_ids)
        memory = Memory(client, {"id": memory_id})
        res = memory.list_memory_messages(**{"agent_id": agent_id})
        for message in res["messages"]["message_list"]:
            assert message["agent_id"] == agent_id, message

    @pytest.mark.p2
    def test_search_keyword(self, client):
        memory_id = self.memory_id
        engine = get_doc_engine(client)
        assert engine in {"elasticsearch", "infinity", "opensearch"}, "Document engine prerequisite is missing"
        session_id = self.session_ids[0]
        memory = Memory(client, {"id": memory_id})
        exact = memory.list_memory_messages(keywords=session_id)["messages"]["message_list"]
        assert exact, exact
        assert all(message["session_id"] == session_id for message in exact), exact
        # ES applies a wildcard substring filter; Infinity applies string equality.
        keyword = session_id[1:-1]
        assert keyword and keyword not in self.session_ids
        res = memory.list_memory_messages(**{"keywords": keyword})
        if engine == "infinity":
            assert res["messages"]["message_list"] == [], res
            return
        assert len(res["messages"]["message_list"]) > 0, res
        for message in res["messages"]["message_list"]:
            assert keyword in message["session_id"], message
