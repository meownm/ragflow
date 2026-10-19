"""Read-only adapter to the Business Documents approved-source contract."""

from contextlib import nullcontext

from api.db.db_models import User
from business_documents.application.errors import BusinessDocumentError
from .linked_domain import digest, fail


class BusinessDocumentSources:
    def __init__(self, queries):
        self.queries = queries

    def _actor(self, actor):
        user = User.get_or_none((User.id == actor) & (User.status == "1") & (User.is_active == "1"))
        if user is None:
            fail("PERMISSION_DENIED", "Доступ к источникам отозван", 403)
        return user.business_document_role, bool(user.is_superuser)

    def read_many(self, actor, ids, *, body=False, lock=False):
        database = User._meta.database
        with database.connection_context() if database.is_closed() else nullcontext():
            role, admin = self._actor(actor)
            try:
                rows = self.queries.agreed_sources(actor, sorted(set(ids)), role, admin, include_body=body, lock=lock)
            except BusinessDocumentError as error:
                fail(error.code, error.message, error.status)
        return {row["document_id"]: {**row, "revision": digest([row["revision_id"], row["agreement_event_id"]]), "source_url": "/business-documents/" + row["document_id"]} for row in rows}

    def validate(self, actor, references, *, lock=False, fresh=False):
        rows = self.read_many(actor, [reference["document_id"] for reference in references], lock=lock)
        for reference in references:
            current = rows.get(reference["document_id"])
            if current is None:
                fail("DOCUMENT_UNAVAILABLE", "Согласованный источник Business Documents недоступен", 409)
            if fresh and current["revision"] != reference["revision"]:
                fail("SOURCE_CHANGED", "Согласованная редакция Business Documents изменилась", 409)
        return rows

    def list(self, actor, query="", page=1):
        database = User._meta.database
        with database.connection_context() if database.is_closed() else nullcontext():
            role, admin = self._actor(actor)
            try:
                return self.queries.list_agreed_sources(actor, query, page, role, admin)
            except BusinessDocumentError as error:
                fail(error.code, error.message, error.status)
