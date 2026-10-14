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

import pytest
from beartype.roar import BeartypeCallHintParamViolation
from configs import CHAT_ASSISTANT_NAME_LIMIT
from utils import encode_avatar
from utils.file_utils import create_image_file


@pytest.mark.usefixtures("clear_chat_assistants")
class TestChatAssistantCreate:
    @pytest.mark.p1
    @pytest.mark.usefixtures("add_chunks")
    @pytest.mark.parametrize(
        "name, expected_message",
        [
            ("valid_name", ""),
            pytest.param("a" * (CHAT_ASSISTANT_NAME_LIMIT + 1), "Chat name length is 256 which is larger than 255."),
            pytest.param(1, "violates type hint"),
            ("", "`name` is required."),
            ("duplicated_name", "Duplicated chat name in creating chat."),
            ("case insensitive", "Duplicated chat name in creating chat."),
        ],
    )
    def test_name(self, client, name, expected_message):
        if name == "duplicated_name":
            client.create_chat(name=name)
        elif name == "case insensitive":
            client.create_chat(name=name.upper())

        if expected_message:
            with pytest.raises(BeartypeCallHintParamViolation if not isinstance(name, str) else Exception) as exception_info:
                client.create_chat(name=name)
            assert expected_message in str(exception_info.value)
        else:
            chat_assistant = client.create_chat(name=name)
            assert chat_assistant.name == name

    @pytest.mark.p3
    @pytest.mark.parametrize(
        "dataset_ids, expected_message",
        [
            ([], ""),
            (lambda r: [r], ""),
            (["invalid_dataset_id"], "You don't own the dataset invalid_dataset_id"),
            ("invalid_dataset_id", "violates type hint list[str] | None"),
        ],
    )
    def test_dataset_ids(self, client, add_chunks, dataset_ids, expected_message):
        dataset, _, _ = add_chunks
        if callable(dataset_ids):
            dataset_ids = dataset_ids(dataset.id)

        if expected_message:
            with pytest.raises(Exception) as exception_info:
                client.create_chat(name="ragflow test", dataset_ids=dataset_ids)
            assert expected_message in str(exception_info.value)
        else:
            chat_assistant = client.create_chat(name="ragflow test", dataset_ids=dataset_ids)
            assert chat_assistant.name == "ragflow test"

    @pytest.mark.p3
    def test_icon(self, client, tmp_path):
        fn = create_image_file(tmp_path / "ragflow_test.png")
        chat_assistant = client.create_chat(name="icon_test", icon=encode_avatar(fn), dataset_ids=[])
        assert chat_assistant.name == "icon_test"

    @pytest.mark.p3
    @pytest.mark.parametrize(
        "llm_setting, expected_message",
        [
            ({}, ""),
            ({"temperature": 0}, ""),
            ({"temperature": 1}, ""),
            pytest.param({"temperature": -1}, ""),
            pytest.param({"temperature": 10}, ""),
            pytest.param({"temperature": "a"}, ""),
            ({"top_p": 0}, ""),
            ({"top_p": 1}, ""),
            pytest.param({"top_p": -1}, ""),
            pytest.param({"top_p": 10}, ""),
            pytest.param({"top_p": "a"}, ""),
            ({"presence_penalty": 0}, ""),
            ({"presence_penalty": 1}, ""),
            pytest.param({"presence_penalty": -1}, ""),
            pytest.param({"presence_penalty": 10}, ""),
            pytest.param({"presence_penalty": "a"}, ""),
            ({"frequency_penalty": 0}, ""),
            ({"frequency_penalty": 1}, ""),
            pytest.param({"frequency_penalty": -1}, ""),
            pytest.param({"frequency_penalty": 10}, ""),
            pytest.param({"frequency_penalty": "a"}, ""),
            ({"max_token": 0}, ""),
            ({"max_token": 1024}, ""),
            pytest.param({"max_token": -1}, ""),
            pytest.param({"max_token": 10}, ""),
            pytest.param({"max_token": "a"}, ""),
            pytest.param({"unknown": "unknown"}, ""),
        ],
    )
    # Configuration persistence is independent of provider-specific inference validation.
    def test_llm_setting(self, client, add_chunks, llm_setting, expected_message):
        dataset, _, _ = add_chunks

        if expected_message:
            with pytest.raises(Exception) as exception_info:
                client.create_chat(name="llm_test", dataset_ids=[dataset.id], llm_setting=llm_setting or None)
            assert expected_message in str(exception_info.value)
        else:
            chat_assistant = client.create_chat(name="llm_test", dataset_ids=[dataset.id], llm_setting=llm_setting or None)
            chat_assistant = client.get_chat(chat_assistant.id)
            for k, v in llm_setting.items():
                assert getattr(chat_assistant.llm_setting, k) == v

    @pytest.mark.p3
    def test_llm_id(self, client, add_chunks):
        dataset, _, _ = add_chunks
        with pytest.raises(Exception) as exception_info:
            client.create_chat(name="llm_test", dataset_ids=[dataset.id], llm_id="unknown")
        assert "`llm_id` unknown doesn't exist" in str(exception_info.value)

    @pytest.mark.p3
    @pytest.mark.parametrize(
        "prompt_config, expected_message",
        [
            ({"empty_response": "Hello World"}, ""),
            ({"empty_response": ""}, ""),
            ({"empty_response": "!@#$%^&*()"}, ""),
            ({"empty_response": "中文测试"}, ""),
            pytest.param({"empty_response": 123}, ""),
            pytest.param({"empty_response": True}, ""),
            pytest.param({"empty_response": " "}, ""),
            ({"prologue": "Hello World"}, ""),
            ({"prologue": ""}, ""),
            ({"prologue": "!@#$%^&*()"}, ""),
            ({"prologue": "中文测试"}, ""),
            pytest.param({"prologue": 123}, ""),
            pytest.param({"prologue": True}, ""),
            pytest.param({"prologue": " "}, ""),
            ({"quote": True}, ""),
            ({"quote": False}, ""),
            ({"system": "Hello World {knowledge}"}, ""),
            ({"system": "{knowledge}"}, ""),
            ({"system": "!@#$%^&*() {knowledge}"}, ""),
            ({"system": "中文测试 {knowledge}"}, ""),
            ({"system": "Hello World"}, ""),
            ({"system": "Hello World", "parameters": []}, ""),
            pytest.param({"system": 123}, ""),
            pytest.param({"unknown": "unknown"}, ""),
        ],
    )
    # Nested prompt values and provider extension keys round-trip through storage.
    def test_prompt_config(self, client, add_chunks, prompt_config, expected_message):
        dataset, _, _ = add_chunks

        if expected_message:
            with pytest.raises(Exception) as exception_info:
                client.create_chat(name="prompt_test", dataset_ids=[dataset.id], prompt_config=prompt_config)
            assert expected_message in str(exception_info.value)
        else:
            chat_assistant = client.create_chat(name="prompt_test", dataset_ids=[dataset.id], prompt_config=prompt_config)
            chat_assistant = client.get_chat(chat_assistant.id)
            for k, v in prompt_config.items():
                assert getattr(chat_assistant.prompt_config, k) == v


class TestChatAssistantCreate2:
    @pytest.mark.p3
    def test_unparsed_document(self, client, add_document):
        dataset, _ = add_document
        with pytest.raises(Exception) as exception_info:
            client.create_chat(name="prompt_test", dataset_ids=[dataset.id])
        assert "doesn't own parsed file" in str(exception_info.value)
