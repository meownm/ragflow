"""Compose Business Documents scenarios at the API/worker runtime boundary."""

from api.apps.business_documents import assets, evidence
from api.apps.business_documents.evidence import related_file_search_enabled
from api.apps.business_documents.exports import BusinessDocumentExportService
from api.apps.business_documents.adapters.queries import PeeweeDocumentReader
from api.apps.business_documents.adapters.persistence import PeeweeDocumentWriter
from api.apps.business_documents.adapters.cleanup import ExportCleanup
from api.apps.business_documents.adapters.access import PeeweeUserRoleWriter
from api.apps.business_documents.adapters.eva import EvaPageSource
from business_documents.application.change_application import ApplyChanges
from business_documents.application.commands import DocumentCommands
from business_documents.application.documents import CreateDocument, DeleteDocument
from business_documents.application.access import ChangeUserRole
from business_documents.application.eva_sync import EvaSynchronization
from business_documents.application.job_completion import JobCompletion
from business_documents.application.queries import DocumentQueries
from common.misc_utils import get_uuid
from common.time_utils import current_timestamp


document_reader = PeeweeDocumentReader()
document_queries = DocumentQueries(document_reader, assets, current_timestamp)
document_writer = PeeweeDocumentWriter()
eva_synchronization = EvaSynchronization(document_reader, document_writer, EvaPageSource(), document_queries, get_uuid, current_timestamp)
document_creation = CreateDocument(document_writer, assets, evidence, EvaPageSource(), document_queries, get_uuid, current_timestamp)
document_deletion = DeleteDocument(document_writer, ExportCleanup())
change_user_role = ChangeUserRole(PeeweeUserRoleWriter())
apply_changes = ApplyChanges(document_writer, assets, get_uuid)
document_commands = DocumentCommands(document_writer, assets, document_queries, apply_changes, get_uuid, current_timestamp)
job_completion = JobCompletion(
    document_writer,
    assets,
    get_uuid,
    clock=current_timestamp,
    queries=document_queries,
    changes=apply_changes,
    exports=BusinessDocumentExportService,
    retrieval_enabled=related_file_search_enabled,
)
