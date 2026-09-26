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

from __future__ import annotations

import json
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any

from business_documents.application.errors import ValidationError
from business_documents.domain import content
from business_documents.domain.errors import RuleViolation
from business_documents.domain.hashing import text_hash


_ASSET_ROOT = Path(__file__).resolve().parents[3] / "agent" / "business_requirements"
_CONTRACT_FILES = {
    "create_document": "create_document.v1.schema.json",
    "create_document_v2": "create_document.v2.schema.json",
    "create_document_v3": "create_document.v3.schema.json",
    "command": "command.v1.schema.json",
    "question_batch": "question_batch.v1.schema.json",
    "document_draft": "document_draft.v1.schema.json",
    "change_plan": "change_plan.v1.schema.json",
    "review_plan": "review_plan.v1.schema.json",
    "eva_change_draft": "eva_change_draft.v1.schema.json",
    "sql_schema_interpretation": "sql_schema_interpretation.v1.schema.json",
    "sql_query_plan": "sql_query_plan.v1.schema.json",
    "sql_requirements_proposal": "sql_requirements_proposal.v1.schema.json",
}
_PROMPT_FILES = {
    "intake": ("intake.v1.md", "1"),
    "review": ("review.v1.md", "1"),
    "draft": ("draft.v1.md", "1"),
    "change_planner": ("change_planner.v1.md", "1"),
    "eva_change": ("eva_change.v1.md", "1"),
    "sql_schema_interpreter": ("sql_schema_interpreter.v1.md", "1"),
    "sql_query_planner": ("sql_query_planner.v1.md", "1"),
    "sql_requirements_analyst": ("sql_requirements_analyst.v1.md", "1"),
}
_JOB_PROMPTS = {
    "ASSESS_INTAKE": "intake",
    "ASSESS_REVIEW": "review",
    "GENERATE_DRAFT": "draft",
    "PLAN_CHANGES": "change_planner",
    "GENERATE_EVA_CHANGE": "eva_change",
    "RESOLVE_SQL_SCHEMA": "sql_schema_interpreter",
    "PLAN_SQL_QUERY": "sql_query_planner",
    "ANALYZE_SQL_REQUIREMENTS": "sql_requirements_analyst",
}


@lru_cache(maxsize=None)
def _load_json(relative_path: str) -> dict[str, Any]:
    path = _ASSET_ROOT / relative_path
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Business requirements asset is unavailable or invalid: {path}") from exc


def process_policy() -> dict[str, Any]:
    return _load_json("policies/process.v1.json")


def rendering_policy() -> dict[str, Any]:
    return _load_json("policies/rendering.v1.json")


def published_template() -> dict[str, Any]:
    template = _load_json("templates/business_requirements.v1.json")
    if template.get("status") != "PUBLISHED":
        raise RuntimeError("Business requirements template must be published")
    return template


def contract_schema(name: str) -> dict[str, Any]:
    try:
        filename = _CONTRACT_FILES[name]
    except KeyError as exc:
        raise RuntimeError(f"Unknown business requirements contract: {name}") from exc
    return _load_json(f"contracts/{filename}")


@lru_cache(maxsize=None)
def prompt_text(name: str) -> str:
    try:
        filename, _ = _PROMPT_FILES[name]
    except KeyError as exc:
        raise RuntimeError(f"Unknown business requirements prompt: {name}") from exc
    path = _ASSET_ROOT / "prompts" / filename
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"Business requirements prompt is unavailable: {path}") from exc


def prompt_descriptor(job_type: str) -> dict[str, str] | None:
    name = _JOB_PROMPTS.get(job_type)
    if name is None:
        return None
    _, version = _PROMPT_FILES[name]
    content = prompt_text(name)
    return {
        "name": name,
        "version": version,
        "content_hash": text_hash(content),
    }


@contextmanager
def _validation_errors():
    """Translate pure content failures at the versioned asset boundary."""
    try:
        yield
    except RuleViolation as error:
        raise ValidationError(error.code, error.message, error.details) from error


def validate_contract(name: str, value: object) -> None:
    with _validation_errors():
        content.validate_contract(name, value, contract_schema(name))


def validate_document_ast(document: object) -> dict[str, Any]:
    with _validation_errors():
        return content.validate_document_ast(document, published_template(), contract_schema("document_draft"))


def import_document_markdown(markdown: object) -> dict[str, Any]:
    with _validation_errors():
        return content.import_document_markdown(markdown, published_template(), contract_schema("document_draft"))


def apply_change_plan(base_document: dict[str, Any], change_plan: dict[str, Any]) -> dict[str, Any]:
    with _validation_errors():
        return content.apply_change_plan(base_document, change_plan, published_template(), contract_schema("document_draft"))


def render_document_ast(document: dict[str, Any]) -> str:
    return content.render_document_ast(document, published_template())
