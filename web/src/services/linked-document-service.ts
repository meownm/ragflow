import api from '@/utils/api';
import request from '@/utils/next-request';
import type { SourceWorkspace } from './source-workbench-service';

export const documentTypes = {
  business_requirements: 'Бизнес-требования',
  technical_specification: 'Техническое задание',
  architecture: 'Архитектура',
  database: 'Описание БД',
  api: 'Спецификация API',
  instruction: 'Инструкция',
  tests: 'Программа испытаний',
  regulation: 'Регламент',
  reference: 'Справочный документ',
  other: 'Другой документ',
} as const;

export type DocumentType = keyof typeof documentTypes;
export type DocumentPolicy = 'working' | 'approval' | 'locked';
export type Section = { id: string; text: string };
export type Dependency = {
  id: string;
  source_id: string;
  target_id: string;
  relation:
    | 'requires'
    | 'constrains'
    | 'details'
    | 'reflects'
    | 'verifies'
    | 'reference';
  required: boolean;
  source_scope: string[];
  target_scope: string[];
  refresh_on: 'saved' | 'approved';
  maintenance: 'manual' | 'auto';
  blocks_approval: boolean;
  reviewed_revision_id: string | null;
  reviewed_target_revision_id?: string | null;
  version: number;
};
export type DocumentSummary = {
  id: string;
  title: string;
  document_type: DocumentType;
  change_policy: DocumentPolicy;
  source_kind: 'local' | 'indexed';
  current_revision_id: string;
  approved_revision_id: string | null;
  responsible_id: string;
  approver_id: string;
  version: number;
  provenance_state: 'none' | 'unresolved' | 'resolved';
};
export type Revision = {
  id: string;
  number: number;
  parent_id: string | null;
  content_hash: string;
  author_id: string;
  created_at: string;
  sections: Section[];
};
export type ChangeOperation = {
  op: 'replace' | 'add' | 'delete';
  section_id: string;
  after_id?: string | null;
  text?: string;
  source_ids: string[];
};
export type SourceInput = {
  dependency_id: string;
  source_id: string;
  source_revision_id: string;
  title: string;
  policy: Dependency;
  sections?: Section[];
  previous_sections?: Section[] | null;
};
export type ChangeSet = {
  id: string;
  origin?: 'model' | 'manual';
  author_id?: string | null;
  author_name?: string;
  created_at?: string;
  candidate_sections?: Section[];
  base_revision_id: string;
  document_version: number;
  inputs: SourceInput[];
  status: 'ready' | 'blocked' | 'applied' | 'rejected';
  decisions?: {
    actor_id: string;
    actor_name: string;
    action: 'accept' | 'reject';
    comment: string;
    created_at: string;
  }[];
  plan: {
    outcome:
      | 'changes_required'
      | 'no_impact'
      | 'conflict'
      | 'insufficient_data';
    summary: string;
    operations: ChangeOperation[];
    source_results: {
      dependency_id: string;
      disposition: string;
      explanation: string;
    }[];
  };
  verification: {
    verdict: 'PASS' | 'FAIL' | 'BLOCKED' | 'HUMAN_REQUIRED';
    issues: string[];
  };
};
export type DocumentJob = {
  id: string;
  status: 'queued' | 'running' | 'done' | 'failed' | 'cancelled';
  stage: string;
  result_id: string | null;
  error: { code: string; message: string } | null;
};
export type WorkspaceDocument = DocumentSummary & {
  capabilities: { edit: boolean; approve: boolean; manage: boolean };
  content: string;
  sections: Section[];
  source_ref: {
    dataset_id: string;
    document_id: string;
    revision: string;
    source_url?: string;
  } | null;
  dependencies: Dependency[];
  impacts: {
    id: string;
    dependency_id: string;
    source_revision_id: string;
    target_revision_id: string;
    state: string;
    explanation: string;
  }[];
  waiting: { dependency_id: string; source_id: string; reason: string }[];
  revisions: Omit<Revision, 'sections'>[];
  changes: ChangeSet[];
  jobs: DocumentJob[];
};
export type WorkspaceMember = {
  user_id: string;
  role: 'owner' | 'editor' | 'reviewer' | 'reader';
  email?: string;
  name?: string;
};

export type WorkspaceOrganization = {
  tenant_id: string | null;
  version: number;
  items: { id: string; name: string }[];
  workspace: SourceWorkspace;
};
export const getWorkspaceOrganization = (workspaceId: string) =>
  get<WorkspaceOrganization>(
    `${api.sourceWorkspace(workspaceId)}/organization`,
  );
export const setWorkspaceOrganization = (
  workspaceId: string,
  tenantId: string,
  version: number,
) =>
  write<WorkspaceOrganization>(
    `${api.sourceWorkspace(workspaceId)}/organization`,
    { tenant_id: tenantId, expected_version: version },
    'put',
  );

type Envelope<T> = { code: number; message?: string; data: T };
const base = (workspaceId: string) =>
  `${api.sourceWorkspace(workspaceId)}/documents`;
const doc = (workspaceId: string, documentId: string) =>
  `${base(workspaceId)}/${documentId}`;

async function get<T>(url: string): Promise<T> {
  const response = await request.get<Envelope<T>>(url);
  if (response.data.code !== 0)
    throw new Error(response.data.message || 'Не удалось получить документ');
  return response.data.data;
}

async function write<T>(
  url: string,
  data: unknown,
  method: 'post' | 'put' = 'post',
): Promise<T> {
  const response = await request[method]<Envelope<T>>(url, data);
  if (response.data.code !== 0)
    throw new Error(response.data.message || 'Не удалось сохранить изменения');
  return response.data.data;
}

export const listWorkspaceDocuments = (workspaceId: string) =>
  get<DocumentSummary[]>(base(workspaceId));
export const getWorkspaceDocument = (workspaceId: string, documentId: string) =>
  get<WorkspaceDocument>(doc(workspaceId, documentId));
export const createWorkspaceDocument = (
  workspaceId: string,
  input: { title: string; document_type: DocumentType; content: string },
) => write<WorkspaceDocument>(base(workspaceId), input);
export const importWorkspaceSource = (
  workspaceId: string,
  input: {
    dataset_id: string;
    document_id: string;
    document_type?: DocumentType;
  },
) => write<WorkspaceDocument>(`${base(workspaceId)}/import`, input);
export const updateWorkspaceDocument = (
  workspaceId: string,
  documentId: string,
  input: Partial<
    Pick<
      WorkspaceDocument,
      | 'title'
      | 'document_type'
      | 'change_policy'
      | 'content'
      | 'responsible_id'
      | 'approver_id'
    >
  > & { expected_version: number },
) => write<WorkspaceDocument>(doc(workspaceId, documentId), input, 'put');
export const saveDocumentDependency = (
  workspaceId: string,
  documentId: string,
  input: Omit<
    Dependency,
    'id' | 'version' | 'target_id' | 'reviewed_revision_id'
  > & { expected_version: number },
  dependencyId?: string,
) =>
  write<WorkspaceDocument>(
    `${doc(workspaceId, documentId)}/dependencies${dependencyId ? `/${dependencyId}` : ''}`,
    input,
    dependencyId ? 'put' : 'post',
  );
export const refreshWorkspaceDocument = (
  workspaceId: string,
  documentId: string,
) => write<WorkspaceDocument>(`${doc(workspaceId, documentId)}/refresh`, {});
export const prepareDocumentChanges = (
  workspaceId: string,
  documentId: string,
  version: number,
  prompt: string,
) =>
  write<DocumentJob>(`${doc(workspaceId, documentId)}/prepare`, {
    expected_version: version,
    prompt,
  });

export const verifyDocumentSelection = (
  workspaceId: string,
  documentId: string,
  changeId: string,
  selectedSections: string[],
) =>
  write<DocumentJob>(
    `${doc(workspaceId, documentId)}/changes/${changeId}/selection`,
    { selected_sections: selectedSections },
  );
export const decideDocumentChange = (
  workspaceId: string,
  documentId: string,
  changeId: string,
  action: 'accept' | 'reject',
  comment: string,
) =>
  write<WorkspaceDocument>(
    `${doc(workspaceId, documentId)}/changes/${changeId}/decision`,
    { action, comment },
  );
export const approveWorkspaceDocument = (
  workspaceId: string,
  documentId: string,
  version: number,
) =>
  write<WorkspaceDocument>(`${doc(workspaceId, documentId)}/approve`, {
    expected_version: version,
  });
export const establishDocumentBaseline = (
  workspaceId: string,
  documentId: string,
  version: number,
) =>
  write<WorkspaceDocument>(`${doc(workspaceId, documentId)}/baseline`, {
    expected_version: version,
    confirm_current_sources: true,
  });
export const cancelDocumentJob = (
  workspaceId: string,
  documentId: string,
  jobId: string,
) =>
  write<DocumentJob>(
    `${doc(workspaceId, documentId)}/jobs/${jobId}/cancel`,
    {},
  );
export const getDocumentRevision = (
  workspaceId: string,
  documentId: string,
  revisionId: string,
) => get<Revision>(`${doc(workspaceId, documentId)}/revisions/${revisionId}`);
export const listWorkspaceMembers = (workspaceId: string) =>
  get<WorkspaceMember[]>(`${api.sourceWorkspace(workspaceId)}/members`);
export const setWorkspaceMember = (
  workspaceId: string,
  input: { email: string; role: WorkspaceMember['role'] },
) =>
  write<WorkspaceMember[]>(
    `${api.sourceWorkspace(workspaceId)}/members`,
    input,
    'put',
  );

export const configureWorkspaceSearch = (
  workspace: SourceWorkspace,
  datasetIds: string[],
) =>
  write<SourceWorkspace>(
    `${api.sourceWorkspace(workspace.id)}/datasets`,
    { dataset_ids: datasetIds, expected_version: workspace.version },
    'put',
  );

export const removeDocumentDependency = async (
  workspaceId: string,
  documentId: string,
  dependencyId: string,
  version: number,
): Promise<WorkspaceDocument> => {
  const response = await request.delete<Envelope<WorkspaceDocument>>(
    `${doc(workspaceId, documentId)}/dependencies/${dependencyId}`,
    { data: { expected_version: version } },
  );
  if (response.data.code !== 0)
    throw new Error(response.data.message || 'Не удалось удалить связь');
  return response.data.data;
};
