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
} from '@/services/business-document-service';
import {
  type ComponentProps,
  useCallback,
  useEffect,
  useRef,
  useState,
} from 'react';

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

export function SqlRevisionWorkspacePanel(
  props: ComponentProps<typeof SqlRevisionWorkspace>,
) {
  return <SqlRevisionWorkspace key={props.project.id} {...props} />;
}

function SqlRevisionWorkspace({
  project,
  onRefresh,
  onSchemaRequested,
}: {
  project: SqlAgentProject;
  onRefresh: () => Promise<void>;
  onSchemaRequested?: (terms: string[]) => void;
}) {
  const [workspace, setWorkspace] = useState<SqlRevisionWorkspace | null>(null);
  const [target, setTarget] = useState<'QUERY' | 'DOCUMENT'>('QUERY');
  const [anchor, setAnchor] = useState<Record<string, unknown>>({
    kind: 'WHOLE',
  });
  const [comment, setComment] = useState('');
  const [reply, setReply] = useState<Record<string, string>>({});
  const [selectedQueryId, setSelectedQueryId] = useState<string | null>(null);
  const [selectedDocumentId, setSelectedDocumentId] = useState<string | null>(
    null,
  );
  const [factsConfirmed, setFactsConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const requestVersion = useRef(0);
  const refresh = useCallback(async () => {
    const version = ++requestVersion.current;
    const next = await fetchBusinessDocumentSqlRevisionWorkspace(project.id);
    if (version === requestVersion.current) setWorkspace(next);
  }, [project.id]);

  useEffect(() => {
    void refresh().catch((nextError) => setError(errorText(nextError)));
    return () => {
      requestVersion.current += 1;
    };
  }, [refresh, project.state_version]);
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
      !workspace.history?.length)
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
    setBusy(true);
    setError(null);
    try {
      await action();
      await refresh();
    } catch (nextError) {
      setError(errorText(nextError));
    } finally {
      setBusy(false);
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
      });
      setComment('');
      setAnchor({ kind: 'WHOLE' });
    });

  const askAgent = (threadId: string) =>
    perform(async () => {
      await requestBusinessDocumentSqlRevision(
        project.id,
        threadId,
        project.state_version,
        key(),
      );
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
          disabled={!workspace.query_artifact_id}
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
      {target === 'QUERY' && workspace.sql && (
        <div className="mt-5 grid gap-5 xl:grid-cols-2">
          <div>
            <h4 className="text-sm font-medium">Части запроса</h4>
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
          <div>
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
              <p key={message.id} className="mt-2 text-sm">
                {message.text}
              </p>
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
