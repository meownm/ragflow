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
from types import SimpleNamespace

from rag.app import audio


def test_video_is_sent_to_speech_to_text_model(monkeypatch):
    calls = []

    class FakeLLMBundle:
        def __init__(self, tenant_id, model_config, lang):
            calls.append((tenant_id, model_config, lang))

        def transcription(self, path):
            calls.append(path)
            return "video transcript"

    monkeypatch.setattr(audio, "get_tenant_default_model_by_type", lambda *_args: SimpleNamespace(id="asr-model"))
    monkeypatch.setattr(audio, "LLMBundle", FakeLLMBundle)

    chunks = audio.chunk("meeting.mp4", b"video bytes", "tenant-1", "English", callback=lambda *_args, **_kwargs: None)

    assert chunks[0]["content_with_weight"] == "video transcript"
    assert calls[0][0] == "tenant-1"
    assert calls[1].endswith(".mp4")
