export interface EvaPage {
  id: string;
  name: string;
  code: string;
  project_id: string;
  version: string;
  web_url: string;
  hierarchy?: string;
  connector_name?: string;
  binding_available: boolean;
  linked_document: { id: string; workspace_id: string } | null;
}
