import { Button } from '@/components/ui/button';
import {
  assistantCommandId,
  useAssistantCommand,
  useAssistantDialogueOptions,
  useAssistantResult,
} from '@/hooks/use-sql-assistant-request';
import type {
  AssistantEffect,
  AssistantSession,
} from '@/interfaces/database/sql-assistant';
import { PreQueryConnection } from '@/pages/business-documents/constructor/sql-project-connection';
import { useSqlResultDeadline } from '@/pages/business-documents/constructor/sql-result-deadline';
import { AssistantRequestError } from '@/services/sql-assistant-service';
import { isSqlRunUnresolved } from '@/utils/sql-execution';
import axios from 'axios';
import { Database, RefreshCw } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { AssistantBuilder } from './builder';
import { AssistantDefinitions } from './definitions';
import { assistantFailure } from './failure';
import { AssistantResultPanel } from './result';
import { ResultDialogue, type DrillRow } from './result-dialogue';

export type SendCommand = (
  type: string,
  payload?: Record<string, unknown>,
) => Promise<AssistantEffect>;

export type MessageDelivery = {
  key: string;
  payload: Record<string, unknown>;
};

export function AssistantWorkspace({
  actor,
  session,
  refresh,
}: {
  actor: string;
  session: AssistantSession;
  refresh: () => Promise<void>;
}) {
  const [parentRow, setParentRow] = useState<DrillRow | null>(null);
  const [localChanges, setLocalChanges] = useState(false);
  const [delivery, setDelivery] = useState<MessageDelivery>();
  const [error, setError] = useState<ReturnType<
    typeof assistantFailure
  > | null>(null);
  useEffect(() => setParentRow(null), [session.selected_result_id]);
  const command = useAssistantCommand(actor, session);
  const dialogue = useAssistantDialogueOptions(actor, session, '');
  const project = session.project;
  const activeRun = project.runs?.find((run) => isSqlRunUnresolved(run.status));
  const preparing =
    ['ROUTING', 'PREPARING', 'EXECUTING', 'CANCELLING'].includes(
      session.conversation?.phase || '',
    ) ||
    project.operation_state === 'RUNNING' ||
    Boolean(activeRun && activeRun.status !== 'UNKNOWN');
  const locked = command.isPending || preparing;
  const canEdit = project.capabilities.edit !== false;
  const attempt = useRef<{
    type: string;
    payload: Record<string, unknown>;
    signature: string;
    key: string;
    revision: number;
    projectVersion: number;
  } | null>(null);
  const send: SendCommand = async (type, payload = {}) => {
    const signature = JSON.stringify([type, payload]);
    if (attempt.current?.signature !== signature)
      attempt.current = {
        type,
        payload: JSON.parse(JSON.stringify(payload)),
        signature,
        key: assistantCommandId(),
        revision: session.revision,
        projectVersion: project.state_version,
      };
    setError(null);
    const pending = attempt.current;
    try {
      const response = await command.mutateAsync({
        type: pending.type,
        payload: pending.payload,
        key: pending.key,
        revision: pending.revision,
        projectVersion: pending.projectVersion,
      });
      if (pending.type === 'message')
        setDelivery({ key: pending.key, payload: pending.payload });
      if (attempt.current === pending) attempt.current = null;
      return response.effect;
    } catch (failure) {
      if (
        (failure instanceof AssistantRequestError &&
          failure.status >= 400 &&
          failure.status < 500) ||
        (axios.isAxiosError(failure) &&
          failure.response &&
          failure.response.status >= 400 &&
          failure.response.status < 500)
      ) {
        if (attempt.current === pending) attempt.current = null;
      }
      setError(assistantFailure(failure));
      throw failure;
    }
  };
  const retry = attempt.current;
  return (
    <section
      className="sa-workspace sa-conversation"
      aria-label="Исследование данных"
    >
      <header className="sa-conversation-header">
        <h1 title={project.title}>{project.title}</h1>
        <Button
          variant="ghost"
          size="sm"
          aria-label="Обновить исследование"
          disabled={command.isPending}
          onClick={() => void refresh()}
        >
          <RefreshCw className="size-4" />
        </Button>
      </header>
      <div
        className={
          session.selected_result_id
            ? 'sa-conversation-body'
            : 'sa-conversation-body sa-before-result'
        }
      >
        {session.selected_result_id && (
          <div className="sa-conversation-result">
            <AssistantResultPanel
              actor={actor}
              session={session}
              send={send}
              busy={locked}
              onDrill={setParentRow}
            />
            <details className="sa-advanced">
              <summary>SQL и условия расчёта</summary>
              <SelectedSql actor={actor} session={session} />
              {canEdit && (
                <>
                  <AssistantBuilder
                    session={session}
                    surface="sql"
                    send={send}
                    busy={locked}
                    onDirtyChange={setLocalChanges}
                  />
                  <AssistantDefinitions
                    actor={actor}
                    session={session}
                    send={send}
                    busy={locked}
                    localChanges={localChanges}
                  />
                </>
              )}
            </details>
          </div>
        )}
        <section
          className="sa-conversation-chat"
          aria-label="Диалог с SQL-помощником"
        >
          {!session.selected_result_id && (
            <div className="sa-chat-heading">
              <Database className="size-4" />
              <span>Вопрос к данным</span>
            </div>
          )}
          <ResultDialogue
            session={session}
            send={send}
            busy={command.isPending}
            preparingQuery={preparing}
            parentRow={parentRow}
            onClearRow={() => setParentRow(null)}
            options={dialogue.data}
            optionsLoading={dialogue.isFetching}
            optionsError={dialogue.isError}
            delivery={delivery}
          />
          {error && (
            <div className="sa-chat-error" role="alert">
              <p>{error.message}</p>
              {retry ? (
                <Button
                  variant="outline"
                  size="sm"
                  disabled={command.isPending}
                  onClick={() =>
                    void send(retry.type, retry.payload).catch(() => undefined)
                  }
                >
                  Повторить отправку
                </Button>
              ) : (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => void refresh()}
                >
                  Обновить состояние
                </Button>
              )}
            </div>
          )}
          {session.conversation?.phase === 'ERROR' && (
            <p className="sa-chat-error" role="alert">
              {session.conversation.error?.message ||
                'Запрос не завершён. Уточните вопрос или начните новое исследование.'}
            </p>
          )}
          {activeRun?.status === 'UNKNOWN' && (
            <div className="sa-chat-error" role="status">
              <p>
                Исход расчёта ещё не подтверждён. Повторный запуск заблокирован.
              </p>
              {canEdit && (
                <Button
                  variant="outline"
                  size="sm"
                  disabled={command.isPending}
                  onClick={() =>
                    void send('cancel', { run_id: activeRun.id }).catch(
                      () => undefined,
                    )
                  }
                >
                  Проверить и запросить остановку
                </Button>
              )}
            </div>
          )}
          <details className="sa-connection-details">
            <summary>Подключение</summary>
            <PreQueryConnection project={project} onRefresh={refresh} />
          </details>
        </section>
      </div>
    </section>
  );
}

function SelectedSql({
  actor,
  session,
}: {
  actor: string;
  session: AssistantSession;
}) {
  const source = useAssistantResult(actor, session, 0);
  const deadline = useSqlResultDeadline(
    session.selected_result_id || '',
    source.data?.expires_at,
    () => {
      void source.refetch();
    },
  );
  return (
    <section className="sa-selected-sql" aria-label="SQL выбранного результата">
      {source.isPending ? (
        <p role="status">Проверяем SQL результата…</p>
      ) : source.isError || deadline.expired ? (
        <p role="alert">
          SQL недоступен. Проверьте доступ и срок хранения результата.
        </p>
      ) : (
        <>
          <pre>{source.data.sql}</pre>
          <p className="text-xs text-text-secondary">
            Параметры выбранного запуска
          </p>
          <pre>{JSON.stringify(source.data.parameters, null, 2)}</pre>
        </>
      )}
    </section>
  );
}
