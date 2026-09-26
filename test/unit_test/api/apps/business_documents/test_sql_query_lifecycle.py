#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#

from __future__ import annotations

from pathlib import Path
import sys
from types import ModuleType

import pytest


if "api.apps" not in sys.modules:
    api_apps = ModuleType("api.apps")
    api_apps.__path__ = [str(Path(__file__).resolve().parents[5] / "api" / "apps")]
    sys.modules["api.apps"] = api_apps

from api.apps.business_documents import sql_query_lifecycle as lifecycle_module
from business_documents.application.errors import PermissionDeniedError, ValidationError
from api.apps.business_documents.sql_query_lifecycle import BusinessDocumentSqlQueryService
from business_documents.sql_query.query_specification import QuerySpecificationValidationError, SqlGuardError


def test_compile_checks_create_permission_before_reading_the_payload(monkeypatch):
    called = False

    def compile_payload(_payload):
        nonlocal called
        called = True
        return {"status": "READY"}

    monkeypatch.setattr(lifecycle_module, "compile_query_payload", compile_payload)

    with pytest.raises(PermissionDeniedError):
        BusinessDocumentSqlQueryService.compile("actor-1", {}, False, "AUTHOR_EDITOR")

    assert called is False


def test_compile_authorizes_creator_and_delegates_to_pure_owner(monkeypatch):
    payload = {"schema_version": "1"}
    monkeypatch.setattr(
        lifecycle_module,
        "compile_query_payload",
        lambda received: {"status": "READY", "received": received},
    )

    result = BusinessDocumentSqlQueryService.compile("actor-1", payload, False, "AUTHOR_CREATOR")

    assert result == {"status": "READY", "received": payload}


@pytest.mark.parametrize(
    ("error", "expected_code"),
    [
        (QuerySpecificationValidationError("bad contract"), "INVALID_SQL_QUERY_SPECIFICATION"),
        (SqlGuardError("unsafe"), "SQL_QUERY_BLOCKED"),
    ],
)
def test_compile_maps_owned_validation_errors(monkeypatch, error, expected_code):
    def reject(_payload):
        raise error

    monkeypatch.setattr(lifecycle_module, "compile_query_payload", reject)

    with pytest.raises(ValidationError) as raised:
        BusinessDocumentSqlQueryService.compile("actor-1", {}, False, "AUTHOR_CREATOR")

    assert raised.value.code == expected_code
