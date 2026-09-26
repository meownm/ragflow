"""Use the existing EVA component through value-only page operations."""


class EvaPageSource:
    def find_title_matches(self, actor_id, title):
        from api.apps.business_documents.eva_changes import EvaDocumentChangeService

        return EvaDocumentChangeService.find_title_matches(actor_id, title)

    def resolve_page_url(self, actor_id, page_url):
        from api.apps.business_documents.eva_changes import EvaDocumentChangeService

        return EvaDocumentChangeService.resolve_page_url(actor_id, page_url)

    def read_connected_page(self, actor_id, binding):
        from api.apps.business_documents.eva_changes import EvaDocumentChangeService

        return EvaDocumentChangeService.read_connected_page(actor_id, binding)

    def create_change(self, tenant_id, actor_id, values):
        from api.apps.business_documents.eva_changes import EvaDocumentChangeService

        return EvaDocumentChangeService.create_change(tenant_id, actor_id, values, allow_prefilled_draft=True)
