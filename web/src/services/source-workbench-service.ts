import { Authorization } from '@/constants/authorization';
import api from '@/utils/api';
import { getAuthorization } from '@/utils/authorization-util';
import request from '@/utils/next-request';
import { EventSourceParserStream } from 'eventsource-parser/stream';

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
  similarity?: number;
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

export interface SourceWorkspaceDraft {
  id: string;
  workspace_id: string;
  content: string;
  prompt: string;
  mode: 'all' | 'sequential';
  source_version: number;
  sources: SourceReference[];
  version: number;
  created_at: string;
  updated_at: string;
}

export type SourceWorkspaceDraftSummary = Omit<SourceWorkspaceDraft, 'content'>;

export interface SourceChatMessage {
  role: 'user' | 'assistant';
  content: string;
}

export interface SourceChatAnswer {
  answer: string;
  sources: SourceCandidate[];
  version: number;
}

export type SourceProcessEvent =
  | {
      event: 'status';
      stage: string;
      message: string;
      current: number;
      total: number;
      context_tokens?: number;
      input_tokens?: number;
      output_tokens?: number;
      context_assumed?: boolean;
      strategy?: string;
    }
  | { event: 'delta'; text: string; article?: number }
  | {
      event: 'step_done';
      text: string;
      article: number;
      processed: number;
      total: number;
      strategy: string;
      version: number;
    }
  | {
      event: 'done';
      text: string;
      processed: number;
      total: number;
      version: number;
    }
  | { event: 'error'; code: string; message: string };

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

export async function listSourceWorkspaceDrafts(
  workspaceId: string,
): Promise<SourceWorkspaceDraftSummary[]> {
  const response = await request.get<Envelope<SourceWorkspaceDraftSummary[]>>(
    api.sourceWorkspaceDrafts(workspaceId),
  );
  return unwrap(response.data);
}

export async function getSourceWorkspaceDraft(
  workspaceId: string,
  draftId: string,
): Promise<SourceWorkspaceDraft> {
  const response = await request.get<Envelope<SourceWorkspaceDraft>>(
    api.sourceWorkspaceDraft(workspaceId, draftId),
  );
  return unwrap(response.data);
}

export async function saveSourceWorkspaceDraft(
  workspace: SourceWorkspace,
  input: { content: string; prompt: string; mode: 'all' | 'sequential' },
): Promise<SourceWorkspaceDraft> {
  const response = await request.post<Envelope<SourceWorkspaceDraft>>(
    api.sourceWorkspaceDrafts(workspace.id),
    { ...input, expected_version: workspace.version },
  );
  return unwrap(response.data);
}

export async function updateSourceWorkspaceDraft(
  draft: SourceWorkspaceDraft,
  content: string,
): Promise<SourceWorkspaceDraft> {
  const response = await request.put<Envelope<SourceWorkspaceDraft>>(
    api.sourceWorkspaceDraft(draft.workspace_id, draft.id),
    { content, expected_version: draft.version },
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

export async function processSourceWorkspaceStream(
  workspace: SourceWorkspace,
  input: { prompt: string; draft: string; mode: 'all' | 'sequential' },
  onEvent: (event: SourceProcessEvent) => void,
  signal: AbortSignal,
): Promise<void> {
  const response = await fetch(api.sourceWorkspaceProcess(workspace.id), {
    method: 'POST',
    headers: {
      [Authorization]: getAuthorization(),
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ ...input, expected_version: workspace.version }),
    signal,
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(
      body?.message || `Ошибка обработки: HTTP ${response.status}`,
    );
  }
  if (!response.body) throw new Error('Сервер не открыл поток ответа');
  const reader = response.body
    .pipeThrough(new TextDecoderStream())
    .pipeThrough(new EventSourceParserStream())
    .getReader();
  let completed = false;
  try {
    let next = await reader.read();
    while (!next.done) {
      const { value } = next;
      const event = JSON.parse(value.data) as SourceProcessEvent;
      if (event.event === 'error') throw new Error(event.message);
      onEvent(event);
      if (event.event === 'done') completed = true;
      next = await reader.read();
    }
  } catch (error) {
    await reader.cancel().catch(() => undefined);
    throw error;
  } finally {
    reader.releaseLock();
  }
  if (!completed) throw new Error('Поток прервался до завершения обработки');
}
