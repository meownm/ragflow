"""Composition root; the application does not import infrastructure."""

from api.apps.business_documents.eva_changes import EvaDocumentChangeService
from common.misc_utils import thread_pool_exec

from .ai import SpecificationAI
from .application import Specifications
from .storage import SpecificationRepository


def build_specifications():
    return Specifications(SpecificationRepository(), SpecificationAI(), thread_pool_exec, EvaDocumentChangeService.pages())
