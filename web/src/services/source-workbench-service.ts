import api from '@/utils/api';
import request from '@/utils/next-request';

export interface SourceReference {
  dataset_id: string;
  document_id: string;
  revision?: string;
}

export interface SourceCandidate extends SourceReference {
  title: string;
  path: string[];
  source_type: 'eva_wiki' | 'dataset';
  source_url: string | null;
  content_hash: string;
  excerpts: string[];
  citation_numbers?: number[];
}

export interface SourceWorkspace {
  id: string;
  title: string;
  dataset_ids: string[];
  selected_documents: SourceReference[];
  selected_sources?: SourceCandidate[];
  search_queries: string[];
  version: number;
  created_at: string;
  updated_at: string;
}

export interface SourceChatMessage {
  role: 'user' | 'assistant';
  content: string;
}

export interface SourceChatAnswer {
  answer: string;
  sources: SourceCandidate[];
  version: number;
}

interface Envelope<T> {
  code: number;
  message?: string;
  data: T;
}

export function sourceRequestError(cause: unknown, fallback: string): string {
  const response = (
    cause as { response?: { data?: { message?: unknown } } } | null
  )?.response;
  if (typeof response?.data?.message === 'string' && response.data.message) {
    return response.data.message;
  }
  return cause instanceof Error ? cause.message : fallback;
}

function unwrap<T>(envelope: Envelope<T>): T {
  if (envelope.code !== 0) {
    throw new Error(envelope.message || 'Не удалось выполнить запрос');
  }
  return envelope.data;
}

export async function listSourceWorkspaces(): Promise<SourceWorkspace[]> {
  const response = await request.get<Envelope<SourceWorkspace[]>>(
    api.sourceWorkspaces,
  );
  return unwrap(response.data);
}

export async function createSourceWorkspace(input: {
  title: string;
  dataset_ids: string[];
}): Promise<SourceWorkspace> {
  const response = await request.post<Envelope<SourceWorkspace>>(
    api.sourceWorkspaces,
    input,
  );
  return unwrap(response.data);
}

export async function getSourceWorkspace(id: string): Promise<SourceWorkspace> {
  const response = await request.get<Envelope<SourceWorkspace>>(
    api.sourceWorkspace(id),
  );
  return unwrap(response.data);
}

export async function searchSourceWorkspace(
  id: string,
  query: string,
  page = 1,
): Promise<{
  query: string;
  page: number;
  has_more: boolean;
  candidates: SourceCandidate[];
}> {
  const response = await request.post<
    Envelope<{
      query: string;
      page: number;
      has_more: boolean;
      candidates: SourceCandidate[];
    }>
  >(api.sourceWorkspaceSearch(id), { query, page });
  return unwrap(response.data);
}

export async function saveSourceSelection(
  workspace: SourceWorkspace,
  selected_documents: SourceReference[],
): Promise<SourceWorkspace> {
  const response = await request.put<Envelope<SourceWorkspace>>(
    api.sourceWorkspaceSelection(workspace.id),
    { expected_version: workspace.version, selected_documents },
  );
  return unwrap(response.data);
}

export async function chatWithSourceWorkspace(
  workspace: SourceWorkspace,
  question: string,
  previous_question: string,
): Promise<SourceChatAnswer> {
  const response = await request.post<Envelope<SourceChatAnswer>>(
    api.sourceWorkspaceChat(workspace.id),
    { question, previous_question, expected_version: workspace.version },
  );
  return unwrap(response.data);
}
