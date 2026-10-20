"""Composition root; the application does not import infrastructure."""

from api.apps.business_documents.eva_changes import EvaDocumentChangeService
from common.misc_utils import thread_pool_exec

from .ai import SpecificationAI
from .application import Specifications
from .storage import SpecificationRepository
from .alignment_ai import AlignmentAI
from .alignment_application import AlignmentApplication


def build_specifications():
    from api.apps.business_documents.runtime import document_queries
    from api.source_workbench.business_source import BusinessDocumentSources

    repository = SpecificationRepository()
    alignment = AlignmentApplication(repository, BusinessDocumentSources(document_queries), AlignmentAI(), thread_pool_exec)
    return Specifications(repository, SpecificationAI(), thread_pool_exec, EvaDocumentChangeService.pages(), alignment)
