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

"""HTTP boundary for governed business requirements documents."""

from urllib.parse import quote

from quart import Response, jsonify, request
from werkzeug.exceptions import BadRequest

from api.apps import current_user, login_required
from api.apps.business_documents.errors import BusinessDocumentError
from api.apps.business_documents.exports import BusinessDocumentExportService
from api.apps.business_documents.service import BusinessDocumentService
from api.apps.business_documents.worker import wake_business_document_worker
from api.utils.api_utils import get_request_json
from common.misc_utils import thread_pool_exec


def _success(data, status=200):
    return jsonify({"code": 0, "data": data}), status


def _error(error: BusinessDocumentError):
    payload = {
        "code": error.status,
        "message": error.message,
        "data": {"error_code": error.code, "details": error.details},
    }
    return jsonify(payload), error.status


@manager.route("/business-documents", methods=["POST"])  # noqa: F821
@login_required
async def create_business_document():
    try:
        data = await get_request_json()
        if not data:
            raise BusinessDocumentError("INVALID_DOCUMENT", "Request body must be a valid JSON object", 422)
        actor_id = current_user.id
        result = await thread_pool_exec(BusinessDocumentService.create_document, actor_id, actor_id, data)
        return _success(result, 201)
    except (AttributeError, TypeError, BadRequest):
        return _error(BusinessDocumentError("INVALID_DOCUMENT", "Request body must be a valid JSON object", 422))
    except BusinessDocumentError as error:
        return _error(error)


@manager.route("/business-documents", methods=["GET"])  # noqa: F821
@login_required
async def list_business_documents():
    try:
        actor_id = current_user.id
        page = int(request.args.get("page", 1))
        page_size = int(request.args.get("page_size", 20))
        return _success(await thread_pool_exec(BusinessDocumentService.list_documents, actor_id, actor_id, page, page_size))
    except (TypeError, ValueError):
        return _error(BusinessDocumentError("INVALID_PAGINATION", "page and page_size must be integers", 422))
    except BusinessDocumentError as error:
        return _error(error)


@manager.route("/business-documents/<document_id>", methods=["GET"])  # noqa: F821
@login_required
async def get_business_document(document_id):
    try:
        tenant_id = current_user.id
        return _success(await thread_pool_exec(BusinessDocumentService.get_document, tenant_id, document_id, tenant_id))
    except BusinessDocumentError as error:
        return _error(error)


@manager.route("/business-documents/<document_id>/commands", methods=["POST"])  # noqa: F821
@login_required
async def execute_business_document_command(document_id):
    try:
        data = await get_request_json()
        if not data:
            raise BusinessDocumentError("INVALID_COMMAND_REQUEST", "Request body must be a valid JSON object", 422)
        actor_id = current_user.id
        result = await thread_pool_exec(BusinessDocumentService.execute_command, actor_id, actor_id, document_id, data)
        if result.get("job_id"):
            wake_business_document_worker()
        return _success(result, 202 if result.get("job_id") else 200)
    except (AttributeError, TypeError, BadRequest):
        return _error(BusinessDocumentError("INVALID_COMMAND_REQUEST", "Request body must be a valid JSON object", 422))
    except BusinessDocumentError as error:
        return _error(error)


@manager.route("/business-documents/<document_id>/revisions", methods=["GET"])  # noqa: F821
@login_required
async def list_business_document_revisions(document_id):
    try:
        tenant_id = current_user.id
        return _success(await thread_pool_exec(BusinessDocumentService.list_revisions, tenant_id, document_id, tenant_id))
    except BusinessDocumentError as error:
        return _error(error)


@manager.route("/business-documents/<document_id>/revisions/<revision_id>", methods=["GET"])  # noqa: F821
@login_required
async def get_business_document_revision(document_id, revision_id):
    try:
        tenant_id = current_user.id
        return _success(await thread_pool_exec(BusinessDocumentService.get_revision, tenant_id, document_id, revision_id, tenant_id))
    except BusinessDocumentError as error:
        return _error(error)


@manager.route("/business-documents/<document_id>/jobs", methods=["GET"])  # noqa: F821
@login_required
async def list_business_document_jobs(document_id):
    try:
        actor_id = current_user.id
        return _success(await thread_pool_exec(BusinessDocumentService.list_jobs, actor_id, actor_id, document_id))
    except BusinessDocumentError as error:
        return _error(error)


@manager.route("/business-documents/<document_id>/exports", methods=["GET"])  # noqa: F821
@login_required
async def list_business_document_exports(document_id):
    try:
        actor_id = current_user.id
        return _success(await thread_pool_exec(BusinessDocumentExportService.list_artifacts, actor_id, actor_id, document_id))
    except BusinessDocumentError as error:
        return _error(error)


@manager.route("/business-documents/<document_id>/exports/<artifact_id>/download", methods=["GET"])  # noqa: F821
@login_required
async def download_business_document_export(document_id, artifact_id):
    try:
        actor_id = current_user.id
        artifact, content = await thread_pool_exec(
            BusinessDocumentExportService.download,
            actor_id,
            actor_id,
            document_id,
            artifact_id,
        )
        response = Response(content, content_type=artifact["mime_type"])
        response.headers["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(artifact['filename'])}"
        response.headers["Content-Length"] = str(artifact["size"])
        response.headers["ETag"] = f'"{artifact["content_hash"].removeprefix("sha256:")}"'
        return response
    except BusinessDocumentError as error:
        return _error(error)
