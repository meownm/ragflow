"""Explicit composition for HTTP and worker linked-document scenarios."""

from .adapters import RAGFlowAnswerGateway, RAGFlowSourceGateway
from .linked_application import LinkedDocuments
from .linked_storage import LinkedDocumentRepository


def build_linked_documents():
    return LinkedDocuments(LinkedDocumentRepository(), RAGFlowSourceGateway(), RAGFlowAnswerGateway())
