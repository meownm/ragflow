import type { AssistantSession } from '@/interfaces/database/sql-assistant';
import { getBusinessDocumentCapabilities } from '@/services/business-document-service';
import {
  createAssistantSession,
  fetchAssistantDefinitions,
  fetchAssistantDialogueOptions,
  fetchAssistantResult,
  fetchAssistantSession,
  listAssistantSessions,
  preflightAssistant,
  searchAssistantCatalog,
  sendAssistantCommand,
} from '@/services/sql-assistant-service';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

export const SqlAssistantKeys = {
  all: (actor: string) => ['sql-assistant', actor] as const,
  access: (actor: string) =>
    [...SqlAssistantKeys.all(actor), 'access'] as const,
  list: (actor: string) =>
    [...SqlAssistantKeys.all(actor), 'sessions'] as const,
  session: (actor: string, id: string | null) =>
    [...SqlAssistantKeys.all(actor), 'session', id] as const,
  result: (actor: string, id: string, run: string | null, offset: number) =>
    [...SqlAssistantKeys.session(actor, id), 'result', run, offset] as const,
  preflight: (actor: string, id: string, version: number) =>
    [...SqlAssistantKeys.session(actor, id), 'preflight', version] as const,
  catalog: (actor: string, q: string) =>
    [...SqlAssistantKeys.all(actor), 'catalog', q] as const,
  definitions: (actor: string, id: string) =>
    [...SqlAssistantKeys.session(actor, id), 'definitions'] as const,
  dialogue: (actor: string, id: string, version: number, q: string) =>
    [...SqlAssistantKeys.session(actor, id), 'dialogue', version, q] as const,
};

export const useAssistantAccess = (actor: string) =>
  useQuery({
    queryKey: SqlAssistantKeys.access(actor),
    queryFn: getBusinessDocumentCapabilities,
    enabled: Boolean(actor),
    retry: false,
  });

export const assistantCommandId = () => crypto.randomUUID();
export const useAssistantSessions = (actor: string) =>
  useQuery({
    queryKey: SqlAssistantKeys.list(actor),
    queryFn: listAssistantSessions,
    enabled: Boolean(actor),
  });
export const useAssistantSession = (actor: string, id: string | null) =>
  useQuery({
    queryKey: SqlAssistantKeys.session(actor, id),
    queryFn: ({ signal }) => fetchAssistantSession(id!, signal),
    enabled: Boolean(actor && id),
    retry: false,
    refetchInterval: (query) => {
      const project = query.state.data?.project;
      return project?.operation_state === 'RUNNING' ||
        project?.runs?.some((run) =>
          ['QUEUED', 'RUNNING', 'CANCEL_REQUESTED'].includes(run.status),
        )
        ? 1500
        : false;
    },
  });

export function useCreateAssistant(actor: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: createAssistantSession,
    retry: false,
    onSuccess: async (session) => {
      client.setQueryData(SqlAssistantKeys.session(actor, session.id), session);
      await client.invalidateQueries({
        queryKey: SqlAssistantKeys.list(actor),
      });
    },
  });
}

export function useAssistantCommand(actor: string, session: AssistantSession) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({
      type,
      payload,
      key,
      revision,
      projectVersion,
    }: {
      type: string;
      payload: Record<string, unknown>;
      key: string;
      revision: number;
      projectVersion: number;
    }) =>
      sendAssistantCommand(session.id, {
        type,
        payload,
        client_request_id: key,
        expected_revision: revision,
        expected_project_version: projectVersion,
      }),
    retry: false,
    onSuccess: async (result) => {
      client.setQueryData(
        SqlAssistantKeys.session(actor, session.id),
        result.session,
      );
      await client.invalidateQueries({
        queryKey: SqlAssistantKeys.session(actor, session.id),
        exact: false,
      });
      await client.invalidateQueries({
        queryKey: SqlAssistantKeys.list(actor),
      });
    },
  });
}

export const useAssistantResult = (
  actor: string,
  id: string,
  run: string | null,
  offset: number,
) =>
  useQuery({
    queryKey: SqlAssistantKeys.result(actor, id, run, offset),
    queryFn: ({ signal }) => fetchAssistantResult(id, run!, offset, signal),
    enabled: Boolean(run),
    retry: false,
    staleTime: 0,
  });
export const useAssistantPreflight = (
  actor: string,
  session: AssistantSession,
) =>
  useQuery({
    queryKey: SqlAssistantKeys.preflight(
      actor,
      session.id,
      session.project.state_version,
    ),
    queryFn: ({ signal }) => preflightAssistant(session.id, signal),
    enabled:
      Boolean(session.project.compilation) &&
      session.project.operation_state === 'IDLE',
    retry: false,
  });
export const useAssistantCatalog = (actor: string, q: string, enabled = true) =>
  useQuery({
    queryKey: SqlAssistantKeys.catalog(actor, q),
    queryFn: ({ signal }) => searchAssistantCatalog(q, signal),
    enabled: Boolean(actor) && enabled,
    retry: false,
  });
export const useAssistantDialogueOptions = (
  actor: string,
  session: AssistantSession,
  q: string,
) =>
  useQuery({
    queryKey: SqlAssistantKeys.dialogue(
      actor,
      session.id,
      session.project.state_version,
      q,
    ),
    queryFn: ({ signal }) =>
      fetchAssistantDialogueOptions(session.id, q, signal),
    enabled: Boolean(actor && session.project.artifact_ids.schema),
    retry: false,
  });
export const useAssistantDefinitions = (
  actor: string,
  session: AssistantSession,
) =>
  useQuery({
    queryKey: SqlAssistantKeys.definitions(actor, session.id),
    queryFn: ({ signal }) => fetchAssistantDefinitions(session.id, signal),
    enabled: Boolean(actor && session.project.artifact_ids.schema),
    retry: false,
  });
