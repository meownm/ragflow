"""Transactional projection of existing product links into the shared registry."""

from api.db.db_models import BusinessDocument, BusinessDocumentEvaBinding, DocumentExchangeBinding, TechnicalSpecification

from .bindings import BindingConflict, reserve
from document_exchange.identity import binding_keys


def migrate_bindings():
    database = DocumentExchangeBinding._meta.database
    with database.atomic():
        rows = BusinessDocumentEvaBinding.select(BusinessDocumentEvaBinding, BusinessDocument.tenant_id).join(BusinessDocument, on=(BusinessDocument.id == BusinessDocumentEvaBinding.document_id))
        for row in rows.iterator():
            page, identity = binding_keys(row.binding)
            if row.page_url_key != page or row.eva_identity_key != identity:
                BusinessDocumentEvaBinding.update(page_url_key=page, eva_identity_key=identity).where(BusinessDocumentEvaBinding.document_id == row.document_id).execute()
            _reserve(row.businessdocument.tenant_id, "business_requirements", row.document_id, row.binding)
        for row in TechnicalSpecification.select().where(TechnicalSpecification.origin_key.is_null(False)).iterator():
            binding = row.payload.get("eva")
            if not isinstance(binding, dict) or binding_keys(binding)[1] != row.origin_key:
                raise RuntimeError(f"Unconfirmed Eva binding for technical specification {row.id}")
            _reserve(row.tenant_id, "technical_specification", row.id, binding)


def _reserve(scope, kind, document_id, binding):
    try:
        reserve(scope, kind, document_id, binding)
    except BindingConflict as error:
        raise RuntimeError(f"Eva page has multiple owners; reconcile {kind} {document_id} before upgrade") from error
