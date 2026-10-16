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

"""Shared setup for RAGFlow unit tests.

Several parsers and the chunking pipeline tokenize text with NLTK, which needs
the ``punkt_tab`` and ``wordnet`` data sets. Production provisions these via
``download_deps.py`` (into ``nltk_data``, exported as ``NLTK_DATA`` by
``docker/launch_backend_service.sh``) and ``api.validation`` at startup, but the
unit-test runner has neither. Without the data, tokenizer-backed tests such as
``test_epub_parser`` and ``test_dataflow_service`` fail with
``LookupError: Resource 'punkt_tab' not found``. Make sure the data is reachable
before any test imports a tokenizer: reuse a provisioned ``nltk_data`` directory
when present, and download only what is still missing.
"""

import asyncio
import os

import nltk
import pytest


@pytest.fixture(scope="session")
def event_loop_policy():
    """Own the test policy without implicitly creating a previous policy loop.

    On Python 3.13, pytest-asyncio's previous-loop probe otherwise creates a
    default loop that no runner owns or closes. Install an explicitly empty
    policy before that probe, and restore the caller's policy at session end.
    """
    previous = asyncio.get_event_loop_policy()
    policy = asyncio.DefaultEventLoopPolicy()
    policy.set_event_loop(None)
    asyncio.set_event_loop_policy(policy)
    try:
        yield policy
    finally:
        asyncio.set_event_loop_policy(previous)


# Reuse data already fetched by download_deps.py (the directory the app exports
# as NLTK_DATA) so provisioned environments do not download it again.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
_LOCAL_NLTK_DATA = os.path.join(_REPO_ROOT, "ragflow_deps", "nltk_data")
if os.path.isdir(_LOCAL_NLTK_DATA) and _LOCAL_NLTK_DATA not in nltk.data.path:
    nltk.data.path.insert(0, _LOCAL_NLTK_DATA)

# (download name, resource path used by nltk.data.find)
_REQUIRED_NLTK_DATA = (
    ("punkt_tab", "tokenizers/punkt_tab"),
    ("wordnet", "corpora/wordnet"),
)
for _name, _find_path in _REQUIRED_NLTK_DATA:
    try:
        nltk.data.find(_find_path)
    except LookupError:
        nltk.download(_name, quiet=True)
