"""Explicit composition for HTTP and worker linked-document scenarios."""

from .adapters import RAGFlowAnswerGateway, RAGFlowSourceGateway
from .linked_application import LinkedDocuments
from .linked_storage import LinkedDocumentRepository


def build_linked_documents():
    from api.apps.business_documents.runtime import document_queries
    from .business_source import BusinessDocumentSources

    business = BusinessDocumentSources(document_queries)
    return LinkedDocuments(LinkedDocumentRepository(business), RAGFlowSourceGateway(), RAGFlowAnswerGateway(), business)
