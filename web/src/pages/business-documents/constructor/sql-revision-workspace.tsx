import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import type {
  SqlAgentProject,
  SqlRevisionWorkspace,
} from '@/pages/business-documents/types';
import {
  cancelBusinessDocumentSqlRevision,
  createBusinessDocumentSqlRevisionThread,
  decideBusinessDocumentSqlRevision,
  fetchBusinessDocumentSqlRevisionWorkspace,
  reattachBusinessDocumentSqlRevisionThread,
  reopenBusinessDocumentSqlSchema,
  replyBusinessDocumentSqlRevisionThread,
  requestBusinessDocumentSqlRevision,
  restoreBusinessDocumentSqlQuery,
} from '@/services/business-document-service';
import {
  type ComponentProps,
  useCallback,
  useEffect,
  useRef,
  useState,
} from 'react';
import { SqlExportButton } from './sql-export-button';
import { SqlHistoryContext } from './sql-history-context';
import { SqlQueryComparison } from './sql-query-comparison';
import type { SqlGraphChangeRequest } from './sql-schema-graph';

const labels: Record<string, string> = {
  FIELD: 'Поле результата',
  JOIN: 'Соединение',
  FILTER: 'Условие',
  GROUP: 'Группировка',
  HAVING: 'Условие группы',
  ORDER: 'Сортировка',
  CTE: 'Промежуточная выборка',
  SUBQUERY: 'Подзапрос',
};

function key() {
  return `sql-revision-${typeof crypto !== 'undefined' && 'randomUUID' in crypto ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`}`;
}

function errorText(value: unknown) {
  return value instanceof Error
    ? value.message
    : 'Не удалось выполнить действие. Обновите проект и повторите попытку.';
}

function HistoryIdentity({
  item,
  compilationId,
}: {
  item: {
    accepted_by?: string;
    source_proposal_id?: string;
    created_at?: number;
    source_artifact_ids?: string[];
    restored_from?: {
      query_artifact_id: string;
      compilation_id: string;
    } | null;
  };
  compilationId?: string;
}) {
  const date = item.created_at == null ? null : new Date(item.created_at);
  return (
    <div className="mt-2 space-y-1 break-all text-xs text-text-secondary">
      <p>
        Автор: {item.accepted_by || 'не записан'} ·{' '}
        {date && Number.isFinite(date.getTime()) ? (
          <time dateTime={date.toISOString()}>{date.toLocaleString()}</time>
        ) : (
          'время не записано'
        )}
      </p>
      {item.source_artifact_ids?.length ? (
        <p>Источник: {item.source_artifact_ids.join(' · ')}</p>
      ) : null}
      {item.source_proposal_id && <p>Основание: {item.source_proposal_id}</p>}
      {compilationId && <p>Проверка: {compilationId}</p>}
      {item.restored_from && (
        <p>
          Восстановлено из запроса {item.restored_from.query_artifact_id} ·
          проверка {item.restored_from.compilation_id}
        </p>
      )}
    </div>
  );
}

export function SqlRevisionWorkspacePanel(
  props: ComponentProps<typeof SqlRevisionWorkspace>,
) {
  return <SqlRevisionWorkspace key={props.project.id} {...props} />;
}

function SqlRevisionWorkspace({
  project,
  onRefresh,
  onSchemaRequested,
  changeRequest,
  onQueryViewChange,
}: {
  project: SqlAgentProject;
  onRefresh: () => Promise<void>;
  onSchemaRequested?: (terms: string[]) => void;
  changeRequest?: SqlGraphChangeRequest | null;
  onQueryViewChange?: (
    view: {
      id: string;
      revision: number;
      sql: string;
      compilation_id?: string;
    } | null,
  ) => void;
}) {
  const [workspace, setWorkspace] = useState<SqlRevisionWorkspace | null>(null);
  const [target, setTarget] = useState<'QUERY' | 'DOCUMENT'>('QUERY');
  const [anchor, setAnchor] = useState<Record<string, unknown>>({
    kind: 'WHOLE',
  });
  const [comment, setComment] = useState('');
  const [additionalEntityIds, setAdditionalEntityIds] = useState<string[]>([]);
  const [selectedQueryId, setSelectedQueryId] = useState<string | null>(null);
  const restoreAttempt = useRef<{
    source: string;
    version: number;
    key: string;
  } | null>(null);
  const activeComponent = useRef(true);
  const restoreScope = useRef('');
  restoreScope.current = JSON.stringify([project.id, selectedQueryId]);
  useEffect(() => {
    activeComponent.current = true;
    return () => {
      activeComponent.current = false;
    };
  }, []);
  useEffect(() => {
    restoreAttempt.current = null;
  }, [project.id, selectedQueryId]);
  const historicalQuery = workspace?.query_revisions?.find(
    (item) =>
      item.id === selectedQueryId && item.id !== workspace.query_artifact_id,
  );
  useEffect(() => {
    if (
      workspace &&
      !workspace.query_artifact_id &&
      !selectedQueryId &&
      workspace.query_revisions?.length
    ) {
      setSelectedQueryId(workspace.query_revisions[0].id);
    }
  }, [workspace, selectedQueryId]);
  useEffect(() => {
    onQueryViewChange?.(
      historicalQuery
        ? {
            id: historicalQuery.id,
            revision: historicalQuery.revision,
            sql: historicalQuery.sql,
            compilation_id: historicalQuery.compilation_id,
          }
        : null,
    );
  }, [historicalQuery, onQueryViewChange]);
  useEffect(() => {
    if (!changeRequest) return;
    setTarget('QUERY');
    setAnchor({ kind: 'WHOLE' });
    setSelectedQueryId(null);
    setComment(changeRequest.text);
    setAdditionalEntityIds(changeRequest.entityIds);
    document
      .getElementById('sql-revision-workspace')
      ?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }, [changeRequest]);
  const [reply, setReply] = useState<Record<string, string>>({});
  const [selectedDocumentId, setSelectedDocumentId] = useState<string | null>(
    null,
  );
  const [factsConfirmed, setFactsConfirmed] = useState(false);
  const [working, setBusy] = useState(false);
  const canEdit = project.capabilities?.edit !== false;
  const busy = working || !canEdit;
  const [error, setError] = useState<string | null>(null);
  const requestVersion = useRef(0);
  const workspaceRequest = useRef<AbortController | null>(null);
  const refresh = useCallback(async () => {
    workspaceRequest.current?.abort();
    const controller = new AbortController();
    workspaceRequest.current = controller;
    const version = ++requestVersion.current;
    try {
      const next = await fetchBusinessDocumentSqlRevisionWorkspace(
        project.id,
        controller.signal,
      );
      if (version === requestVersion.current && !controller.signal.aborted)
        setWorkspace(next);
    } catch (failure) {
      if (!controller.signal.aborted) throw failure;
    }
  }, [project.id]);

  useEffect(() => {
    let active = true;
    activeComponent.current = true;
    const abort = () => {
      activeComponent.current = false;
      workspaceRequest.current?.abort();
      requestVersion.current += 1;
    };
    const load = () =>
      void refresh().catch((nextError) => {
        if (active) setError(errorText(nextError));
      });
    const restored = (event: PageTransitionEvent) => {
      if (!event.persisted) return;
      activeComponent.current = true;
      setWorkspace(null);
      load();
    };
    window.addEventListener('pagehide', abort);
    window.addEventListener('pageshow', restored);
    load();
    return () => {
      active = false;
      abort();
      window.removeEventListener('pagehide', abort);
      window.removeEventListener('pageshow', restored);
    };
  }, [
    refresh,
    project.state_version,
    project.operation_state,
    project.pending_proposal?.id,
  ]);
  useEffect(() => {
    if (
      workspace &&
      !workspace.query_artifact_id &&
      workspace.document_artifact_id
    )
      setTarget('DOCUMENT');
  }, [workspace]);
  useEffect(() => {
    if (
      selectedQueryId &&
      workspace &&
      !workspace.query_revisions?.some((item) => item.id === selectedQueryId)
    ) {
      setSelectedQueryId(null);
      setAnchor({ kind: 'WHOLE' });
    }
  }, [selectedQueryId, workspace]);
  useEffect(() => setFactsConfirmed(false), [project.pending_proposal?.id]);

  if (
    !workspace ||
    (!workspace.query_artifact_id &&
      !workspace.document_artifact_id &&
      !workspace.history?.length &&
      !workspace.query_revisions?.length)
  )
    return null;

  const proposal =
    project.pending_proposal?.kind === 'REVISION'
      ? project.pending_proposal
      : null;
  const proposalResult = proposal?.payload?.agent_result as
    | {
        proposal?: {
          sql?: string;
          parameters?: unknown[];
          parameter_diff?: string;
          document_text?: string;
          text_diff?: string;
          summary?: string;
          requested_sources?: string[];
          sql_diff?: string;
          affected_blocks?: Array<{
            change: string;
            kind: string;
            sql: string;
          }>;
          checks?: { status?: string; reason?: string };
        };
        source?: {
          target?: string;
          thread_id?: string;
          thread_version?: number;
          base_artifact_id?: string;
        };
      }
    | undefined;

  const perform = async (action: () => Promise<unknown>) => {
    if (!canEdit || working) return;
    setBusy(true);
    setError(null);
    try {
      await action();
      if (activeComponent.current) await refresh();
    } catch (nextError) {
      if (activeComponent.current) setError(errorText(nextError));
    } finally {
      if (activeComponent.current) setBusy(false);
    }
  };

  const addComment = () =>
    perform(async () => {
      await createBusinessDocumentSqlRevisionThread(project.id, {
        schema_version: '1',
        expected_state_version: project.state_version,
        idempotency_key: key(),
        target,
        ...(target === 'QUERY' &&
        selectedQueryId &&
        selectedQueryId !== workspace.query_artifact_id
          ? { base_artifact_id: selectedQueryId }
          : {}),
        ...(target === 'DOCUMENT' && selectedDocumentId
          ? { base_artifact_id: selectedDocumentId }
          : {}),
        anchor: target === 'DOCUMENT' ? { kind: 'WHOLE' } : anchor,
        text: comment,
        ...(additionalEntityIds.length
          ? { additional_entity_ids: additionalEntityIds }
          : {}),
      });
      setComment('');
      setAdditionalEntityIds([]);
      setAnchor({ kind: 'WHOLE' });
    });

  const askAgent = (threadId: string) =>
    perform(async () => {
      const entities = [
        ...new Set(
          workspace.threads
            .find((thread) => thread.id === threadId)
            ?.messages.flatMap(
              (message) => message.additional_entity_ids || [],
            ) || [],
        ),
      ];
      const commandKey = key();
      await (entities.length
        ? requestBusinessDocumentSqlRevision(
            project.id,
            threadId,
            project.state_version,
            commandKey,
            entities,
          )
        : requestBusinessDocumentSqlRevision(
            project.id,
            threadId,
            project.state_version,
            commandKey,
          ));
      await onRefresh();
    });

  const decide = (decision: 'ACCEPT' | 'REJECT') =>
    perform(async () => {
      if (!proposal) return;
      await decideBusinessDocumentSqlRevision(
        project.id,
        proposal.id,
        project.state_version,
        key(),
        decision,
        proposalResult?.source?.target === 'DOCUMENT' && factsConfirmed,
      );
      if (
        decision === 'ACCEPT' &&
        proposalResult?.source?.target === 'DOCUMENT'
      )
        setSelectedDocumentId(null);
      await onRefresh();
    });
  const cancel = () =>
    perform(async () => {
      await cancelBusinessDocumentSqlRevision(
        project.id,
        project.state_version,
        key(),
      );
      await onRefresh();
    });
  const reopenSchema = () =>
    perform(async () => {
      await reopenBusinessDocumentSqlSchema(
        project.id,
        project.state_version,
        key(),
      );
      const schema = project.artifacts?.schema as
        | {
            accepted_schema?: Array<{ entity_id: string }>;
            schema_snapshot?: {
              requirements?: Array<{
                term: string;
                selected_table?: { id: string };
              }>;
            };
          }
        | undefined;
      const accepted = new Set(
        schema?.accepted_schema?.map((item) => item.entity_id) || [],
      );
      const existingTerms =
        schema?.schema_snapshot?.requirements
          ?.filter(
            (item) =>
              item.selected_table && accepted.has(item.selected_table.id),
          )
          .map((item) => item.term) || [];
      const terms = [
        ...new Set(
          [
            ...existingTerms,
            ...(proposalResult?.proposal?.requested_sources || []),
          ]
            .map((item) => item.trim())
            .filter(Boolean),
        ),
      ].slice(0, 8);
      onSchemaRequested?.(terms);
      await onRefresh();
    });

  const selectedDocument =
    (selectedDocumentId
      ? project.documents?.find((item) => item.id === selectedDocumentId)
      : null) || project.document;
  const selectedQuery = workspace.query_revisions?.find(
    (item) => item.id === selectedQueryId,
  );
  const exportQuery =
    selectedQuery ||
    workspace.query_revisions?.find(
      (item) => item.id === workspace.query_artifact_id,
    );
  const currentQuery = workspace.query_revisions?.find(
    (item) => item.id === workspace.query_artifact_id,
  );
  const canRestore = Boolean(
    historicalQuery?.compilation_id &&
    !busy &&
    !project.archived_at &&
    project.operation_state !== 'RUNNING' &&
    project.artifact_ids.requirements &&
    project.artifact_ids.schema &&
    !project.runs?.some((item) =>
      ['QUEUED', 'RUNNING', 'CANCEL_REQUESTED'].includes(item.status),
    ),
  );
  const restoreSelected = () =>
    perform(async () => {
      if (!canRestore || !historicalQuery?.compilation_id) return;
      const source = historicalQuery.compilation_id;
      if (restoreAttempt.current?.source !== source) {
        restoreAttempt.current = {
          source,
          version: project.state_version,
          key: key(),
        };
      }
      const scope = restoreScope.current;
      try {
        await restoreBusinessDocumentSqlQuery(
          project.id,
          source,
          restoreAttempt.current.version,
          restoreAttempt.current.key,
        );
      } catch (failure) {
        if (
          failure instanceof Error &&
          'code' in failure &&
          failure.code === 'SQL_PROJECT_VERSION_CONFLICT'
        )
          restoreAttempt.current = null;
        throw failure;
      }
      if (!activeComponent.current || restoreScope.current !== scope) return;
      await onRefresh();
      if (activeComponent.current && restoreScope.current === scope)
        setSelectedQueryId(null);
    });
  const selectedSql = selectedQuery?.sql || workspace.sql;
  const selectedBlocks = selectedQuery?.blocks || workspace.blocks;
  const proposalBaseSql =
    workspace.query_revisions?.find(
      (item) => item.id === proposalResult?.source?.base_artifact_id,
    )?.sql || workspace.sql;
  const proposalThread = workspace.threads.find(
    (item) => item.id === proposalResult?.source?.thread_id,
  );
  const proposalNeedsRefresh = Boolean(
    proposal &&
    proposalResult?.source?.thread_version != null &&
    proposalResult.source.thread_version !== proposalThread?.version,
  );
  const viewingOldDocument = Boolean(
    selectedDocument && selectedDocument.id !== workspace.document_artifact_id,
  );
  const documentText = String(
    (
      selectedDocument?.payload?.confirmed_conclusion as
        | { text?: string }
        | undefined
    )?.text || '',
  );
  const proposalSourceDocument =
    project.documents?.find(
      (item) => item.id === proposalResult?.source?.base_artifact_id,
    ) || project.document;
  return (
    <section
      id="sql-revision-workspace"
      className="mb-8 border-y border-border-button py-6"
      data-testid="sql-revision-workspace"
    >
      <h3 className="text-lg font-semibold">Доработка запроса и документа</h3>
      <p className="mt-1 text-sm text-text-secondary">
        Выберите понятную часть запроса или выделите SQL, опишите изменение
        обычным языком. ИИ подготовит новую версию для проверки; сохранённая
        ревизия останется доступной.
      </p>
      <div className="mt-5 flex flex-wrap gap-2">
        <Button
          size="sm"
          variant={target === 'QUERY' ? 'default' : 'outline'}
          onClick={() => {
            setTarget('QUERY');
            setAnchor({ kind: 'WHOLE' });
          }}
          disabled={
            !workspace.query_artifact_id && !workspace.query_revisions?.length
          }
        >
          Запрос
        </Button>
        <Button
          size="sm"
          variant={target === 'DOCUMENT' ? 'default' : 'outline'}
          onClick={() => {
            setTarget('DOCUMENT');
            setAnchor({ kind: 'WHOLE' });
          }}
          disabled={!workspace.document_artifact_id}
        >
          Итоговый документ
        </Button>
        {workspace.query_artifact_id && project.operation_state === 'IDLE' && (
          <Button
            size="sm"
            variant="ghost"
            disabled={busy}
            onClick={() => void reopenSchema()}
          >
            Изменить подтверждённые данные
          </Button>
        )}
      </div>
      {target === 'QUERY' &&
        (workspace.sql || workspace.query_revisions?.length > 0) && (
          <div className="mt-5 grid grid-cols-1 gap-5 xl:grid-cols-2">
            <div className="min-w-0">
              <h4 className="text-sm font-medium">Части запроса</h4>
              {exportQuery?.compilation_id && (
                <SqlExportButton
                  projectId={project.id}
                  stateVersion={project.state_version}
                  command={{
                    schema_version: '1',
                    kind: 'QUERY',
                    format: 'json',
                    compilation_id: exportQuery.compilation_id,
                    query_artifact_id: exportQuery.id,
                  }}
                  label={`Скачать запрос · версия ${exportQuery.revision}`}
                />
              )}
              {exportQuery && (
                <HistoryIdentity
                  item={exportQuery}
                  compilationId={exportQuery.compilation_id}
                />
              )}
              {exportQuery?.compilation_id && (
                <SqlHistoryContext
                  key={JSON.stringify([
                    project.id,
                    project.state_version,
                    exportQuery.compilation_id,
                    exportQuery.id,
                  ])}
                  projectId={project.id}
                  source={{
                    compilation_id: exportQuery.compilation_id,
                    query_artifact_id: exportQuery.id,
                  }}
                />
              )}
              {workspace.query_revisions?.length > 1 && (
                <div
                  className="mt-2 flex flex-wrap gap-2"
                  aria-label="История запросов"
                >
                  {workspace.query_revisions.map((item) => (
                    <Button
                      key={item.id}
                      size="sm"
                      variant={
                        (selectedQuery?.id || workspace.query_artifact_id) ===
                        item.id
                          ? 'secondary'
                          : 'outline'
                      }
                      onClick={() => {
                        setSelectedQueryId(item.id);
                        setAnchor({ kind: 'WHOLE' });
                      }}
                    >
                      Запрос · версия {item.revision}
                    </Button>
                  ))}
                </div>
              )}
              {(workspace.history?.filter((item) => item.kind === 'QUERY')
                .length || 0) > (workspace.query_revisions?.length || 0) && (
                <p className="mt-2 text-xs text-text-secondary">
                  Версии с другой подтверждённой схемой доступны в истории для
                  просмотра; для доработки требуется вновь подтвердить данные.
                </p>
              )}
              {historicalQuery?.matches_current_context === false && (
                <p className="mt-3 text-xs text-state-warning">
                  У этой версии прежние уточнения или схема. Перед
                  восстановлением проверим SQL по текущим подтверждённым данным.
                </p>
              )}
              <div className="mt-2 max-h-80 space-y-2 overflow-y-auto">
                <Button
                  size="sm"
                  variant={anchor.kind === 'WHOLE' ? 'secondary' : 'outline'}
                  onClick={() => setAnchor({ kind: 'WHOLE' })}
                >
                  Весь запрос
                </Button>
                {selectedBlocks.map((block) => (
                  <button
                    type="button"
                    key={block.id}
                    className={`block w-full rounded-md border p-3 text-start text-xs ${anchor.block_id === block.id ? 'border-accent-primary' : 'border-border-button'}`}
                    onClick={() =>
                      setAnchor({ kind: 'BLOCK', block_id: block.id })
                    }
                  >
                    <span className="font-semibold">
                      {block.title || labels[block.kind] || block.kind}
                    </span>
                    <code className="mt-1 block truncate font-mono">
                      {block.sql}
                    </code>
                  </button>
                ))}
              </div>
            </div>
            <div className="min-w-0">
              <label
                htmlFor="sql-revision-source"
                className="text-sm font-medium"
              >
                Проверенный SQL
              </label>
              <Textarea
                id="sql-revision-source"
                className="mt-2 min-h-72 font-mono text-xs"
                readOnly
                value={selectedSql}
                onSelect={(event) => {
                  const element = event.currentTarget;
                  if (element.selectionEnd > element.selectionStart) {
                    setAnchor({
                      kind: 'TEXT',
                      start: element.selectionStart,
                      end: element.selectionEnd,
                    });
                  }
                }}
              />
              {anchor.kind === 'TEXT' && (
                <p className="mt-1 text-xs text-accent-primary">
                  Комментарий к выделенному фрагменту
                </p>
              )}
              {historicalQuery && (
                <>
                  <SqlQueryComparison
                    current={currentQuery}
                    selected={historicalQuery}
                  />
                  <div className="mt-4 space-y-2">
                    {canEdit && !project.archived_at && (
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={!canRestore}
                        onClick={() => void restoreSelected()}
                      >
                        Восстановить как новую версию
                      </Button>
                    )}
                    <p className="text-xs text-text-secondary">
                      Текущие уточнения и подтверждённые данные сохранятся.
                      Новый запуск выполняется отдельно.
                    </p>
                    {project.operation_state === 'REVIEW' && (
                      <p className="text-xs text-state-warning">
                        Непринятое предложение будет отклонено.
                      </p>
                    )}
                  </div>
                </>
              )}
            </div>
          </div>
        )}
      {target === 'DOCUMENT' && (
        <div className="mt-5">
          {project.documents && project.documents.length > 1 && (
            <div
              className="mb-3 flex flex-wrap gap-2"
              aria-label="История документов"
            >
              {project.documents.map((item) => (
                <Button
                  key={item.id}
                  size="sm"
                  variant={
                    selectedDocument?.id === item.id ? 'secondary' : 'outline'
                  }
                  onClick={() => setSelectedDocumentId(item.id)}
                >
                  Документ · версия {item.revision}
                </Button>
              ))}
            </div>
          )}
          <p className="whitespace-pre-wrap rounded-md bg-bg-accent p-4 text-sm">
            {documentText || 'В сохранённом документе пока нет вывода.'}
          </p>
          {viewingOldDocument && (
            <p className="mt-2 text-xs text-text-secondary">
              Выбрана прежняя версия. Новая текстовая ревизия будет создана на
              её основе.
            </p>
          )}
        </div>
      )}
      {workspace.query_artifact_id || workspace.document_artifact_id ? (
        <div className="mt-5 max-w-3xl">
          <label htmlFor="sql-revision-comment" className="text-sm font-medium">
            Что изменить?
          </label>
          <Textarea
            id="sql-revision-comment"
            readOnly={!canEdit}
            className="mt-2 min-h-24"
            value={comment}
            onChange={(event) => setComment(event.target.value)}
            placeholder="Например: в этом соединении оставь только последние заказы каждого клиента"
          />
          <Button
            className="mt-2"
            size="sm"
            disabled={
              busy ||
              !comment.trim() ||
              project.operation_state === 'RUNNING' ||
              (target === 'QUERY' && !workspace.query_artifact_id) ||
              (target === 'QUERY' &&
                historicalQuery?.matches_current_context === false) ||
              (target === 'DOCUMENT' && !workspace.document_artifact_id)
            }
            onClick={() => void addComment()}
          >
            Добавить комментарий
          </Button>
        </div>
      ) : (
        <p className="mt-5 text-xs text-text-secondary">
          После первого проверенного запроса здесь появятся комментарии к его
          частям.
        </p>
      )}
      {error && (
        <p className="mt-3 text-sm text-state-error" role="alert">
          {error}
        </p>
      )}
      {project.operation_state === 'RUNNING' &&
        project.current_job?.kind === 'REVISION' && (
          <div className="mt-5 flex items-center gap-3">
            <p className="text-sm">ИИ готовит предложение по комментарию…</p>
            <Button
              size="sm"
              variant="outline"
              disabled={busy}
              onClick={() => void cancel()}
            >
              Отменить предложение
            </Button>
          </div>
        )}
      {proposal && proposalResult?.proposal && (
        <div
          className="mt-6 rounded-md border border-accent-primary p-4"
          data-testid="sql-revision-proposal"
        >
          <h4 className="font-semibold">Предложение ИИ</h4>
          <p className="mt-2 text-sm">{proposalResult.proposal.summary}</p>
          {proposalResult.source?.target === 'QUERY' ? (
            <div className="mt-4 space-y-3">
              <div className="grid gap-3 lg:grid-cols-2">
                <div>
                  <p className="text-xs font-medium">SQL выбранной версии</p>
                  <pre className="mt-1 max-h-72 overflow-auto rounded bg-bg-accent p-3 text-xs">
                    {proposalBaseSql}
                  </pre>
                </div>
                <div>
                  <p className="text-xs font-medium">Предложенный SQL</p>
                  <pre className="mt-1 max-h-72 overflow-auto rounded bg-bg-accent p-3 text-xs">
                    {proposalResult.proposal.sql}
                  </pre>
                </div>
              </div>
              {proposalResult.proposal.affected_blocks?.length ? (
                <div>
                  <p className="text-xs font-medium">Затронутые части</p>
                  <ul className="mt-1 space-y-1 text-xs">
                    {proposalResult.proposal.affected_blocks.map(
                      (item, index) => (
                        <li key={`${item.change}-${item.kind}-${index}`}>
                          {item.change === 'ADDED' ? 'Добавлено' : 'Удалено'}:{' '}
                          {labels[item.kind] || item.kind} ·{' '}
                          <code>{item.sql}</code>
                        </li>
                      ),
                    )}
                  </ul>
                </div>
              ) : null}
              {proposalResult.proposal.sql_diff && (
                <div>
                  <p className="text-xs font-medium">Различия в SQL</p>
                  <pre className="mt-1 max-h-72 overflow-auto rounded bg-bg-accent p-3 text-xs">
                    {proposalResult.proposal.sql_diff}
                  </pre>
                </div>
              )}
              {proposalResult.proposal.parameter_diff && (
                <div>
                  <p className="text-xs font-medium">Изменения параметров</p>
                  <pre className="mt-1 max-h-72 overflow-auto rounded bg-bg-accent p-3 text-xs">
                    {proposalResult.proposal.parameter_diff}
                  </pre>
                </div>
              )}
            </div>
          ) : proposalResult.proposal.document_text ? (
            <div className="mt-4 space-y-3">
              <div className="grid gap-3 lg:grid-cols-2">
                <div>
                  <p className="text-xs font-medium">Сохранённый текст</p>
                  <p className="mt-1 whitespace-pre-wrap rounded bg-bg-accent p-3 text-sm">
                    {String(
                      (
                        proposalSourceDocument?.payload
                          ?.confirmed_conclusion as
                          | { text?: string }
                          | undefined
                      )?.text || '',
                    )}
                  </p>
                </div>
                <div>
                  <p className="text-xs font-medium">Предложенный текст</p>
                  <p className="mt-1 whitespace-pre-wrap rounded bg-bg-accent p-3 text-sm">
                    {proposalResult.proposal.document_text}
                  </p>
                </div>
              </div>
              {proposalResult.proposal.text_diff && (
                <pre className="max-h-56 overflow-auto rounded bg-bg-accent p-3 text-xs">
                  {proposalResult.proposal.text_diff}
                </pre>
              )}
            </div>
          ) : null}
          <p className="mt-2 text-xs">
            Проверка:{' '}
            {proposalResult.proposal.checks?.status === 'PASS'
              ? 'пройдена'
              : proposalResult.proposal.checks?.status === 'NEEDS_SCHEMA'
                ? 'нужно подтвердить данные'
                : proposalResult.proposal.checks?.status === 'NEEDS_RUN'
                  ? 'нужен новый запуск запроса'
                  : 'не пройдена'}
            .{' '}
            {proposalResult.source?.target === 'QUERY'
              ? 'Принятие SQL потребует нового запуска запроса.'
              : ''}
          </p>
          {proposalResult.proposal.checks?.reason && (
            <p className="mt-1 text-xs text-state-error">
              {proposalResult.proposal.checks.reason}
            </p>
          )}
          {proposalResult.proposal.requested_sources?.length ? (
            <p className="mt-2 text-xs">
              Нужно подтвердить новые источники:{' '}
              {proposalResult.proposal.requested_sources.join(', ')}
            </p>
          ) : null}
          {proposalNeedsRefresh && (
            <p className="mt-2 text-xs text-state-error">
              После запроса к ИИ обсуждение изменилось. Отклоните предложение и
              запросите новое.
            </p>
          )}
          {proposalResult.source?.target === 'QUERY' &&
            ['BLOCKED', 'NEEDS_SCHEMA'].includes(
              proposalResult.proposal.checks?.status || '',
            ) && (
              <Button
                className="mt-2"
                size="sm"
                variant="outline"
                disabled={busy}
                onClick={() => void reopenSchema()}
              >
                Пересмотреть данные и подтвердить источник
              </Button>
            )}
          {proposalResult.source?.target === 'DOCUMENT' && (
            <label className="mt-3 flex items-start gap-2 text-xs">
              <input
                type="checkbox"
                checked={factsConfirmed}
                onChange={(event) => setFactsConfirmed(event.target.checked)}
              />
              <span>
                Я сравнил текст с сохранённым документом: новые факты и выводы
                не добавлены.
              </span>
            </label>
          )}
          <div className="mt-4 flex gap-2">
            <Button
              size="sm"
              disabled={
                busy ||
                proposalNeedsRefresh ||
                proposalResult.proposal.checks?.status !== 'PASS' ||
                (proposalResult.source?.target === 'DOCUMENT' &&
                  !factsConfirmed)
              }
              onClick={() => void decide('ACCEPT')}
            >
              Принять версию
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={busy}
              onClick={() => void decide('REJECT')}
            >
              Отклонить
            </Button>
          </div>
        </div>
      )}
      <div className="mt-7 space-y-4">
        <h4 className="text-sm font-semibold">Обсуждения</h4>
        {workspace.threads.length === 0 && (
          <p className="text-xs text-text-secondary">Комментариев пока нет.</p>
        )}
        {workspace.threads.map((thread) => (
          <div
            key={thread.id}
            className="rounded-md border border-border-button p-4"
          >
            <p className="text-xs font-medium">
              {thread.target === 'QUERY' ? 'Запрос' : 'Документ'} ·{' '}
              {thread.anchor_status === 'CURRENT'
                ? 'привязка актуальна'
                : 'нужно выбрать место заново'}
            </p>
            {thread.rebindings?.length ? (
              <p className="mt-1 text-xs text-text-secondary">
                Перепривязок: {thread.rebindings.length}
              </p>
            ) : null}
            {thread.messages.map((message) => (
              <div
                key={message.id}
                className={`mt-2 text-sm ${message.role === 'assistant' ? 'border-l-2 border-accent-primary pl-3' : ''}`}
                data-testid="sql-dialogue-message"
              >
                {message.role === 'assistant' && (
                  <p className="text-xs font-medium text-accent-primary">
                    ИИ ·{' '}
                    {message.response_kind === 'CLARIFICATION'
                      ? 'Нужно уточнение'
                      : 'Объяснение'}
                  </p>
                )}
                <p className="whitespace-pre-wrap">{message.text}</p>
                {message.questions?.map((question) => (
                  <div key={question.id} className="mt-2">
                    <p className="font-medium">{question.text}</p>
                    <div className="mt-1 flex flex-wrap gap-2">
                      {question.options.map((option) => (
                        <Button
                          key={option}
                          size="sm"
                          variant="outline"
                          disabled={busy}
                          onClick={() =>
                            setReply((current) => ({
                              ...current,
                              [thread.id]: `${current[thread.id] ? current[thread.id] + '\n' : ''}${question.text}: ${option}`,
                            }))
                          }
                        >
                          {option}
                        </Button>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            ))}
            {thread.proposals?.map((item) => (
              <details key={item.id} className="mt-3 text-xs">
                <summary className="cursor-pointer">
                  Предложение ИИ ·{' '}
                  {item.status === 'ACCEPTED'
                    ? 'принято'
                    : item.status === 'REJECTED'
                      ? 'отклонено'
                      : 'ожидает решения'}{' '}
                  · {item.proposal?.summary || 'без сводки'}
                </summary>
                <pre className="mt-2 max-h-56 overflow-auto rounded bg-bg-accent p-2">
                  {item.proposal?.sql || item.proposal?.document_text || ''}
                </pre>
              </details>
            ))}
            <div className="mt-3 flex flex-wrap gap-2">
              <Button
                size="sm"
                variant="outline"
                disabled={
                  busy ||
                  project.operation_state !== 'IDLE' ||
                  thread.anchor_status !== 'CURRENT'
                }
                onClick={() => void askAgent(thread.id)}
              >
                Предложить изменение через ИИ
              </Button>
              {thread.anchor_status === 'UNRESOLVED' && (
                <Button
                  size="sm"
                  variant="outline"
                  disabled={
                    busy ||
                    project.operation_state !== 'IDLE' ||
                    target !== thread.target
                  }
                  onClick={() =>
                    void perform(async () => {
                      await reattachBusinessDocumentSqlRevisionThread(
                        project.id,
                        thread.id,
                        project.state_version,
                        thread.version,
                        target === 'DOCUMENT' ? { kind: 'WHOLE' } : anchor,
                        key(),
                      );
                    })
                  }
                >
                  Привязать к выбранной части
                </Button>
              )}
              <input
                aria-label="Ответ в обсуждении"
                className="min-w-48 flex-1 rounded-md border border-border-button bg-bg-base px-2 text-sm"
                value={reply[thread.id] || ''}
                onChange={(event) =>
                  setReply((values) => ({
                    ...values,
                    [thread.id]: event.target.value,
                  }))
                }
              />
              <Button
                size="sm"
                variant="ghost"
                disabled={busy || !reply[thread.id]?.trim()}
                onClick={() =>
                  void perform(async () => {
                    await replyBusinessDocumentSqlRevisionThread(
                      project.id,
                      thread.id,
                      thread.version,
                      reply[thread.id],
                      key(),
                    );
                    setReply((values) => ({ ...values, [thread.id]: '' }));
                  })
                }
              >
                Ответить
              </Button>
            </div>
          </div>
        ))}
      </div>
      {workspace.history?.length ? (
        <div className="mt-7 border-t border-border-button pt-5">
          <h4 className="text-sm font-semibold">История принятых версий</h4>
          <div className="mt-3 space-y-2">
            {workspace.history.map((item) => (
              <details
                key={item.id}
                className="rounded-md border border-border-button p-3 text-xs"
              >
                <summary className="cursor-pointer font-medium">
                  {
                    (
                      {
                        REQUIREMENTS: 'Требования',
                        SCHEMA: 'Данные',
                        QUERY: 'Запрос',
                        COMPILATION: 'Проверка SQL',
                        DOCUMENT: 'Документ',
                      } as Record<string, string>
                    )[item.kind]
                  }{' '}
                  · версия {item.revision}
                </summary>
                <HistoryIdentity item={item} />
                {(item.kind === 'REQUIREMENTS' || item.kind === 'SCHEMA') && (
                  <SqlHistoryContext
                    key={JSON.stringify([
                      project.id,
                      project.state_version,
                      item.id,
                    ])}
                    projectId={project.id}
                    source={{ artifact_id: item.id }}
                    label={
                      item.kind === 'REQUIREMENTS'
                        ? 'Полные требования'
                        : 'Полная схема'
                    }
                  />
                )}
                <pre className="mt-2 max-h-60 overflow-auto whitespace-pre-wrap text-text-secondary">
                  {item.preview}
                </pre>
              </details>
            ))}
          </div>
        </div>
      ) : null}
    </section>
  );
}
