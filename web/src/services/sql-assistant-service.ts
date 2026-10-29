import type {
  AssistantDefinition,
  AssistantDialogueOptions,
  AssistantEffect,
  AssistantResult,
  AssistantSession,
  AssistantSessionList,
} from '@/interfaces/database/sql-assistant';
import type {
  AssistantCommand,
  CreateAssistantSession,
} from '@/interfaces/request/sql-assistant';
import type { SqlProjectPreflight } from '@/pages/business-documents/types';
import type { CatalogEntity, Freshness } from '@/pages/openmetadata/types';
import { registerNextServer } from '@/utils/register-server';
import type { AxiosResponse } from 'axios';

const Root = '/api/v1/sql-assistant';
const RequestOptions = { skipErrorNotification: true };

export class AssistantRequestError extends Error {
  constructor(
    public status: number,
    public code?: string,
  ) {
    super('Операция SQL-помощника не принята');
  }
}
const server = registerNextServer({
  list: { url: `${Root}/sessions`, method: 'get' },
  create: { url: `${Root}/sessions`, method: 'post' },
  session: { url: `${Root}/sessions`, method: 'get' },
  command: { url: `${Root}/sessions`, method: 'post' },
  result: { url: `${Root}/sessions`, method: 'get' },
  preflight: { url: `${Root}/sessions`, method: 'get' },
  catalog: { url: `${Root}/catalog`, method: 'get' },
  definitions: { url: `${Root}/sessions`, method: 'get' },
  dialogue: { url: `${Root}/sessions`, method: 'get' },
});

async function unwrap<T>(
  response: Promise<AxiosResponse<{ code: number; data: T; message?: string }>>,
): Promise<T> {
  const { data } = await response;
  if (data.code !== 0)
    throw new AssistantRequestError(
      data.code,
      (data.data as { error_code?: string })?.error_code,
    );
  return data.data;
}

const sessionUrl = (id: string) => `${Root}/sessions/${encodeURIComponent(id)}`;
export const listAssistantSessions = () =>
  unwrap<AssistantSessionList>(server.list(RequestOptions, true));
export const createAssistantSession = (data: CreateAssistantSession) =>
  unwrap<AssistantSession>(server.create({ data, ...RequestOptions }, true));
export const fetchAssistantSession = (id: string, signal?: AbortSignal) =>
  unwrap<AssistantSession>(
    server.session({ url: sessionUrl(id), signal, ...RequestOptions }, true),
  );
export const sendAssistantCommand = (id: string, data: AssistantCommand) =>
  unwrap<{ session: AssistantSession; effect: AssistantEffect }>(
    server.command(
      { url: `${sessionUrl(id)}/commands`, data, ...RequestOptions },
      true,
    ),
  );
export const fetchAssistantResult = (
  id: string,
  runId: string,
  offset: number,
  signal?: AbortSignal,
) =>
  unwrap<AssistantResult>(
    server.result(
      {
        url: `${sessionUrl(id)}/results/${encodeURIComponent(runId)}`,
        params: { offset },
        signal,
        ...RequestOptions,
      },
      true,
    ),
  );
export const preflightAssistant = (id: string, signal?: AbortSignal) =>
  unwrap<SqlProjectPreflight>(
    server.preflight(
      { url: `${sessionUrl(id)}/preflight`, signal, ...RequestOptions },
      true,
    ),
  );
export const fetchAssistantDefinitions = (id: string, signal?: AbortSignal) =>
  unwrap<{ definitions: AssistantDefinition[]; can_publish: boolean }>(
    server.definitions(
      { url: `${sessionUrl(id)}/definitions`, signal, ...RequestOptions },
      true,
    ),
  );
export const fetchAssistantDialogueOptions = (
  id: string,
  q: string,
  signal?: AbortSignal,
) =>
  unwrap<AssistantDialogueOptions>(
    server.dialogue(
      {
        url: `${sessionUrl(id)}/dialogue-options`,
        params: { q },
        signal,
        ...RequestOptions,
      },
      true,
    ),
  );
export const searchAssistantCatalog = (q: string, signal?: AbortSignal) =>
  unwrap<{
    entities: CatalogEntity[];
    freshness?: Freshness;
    warnings?: string[];
  }>(server.catalog({ params: { q }, signal, ...RequestOptions }, true));
