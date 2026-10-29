import { Button } from '@/components/ui/button';
import {
  assistantCommandId,
  useAssistantCommand,
  useAssistantDialogueOptions,
  useAssistantPreflight,
  useAssistantResult,
} from '@/hooks/use-sql-assistant-request';
import type {
  AssistantEffect,
  AssistantSession,
} from '@/interfaces/database/sql-assistant';
import {
  QueryReview,
  RequirementsReview,
} from '@/pages/business-documents/constructor/sql-agent-workbench';
import { PreQueryConnection } from '@/pages/business-documents/constructor/sql-project-connection';
import { useSqlResultDeadline } from '@/pages/business-documents/constructor/sql-result-deadline';
import { SqlSchemaReview } from '@/pages/business-documents/constructor/sql-schema-review';
import { AssistantRequestError } from '@/services/sql-assistant-service';
import { isSqlRunUnresolved } from '@/utils/sql-execution';
import { useDebounce } from 'ahooks';
import axios from 'axios';
import { LoaderCircle, RefreshCw } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { AssistantBuilder } from './builder';
import { AssistantDefinitions, DefinitionStatus } from './definitions';
import { assistantFailure } from './failure';
import { MetricChoice } from './metric-choice';
import { AssistantResultPanel } from './result';
import { ResultDialogue, type DrillRow } from './result-dialogue';

export type SendCommand = (
  type: string,
  payload?: Record<string, unknown>,
) => Promise<AssistantEffect>;

export function AssistantWorkspace({
  actor,
  session,
  refresh,
}: {
  actor: string;
  session: AssistantSession;
  refresh: () => Promise<void>;
}) {
  const [surface, setSurface] = useState<'dialog' | 'builder' | 'sql'>(
    'dialog',
  );
  const [parentRow, setParentRow] = useState<DrillRow | null>(null);
  useEffect(() => setParentRow(null), [session.selected_result_id]);
  const [error, setError] = useState<ReturnType<
    typeof assistantFailure
  > | null>(null);
  const [dialogueQuestion, setDialogueQuestion] = useState('');
  const [advanceExploratory, setAdvanceExploratory] = useState(false);
  const lookupQuestion = useDebounce(
    session.selected_result_id
      ? dialogueQuestion
      : session.messages[0]?.text || '',
    { wait: 250 },
  );
  const dialogue = useAssistantDialogueOptions(actor, session, lookupQuestion);
  const refreshState = async () => {
    await refresh();
    if (session.project.artifact_ids.schema) await dialogue.refetch();
  };
  const [localChanges, setLocalChanges] = useState(false);
  const command = useAssistantCommand(actor, session);
  const preflight = useAssistantPreflight(actor, session);
  const project = session.project;
  const busy = command.isPending;
  const canEdit = project.capabilities.edit !== false;
  const active = project.operation_state === 'RUNNING';
  const activeRun = project.runs?.find((run) => isSqlRunUnresolved(run.status));
  const attempt = useRef<{
    signature: string;
    key: string;
    revision: number;
    projectVersion: number;
  } | null>(null);
  const send: SendCommand = async (type, payload = {}) => {
    const signature = JSON.stringify([type, payload]);
    if (attempt.current?.signature !== signature)
      attempt.current = {
        signature,
        key: assistantCommandId(),
        revision: session.revision,
        projectVersion: project.state_version,
      };
    setError(null);
    try {
      const response = await command.mutateAsync({
        type,
        payload,
        key: attempt.current.key,
        revision: attempt.current.revision,
        projectVersion: attempt.current.projectVersion,
      });
      attempt.current = null;
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
      )
        attempt.current = null;
      setError(assistantFailure(failure));
      throw failure;
    }
  };
  const act = (type: string, payload = {}) => {
    void send(type, payload).catch(() => undefined);
  };
  const decide = async (
    decision: 'ACCEPT' | 'REJECT',
    artifact_payload: Record<string, unknown> | null,
  ) => {
    await send('decide', { decision, artifact_payload });
  };
  const proposal = project.pending_proposal;
  const revision = proposal?.payload.agent_result as
    | { proposal?: { summary?: string; sql?: string; questions?: unknown[] } }
    | undefined;
  return (
    <section className="sa-workspace">
      <div className="sa-toolbar">
        <div>
          <p className="sa-caption text-text-secondary">Исследование</p>
          <h1>{project.title}</h1>
          <p className="mt-1 text-xs text-text-secondary">
            {session.mode === 'AGREED'
              ? 'По согласованному определению'
              : 'Исследовательский расчёт'}{' '}
            · версия {project.state_version}
          </p>
        </div>
        <Button
          variant="ghost"
          onClick={() => void refreshState()}
          disabled={busy}
        >
          <RefreshCw className="size-4" />
          Обновить
        </Button>
      </div>
      <div
        className="sa-tabs"
        role="tablist"
        aria-label="Способ работы с исследованием"
      >
        {(
          [
            ['dialog', 'Чат'],
            ['builder', 'Конструктор'],
            ['sql', 'SQL'],
          ] as const
        ).map(([key, title]) => (
          <button
            key={key}
            id={`sa-tab-${key}`}
            role="tab"
            aria-controls={`sa-panel-${key}`}
            aria-selected={surface === key}
            tabIndex={surface === key ? 0 : -1}
            onClick={() => setSurface(key)}
            onKeyDown={(event) => {
              const order = ['dialog', 'builder', 'sql'] as const;
              const index = order.indexOf(surface);
              const next =
                event.key === 'ArrowRight'
                  ? order[(index + 1) % 3]
                  : event.key === 'ArrowLeft'
                    ? order[(index + 2) % 3]
                    : event.key === 'Home'
                      ? order[0]
                      : event.key === 'End'
                        ? order[2]
                        : null;
              if (next) {
                event.preventDefault();
                setSurface(next);
                requestAnimationFrame(() =>
                  document.getElementById(`sa-tab-${next}`)?.focus(),
                );
              }
            }}
          >
            {title}
          </button>
        ))}
      </div>
      <div className="sa-statuses">
        <div>
          <strong>Определение</strong>
          <DefinitionStatus value={session} />
        </div>
        <div>
          <strong>Источники</strong>
          {session.source_completeness === 'COMPLETE'
            ? 'Полнота периода подтверждена'
            : session.source_completeness === 'PARTIAL'
              ? 'Период загружен частично'
              : session.source_completeness === 'STALE'
                ? 'Данные источника устарели'
                : 'Полнота загрузки неизвестна'}
        </div>
        <div>
          <strong>Расчёт</strong>
          {activeRun
            ? activeRun.status === 'UNKNOWN'
              ? 'Исход неизвестен. Повтор запрещён; требуется сверка состояния.'
              : activeRun.status === 'CANCEL_REQUESTED'
                ? 'Ожидаем подтверждения остановки'
                : 'Выполняется'
            : active
              ? 'Готовим запрос'
              : 'Ожидает действия'}
        </div>
        <div>
          <strong>Результат</strong>
          {session.selected_result_id
            ? 'Выбран сохранённый результат'
            : 'Выберите запуск в истории'}
        </div>
      </div>
      <AssistantDefinitions
        actor={actor}
        session={session}
        send={send}
        busy={busy || active || Boolean(activeRun)}
        localChanges={localChanges}
      />
      {error && (
        <div className="sa-error" role="alert">
          <p>{error.message}</p>
          <Button
            variant="outline"
            className="mt-2"
            onClick={() => void refreshState()}
          >
            Обновить состояние
          </Button>
        </div>
      )}
      {project.last_error && (
        <div className="sa-error" role="alert">
          {/DENIED|PERMISSION|AUTH/.test(project.last_error.code || '')
            ? 'Подготовка остановлена из-за ограничения доступа. Принятые условия сохранены; проверьте права на источники.'
            : /UNAVAILABLE|WORKER_FAILURE|TIMEOUT/.test(
                  project.last_error.code || '',
                )
              ? 'Подготовка временно недоступна. Принятые условия сохранены; повторите текущий шаг позже.'
              : 'Подготовка запроса остановлена. Принятые условия сохранены; проверьте вопрос и текущий шаг.'}
        </div>
      )}
      <div
        role="tabpanel"
        id="sa-panel-dialog"
        aria-labelledby="sa-tab-dialog"
        hidden={surface !== 'dialog'}
      >
        <div className="sa-dialog-layout">
          <aside className="sa-panel">
            <h2>Диалог и условия</h2>
            <ResultDialogue
              session={session}
              send={send}
              busy={busy}
              preparingQuery={active || Boolean(activeRun)}
              parentRow={parentRow}
              onClearRow={() => setParentRow(null)}
              options={dialogue.data}
              optionsLoading={
                Boolean(project.artifact_ids.schema) &&
                (dialogue.isPending || lookupQuestion !== dialogueQuestion)
              }
              optionsError={dialogue.isError}
              onQuestionChange={setDialogueQuestion}
              onOpenSql={() => setSurface('sql')}
              onUseDefinition={(definition_id, version) => {
                void send('use_definition', { definition_id, version })
                  .then(() => setSurface('builder'))
                  .catch(() => undefined);
              }}
            />
          </aside>
          <div className="space-y-5 min-w-0">
            {active && (
              <div className="sa-panel" role="status">
                <LoaderCircle className="inline size-4 animate-spin mr-2" />
                {project.current_job?.progress_message ||
                  'Подбираем контекст и готовим предложение…'}
                <p className="mt-2 text-xs text-text-secondary">
                  {project.current_job?.progress ?? 0}% · ожидание вашего ответа
                  не запускает SQL.
                </p>
              </div>
            )}
            {proposal && (
              <div className="sa-panel">
                <h2>
                  {proposal.kind === 'REQUIREMENTS'
                    ? 'Уточним смысл вопроса'
                    : proposal.kind === 'SCHEMA'
                      ? 'Проверьте выбранные данные'
                      : proposal.kind === 'REVISION'
                        ? 'Изменения расчёта'
                        : 'Условия будущего расчёта'}
                </h2>
                {!canEdit ? (
                  <p>
                    Ваша роль позволяет просматривать исследование. Принятие
                    изменений недоступно.
                  </p>
                ) : proposal.kind === 'REQUIREMENTS' ? (
                  <RequirementsReview
                    project={project}
                    busy={busy}
                    onDecision={decide}
                  />
                ) : proposal.kind === 'SCHEMA' ? (
                  <SqlSchemaReview
                    project={project}
                    busy={busy}
                    onDecision={decide}
                  />
                ) : proposal.kind === 'QUERY' ? (
                  <QueryReview
                    project={project}
                    busy={busy}
                    onDecision={decide}
                  />
                ) : (
                  <div className="space-y-3">
                    <p>
                      {revision?.proposal?.summary ||
                        'Предложение требует проверки.'}
                    </p>
                    <pre>
                      {revision?.proposal?.sql ||
                        'SQL отсутствует; уточните вопрос.'}
                    </pre>
                    <p className="text-sm text-text-secondary">
                      Проверьте, что исходные фильтры, метрика и период
                      сохранены. Изменение требует нового расчёта.
                    </p>
                    <div className="flex gap-2">
                      <Button
                        disabled={busy || !revision?.proposal?.sql}
                        onClick={() =>
                          act('decide', {
                            decision: 'ACCEPT',
                            artifact_payload: null,
                          })
                        }
                      >
                        Применить изменения
                      </Button>
                      <Button
                        variant="outline"
                        disabled={busy}
                        onClick={() =>
                          act('decide', {
                            decision: 'REJECT',
                            artifact_payload: null,
                          })
                        }
                      >
                        Отклонить
                      </Button>
                    </div>
                  </div>
                )}
              </div>
            )}
            {canEdit && !active && !proposal && project.next_agent && (
              <div className="sa-panel">
                <h2>Следующий шаг</h2>
                <p className="mb-3 text-sm text-text-secondary">
                  {project.next_agent === 'SCHEMA'
                    ? 'Найдём подходящие таблицы и поля по принятым условиям.'
                    : project.next_agent === 'QUERY'
                      ? 'Подготовим запрос по выбранным данным.'
                      : 'Разберём вопрос и существенные неоднозначности.'}
                </p>
                {project.next_agent === 'QUERY' && (
                  <MetricChoice
                    metrics={dialogue.data?.metrics || []}
                    busy={busy}
                    canUse={!session.draft && !proposal && !active}
                    onUse={(definition_id, version) => {
                      void send('use_definition', { definition_id, version })
                        .then(() => setSurface('builder'))
                        .catch(() => undefined);
                    }}
                    exploratory={advanceExploratory}
                    onExplore={setAdvanceExploratory}
                  />
                )}
                <Button
                  disabled={
                    busy ||
                    (project.next_agent === 'QUERY' &&
                      (dialogue.isPending ||
                        dialogue.isError ||
                        Boolean(
                          dialogue.data?.metrics.length && !advanceExploratory,
                        )))
                  }
                  onClick={() =>
                    act(
                      'advance',
                      advanceExploratory
                        ? { metric_resolution: 'EXPLORATORY' }
                        : {},
                    )
                  }
                >
                  Продолжить подготовку
                </Button>
              </div>
            )}
            {project.artifact_ids.schema && (
              <div className="sa-panel">
                <PreQueryConnection
                  project={project}
                  busy={busy}
                  onRefresh={refresh}
                />
              </div>
            )}
            {canEdit &&
              project.artifact_ids.query &&
              !project.compilation &&
              !active && (
                <Button disabled={busy} onClick={() => act('compile')}>
                  Проверить SQL
                </Button>
              )}
            {project.compilation && (
              <div className="sa-panel">
                <h2>Расчёт по текущей версии</h2>
                <p className="mb-3 text-sm text-text-secondary">
                  Результаты сохраняются отдельно. Полнота источника и
                  соответствие бизнес-определению не подтверждены.
                </p>
                {preflight.isError && (
                  <p role="alert">
                    Проверка подключения недоступна. Повторите обновление.
                  </p>
                )}
                {preflight.data?.blocker && (
                  <p role="alert">
                    Запуск недоступен: {preflight.data.blocker.message}
                  </p>
                )}
                <Button
                  disabled={
                    busy ||
                    active ||
                    Boolean(activeRun) ||
                    !canEdit ||
                    project.capabilities.execute === false ||
                    !preflight.data ||
                    Boolean(preflight.data.blocker) ||
                    preflight.data.binding.status !== 'BOUND'
                  }
                  onClick={() =>
                    act('execute', {
                      selected_profile_id: project.execution_profile_id || null,
                    })
                  }
                >
                  Выполнить SQL
                </Button>
              </div>
            )}
          </div>
        </div>
      </div>
      {surface === 'sql' && session.selected_result_id && (
        <SelectedSql actor={actor} session={session} />
      )}
      <div hidden={surface === 'dialog'}>
        <AssistantBuilder
          key={`${session.id}:${project.artifact_ids.query}:${session.draft?.revision || 0}`}
          session={session}
          surface={surface === 'sql' ? 'sql' : 'builder'}
          send={send}
          busy={busy || active || Boolean(activeRun)}
          onDirtyChange={setLocalChanges}
        />
      </div>
      <AssistantResultPanel
        actor={actor}
        session={session}
        send={send}
        busy={busy}
        onNewMetric={
          canEdit
            ? () => {
                const query =
                  session.draft?.query ||
                  session.structured_query ||
                  project.artifacts?.query;
                setSurface(query && !('mode' in query) ? 'builder' : 'sql');
              }
            : undefined
        }
        onDrill={(row) => {
          setParentRow(row);
          setSurface('dialog');
        }}
      />
      {activeRun && canEdit && (
        <div className="sa-panel" role="status">
          <p>
            {activeRun.status === 'UNKNOWN'
              ? 'Исход SQL неизвестен. Повторная отправка заблокирована. Обновите состояние для сверки с исполнителем.'
              : activeRun.status === 'CANCEL_REQUESTED'
                ? 'Запрос на отмену передан. Остановка ещё не подтверждена исполнителем.'
                : 'SQL выполняется. Закрытие страницы не означает остановку расчёта.'}
          </p>
          <Button
            className="mt-3"
            variant="outline"
            disabled={busy || activeRun.status === 'CANCEL_REQUESTED'}
            onClick={() => act('cancel', { run_id: activeRun.id })}
          >
            {activeRun.status === 'UNKNOWN'
              ? 'Проверить и запросить остановку'
              : 'Остановить расчёт'}
          </Button>
        </div>
      )}
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
  const source = useAssistantResult(
    actor,
    session.id,
    session.selected_result_id,
    0,
  );
  const deadline = useSqlResultDeadline(
    session.selected_result_id || '',
    source.data?.expires_at,
    () => {
      void source.refetch();
    },
  );
  return (
    <section className="sa-panel" aria-label="SQL выбранного результата">
      <h2>SQL выбранного результата</h2>
      {source.isPending ? (
        <p role="status">Проверяем сохранённый источник…</p>
      ) : source.isError || deadline.expired ? (
        <p role="alert">
          SQL выбранного результата недоступен. Проверьте права и срок хранения.
        </p>
      ) : (
        <>
          <pre>{source.data.sql}</pre>
          <details>
            <summary>Параметры выбранного запуска</summary>
            <pre>{JSON.stringify(source.data.parameters, null, 2)}</pre>
          </details>
          <p className="mt-2 text-xs text-text-secondary">
            Этот SQL относится к выбранному результату. Редактор ниже готовит
            следующую версию расчёта.
          </p>
        </>
      )}
    </section>
  );
}
