"""Read-only role persistence, document mutations, and EVA HTTP adapter guards."""

import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from test.unit_test.api.apps.business_documents.test_business_document_service import (
    AUTHOR,
    TENANT,
    _command,
    _create,
    database as database,
)
from api.apps.business_documents.authorization import BusinessDocumentAccess
from api.apps.business_documents.runtime import change_user_role, document_commands, document_creation, document_deletion, document_queries, eva_synchronization
from api.db.db_models import BusinessDocument, BusinessDocumentCommand, BusinessDocumentEvent, BusinessDocumentJob, User
from business_documents.application.errors import BusinessDocumentError


def _counts():
    return [model.select().count() for model in (BusinessDocument, BusinessDocumentCommand, BusinessDocumentEvent, BusinessDocumentJob)]


@pytest.mark.usefixtures("database")
def test_reader_role_is_persisted_and_blocks_own_document_mutations():
    document = _create()
    foreign = document_creation.execute("other-tenant", "other-author", {"schema_version": "1", "document_type": "business_requirements", "title": "Other document", "idea": "Read only"})
    User.create(id=AUTHOR, nickname="Reader", email="reader@example.test")
    result = change_user_role.execute(AUTHOR, {"role": "READER"}, is_admin=True)
    assert result["role"] == "READER"
    assert User.get_by_id(AUTHOR).business_document_role == "READER"

    listing = document_queries.list_documents(AUTHOR, access_role="READER")
    assert {row["document_id"] for row in listing["items"]} == {document["document_id"], foreign["document_id"]}
    assert listing["access_role"] == "READER"
    assert listing["capabilities"] == {"read": True, "create": False, "edit_own": False, "edit_all": False, "delete": False, "assign": False}
    for row in listing["items"]:
        assert row["permissions"] == {"read": True, "edit": False, "delete": False, "assign": False}
        projection = document_queries.get_document(row["document_id"], AUTHOR, access_role="READER")
        assert projection["allowed_commands"] == []
        assert projection["permissions"] == row["permissions"]

    before = _counts()
    actions = [
        lambda: document_creation.execute(TENANT, AUTHOR, {}, access_role="READER"),
        lambda: document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(document, "REQUEST_INTAKE_ASSESSMENT"), access_role="READER"),
        lambda: document_deletion.execute(AUTHOR, document["document_id"], access_role="READER"),
        lambda: document_queries.list_access_users(AUTHOR, access_role="READER"),
        lambda: change_user_role.execute(AUTHOR, {"role": "AUTHOR_CREATOR"}),
        lambda: eva_synchronization.pull_from_eva(TENANT, AUTHOR, document["document_id"], {"expected_state_version": document["state_version"]}, access_role="READER"),
        lambda: eva_synchronization.rebind_eva(TENANT, AUTHOR, document["document_id"], {"expected_state_version": document["state_version"]}, access_role="READER"),
        lambda: eva_synchronization.create_eva_change_from_revision(TENANT, AUTHOR, document["document_id"], {"expected_state_version": document["state_version"]}, access_role="READER"),
    ]
    for action in actions:
        with pytest.raises(BusinessDocumentError) as denied:
            action()
        assert denied.value.status == 403
        assert denied.value.code == "DOCUMENT_PERMISSION_DENIED"
        assert _counts() == before


@pytest.mark.usefixtures("database")
def test_role_downgrade_rejects_command_receipt_replay():
    document = _create()
    command = _command(document, "REQUEST_INTAKE_ASSESSMENT")
    document_commands.execute(TENANT, AUTHOR, document["document_id"], command)
    before = _counts()
    with pytest.raises(BusinessDocumentError) as denied:
        document_commands.execute(TENANT, AUTHOR, document["document_id"], command, access_role="READER")
    assert denied.value.status == 403
    assert _counts() == before


def test_reader_is_rejected_by_sql_mutation_guards():
    access = BusinessDocumentAccess(AUTHOR, "READER")
    for require in (access.require_create, lambda: access.require_edit(AUTHOR), access.require_execute_sql, access.require_delete, access.require_assign):
        with pytest.raises(BusinessDocumentError) as denied:
            require()
        assert denied.value.status == 403


def _eva_handler(name, thread_pool_exec):
    path = Path(__file__).parents[5] / "api" / "apps" / "restful_apis" / "business_document_api.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    function = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == name)
    function.decorator_list = []
    namespace = {
        "current_user": SimpleNamespace(id=AUTHOR),
        "_access_role": lambda: "READER",
        "_is_admin": lambda: False,
        "BusinessDocumentAccess": BusinessDocumentAccess,
        "BusinessDocumentError": BusinessDocumentError,
        "thread_pool_exec": thread_pool_exec,
        "get_request_json": AsyncMock(return_value={}),
        "BadRequest": type("BadRequest", (Exception,), {}),
        "EvaDocumentChangeService": SimpleNamespace(get_change=lambda *_args: None),
        "_error": lambda error: ({"code": error.code}, error.status),
        "_success": lambda result: result,
    }
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec"), namespace)
    return namespace[name]


@pytest.mark.parametrize(
    "name",
    [
        "create_eva_business_document_change",
        "generate_eva_business_document_change_draft",
        "approve_eva_business_document_change",
        "prepare_eva_business_document_change",
        "publish_eva_business_document_change",
    ],
)
async def test_reader_eva_http_mutations_return_403_before_io(name):
    execute = AsyncMock()
    handler = _eva_handler(name, execute)
    kwargs = {} if name == "create_eva_business_document_change" else {"change_id": "own-change"}
    payload, status = await handler(**kwargs)
    assert status == 403
    assert payload["code"] == "DOCUMENT_PERMISSION_DENIED"
    execute.assert_not_awaited()


async def test_reader_can_view_an_eva_change_without_mutation_actions():
    execute = AsyncMock(return_value={"change_id": "own-change", "allowed_actions": ["GENERATE_DRAFT", "APPROVE", "PUBLISH_EVA"]})
    result = await _eva_handler("get_eva_business_document_change", execute)("own-change")
    assert result["change_id"] == "own-change"
    assert result["allowed_actions"] == []
