import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
import { Progress } from '@/components/ui/progress';
import { Textarea } from '@/components/ui/textarea';
import type {
  SqlAgentKind,
  SqlAgentProject,
  SqlProjectPreflight,
  SqlProjectPreview,
  SqlQueryCompileResponse,
} from '@/pages/business-documents/types';
import {
  cancelBusinessDocumentSqlRun,
  compileBusinessDocumentSqlProject,
  completeBusinessDocumentSqlProject,
  confirmBusinessDocumentSqlConclusion,
  copyBusinessDocumentSqlAgentProject,
  createBusinessDocumentSqlAgentProject,
  decideBusinessDocumentSqlAgentProposal,
  fetchBusinessDocumentSqlAgentProject,
  listBusinessDocumentSqlAgentProjects,
  preflightBusinessDocumentSqlProject,
  previewBusinessDocumentSqlRun,
  proposeBusinessDocumentSqlConclusion,
  requestBusinessDocumentSqlAgent,
  reviseBusinessDocumentSqlQuestion,
  runBusinessDocumentSqlLookup,
  runBusinessDocumentSqlProject,
  runBusinessDocumentSqlPython,
  updateBusinessDocumentSqlAgentProject,
} from '@/services/business-document-service';
import {
  AlertCircle,
  ArrowRight,
  Check,
  Code2,
  Database,
  FileText,
  LoaderCircle,
  Plus,
  RefreshCw,
  Sparkles,
  Table2,
  X,
} from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation as useUiTranslation } from 'react-i18next';
import { GuidedSqlForm } from './guided-sql-form';
import {
  acceptedRequirementsText,
  queryProposalFromPending,
  requirementsProposal,
} from './sql-agent-document';
import './sql-agent-workbench.css';
import { SqlExportButton } from './sql-export-button';
import {
  PreQueryConnection,
  SqlProjectConnectionPanel,
  useSqlProjectConnection,
} from './sql-project-connection';
import {
  earliestResultDeadline,
  isExpiredResultError,
  resultHasExpired,
  useSqlResultDeadline,
} from './sql-result-deadline';
import { SqlRevisionWorkspacePanel } from './sql-revision-workspace';
import { SqlSchemaGraph, type SqlGraphChangeRequest } from './sql-schema-graph';
import { SqlSchemaReview } from './sql-schema-review';

const STAGES: Array<{
  kind: SqlAgentKind;
  label: string;
}> = [
  {
    kind: 'REQUIREMENTS',
    label: 'Уточнения',
  },
  {
    kind: 'SCHEMA',
    label: 'Данные',
  },
  {
    kind: 'QUERY',
    label: 'Проверка SQL',
  },
];

const KIND_ORDER: Record<SqlAgentKind | 'COMPLETE', number> = {
  REQUIREMENTS: 0,
  SCHEMA: 1,
  QUERY: 2,
  COMPLETE: 3,
};

function commandKey(prefix: string) {
  const suffix =
    typeof crypto !== 'undefined' && 'randomUUID' in crypto
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `${prefix}-${suffix}`;
}

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : 'Операция не выполнена.';
}

function schemaSearchTerms(input: string) {
  return input
    .split(/\r?\n/)
    .map((item) => item.replace(/^\s*[-*•]\s+/, '').trim())
    .filter(Boolean);
}

function requirementsKindLabel(kind: unknown) {
  return (
    {
      output: 'Вывод данных',
      join: 'Соединения',
      filter: 'Фильтрация',
      sort: 'Сортировка',
      limit: 'Ограничения',
      context: 'Контекст',
    }[String(kind)] || 'Требование'
  );
}

function EmptyProjects({ onCreate }: { onCreate: () => void }) {
  return (
    <section className="flex h-full min-h-[420px] items-center justify-center px-6">
      <div className="max-w-lg text-center">
        <div className="mx-auto flex size-12 items-center justify-center rounded-full bg-accent-primary/10 text-accent-primary">
          <Sparkles className="size-5" />
        </div>
        <h2 className="mt-5 text-2xl font-semibold tracking-tight">
          Получите данные по вашему вопросу
        </h2>
        <p className="mt-2 text-sm leading-6 text-text-secondary">
          Опишите задачу своими словами. Мы уточним смысл, проверим источник и
          покажем результат запроса.
        </p>
        <Button className="mt-6" onClick={onCreate}>
          <Plus className="size-4" />
          Новый запрос
        </Button>
      </div>
    </section>
  );
}

function CreateProject({
  busy,
  onCancel,
  onSubmit,
}: {
  busy: boolean;
  onCancel: () => void;
  onSubmit: (title: string, source: string) => Promise<void>;
}) {
  const [title, setTitle] = useState('');
  const [source, setSource] = useState('');
  const valid = source.trim().length > 0;
  return (
    <section
      className="mx-auto w-full max-w-3xl px-6 py-10 lg:px-10"
      data-testid="sql-agent-create-project"
    >
      <p className="text-xs font-medium uppercase tracking-[0.16em] text-accent-primary">
        Новый запрос
      </p>
      <h2 className="mt-2 text-3xl font-semibold tracking-tight">
        Какой результат вам нужен?
      </h2>
      <p className="mt-2 max-w-2xl text-sm leading-6 text-text-secondary">
        Опишите вопрос обычными словами: что посчитать, за какой период и как
        сгруппировать ответ. Имена таблиц и полей знать не нужно — подходящие
        данные найдём по описанию задачи.
      </p>
      <label className="mt-8 block text-sm font-medium">
        Название (необязательно)
        <Input
          className="mt-2"
          value={title}
          maxLength={255}
          placeholder="Например, Активные клиенты по регионам"
          onChange={(event) => setTitle(event.target.value)}
        />
      </label>
      <label className="mt-5 block text-sm font-medium">
        Какой вопрос нужно решить?
        <Textarea
          className="mt-2 min-h-52 leading-6"
          value={source}
          maxLength={20000}
          resize="vertical"
          placeholder="За сентябрь 2026 года покажи по каждому виду импорта глоссария число успешных запусков и сколько записей загружено. Отсортируй по числу запусков."
          onChange={(event) => setSource(event.target.value)}
        />
      </label>
      <div className="mt-6 flex items-center justify-between gap-3 border-t border-border-button pt-5">
        <span className="text-xs text-text-disabled">
          {source.length.toLocaleString('ru-RU')} / 20 000 символов
        </span>
        <div className="flex gap-2">
          <Button variant="outline" onClick={onCancel} disabled={busy}>
            Отмена
          </Button>
          <Button
            disabled={!valid || busy}
            onClick={() =>
              onSubmit(
                title.trim() || source.trim().slice(0, 80),
                source.trim(),
              )
            }
          >
            {busy ? (
              <LoaderCircle className="size-4 animate-spin" />
            ) : (
              <ArrowRight className="size-4" />
            )}
            Создать и продолжить
          </Button>
        </div>
      </div>
    </section>
  );
}

function RequirementsReview({
  project,
  busy,
  onDecision,
}: {
  project: SqlAgentProject;
  busy: boolean;
  onDecision: (
    decision: 'ACCEPT' | 'REJECT',
    payload: Record<string, unknown> | null,
  ) => Promise<void>;
}) {
  const { requirements, questions } = requirementsProposal(project);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  useEffect(() => setAnswers({}), [project.pending_proposal?.id]);
  const blockingAnswered = questions.every(
    (question) =>
      question.blocking !== true ||
      Boolean(answers[String(question.id)]?.trim()),
  );
  return (
    <div className="space-y-8" data-testid="sql-agent-requirements-review">
      <section>
        <div className="flex items-center justify-between gap-3">
          <div>
            <p className="text-xs font-medium uppercase tracking-[0.14em] text-accent-primary">
              Предложение агента требований
            </p>
            <h3 className="mt-1 text-xl font-semibold">
              Проверьте формулировки
            </h3>
          </div>
          <Badge variant="outline">{requirements.length} требований</Badge>
        </div>
        <div className="mt-5 divide-y divide-border-button border-y border-border-button">
          {requirements.map((item) => (
            <div
              key={String(item.id)}
              className="grid gap-2 py-4 sm:grid-cols-[140px_1fr]"
            >
              <span className="text-xs font-medium text-text-secondary">
                {requirementsKindLabel(item.kind)}
              </span>
              <div>
                <p className="text-sm leading-6">{String(item.statement)}</p>
                {item.rationale ? (
                  <p className="mt-1 text-xs leading-5 text-text-disabled">
                    {String(item.rationale)}
                  </p>
                ) : null}
              </div>
            </div>
          ))}
        </div>
      </section>

      {questions.length > 0 && (
        <section>
          <h3 className="text-base font-semibold">Нужно уточнить</h3>
          <p className="mt-1 text-sm text-text-secondary">
            Ответы станут частью подтверждённых требований.
          </p>
          <div className="mt-4 space-y-6">
            {questions.map((question, index) => {
              const id = String(question.id);
              const options: string[] = Array.isArray(question.options)
                ? (question.options as unknown[]).map((option) =>
                    String(option),
                  )
                : [];
              return (
                <div key={id} className="border-s-2 border-border-button ps-4">
                  <p className="text-sm font-medium">
                    {index + 1}. {String(question.question)}
                    {question.blocking === true && (
                      <span className="ms-1 text-state-error">*</span>
                    )}
                  </p>
                  {question.reason ? (
                    <p className="mt-1 text-xs text-text-disabled">
                      {String(question.reason)}
                    </p>
                  ) : null}
                  <div className="mt-3 flex flex-wrap gap-2">
                    {options.map((option) => (
                      <button
                        key={option}
                        type="button"
                        className={`rounded-md border px-3 py-1.5 text-xs transition-colors ${
                          answers[id] === option
                            ? 'border-accent-primary bg-accent-primary/10 text-accent-primary'
                            : 'border-border-button hover:border-border-default'
                        }`}
                        onClick={() =>
                          setAnswers((current) => ({
                            ...current,
                            [id]: option,
                          }))
                        }
                      >
                        {option}
                      </button>
                    ))}
                  </div>
                  {question.allow_custom_answer === true && (
                    <Input
                      className="mt-3"
                      aria-label={`Ответ на вопрос ${index + 1}`}
                      value={answers[id] ?? ''}
                      placeholder="Или укажите свой вариант"
                      onChange={(event) =>
                        setAnswers((current) => ({
                          ...current,
                          [id]: event.target.value,
                        }))
                      }
                    />
                  )}
                </div>
              );
            })}
          </div>
        </section>
      )}

      <DecisionBar
        busy={busy}
        acceptDisabled={!requirements.length || !blockingAnswered}
        acceptLabel="Подтвердить требования"
        onAccept={() => onDecision('ACCEPT', { answers })}
        onReject={() => onDecision('REJECT', null)}
      />
    </div>
  );
}

function QueryReview({
  project,
  busy,
  onDecision,
}: {
  project: SqlAgentProject;
  busy: boolean;
  onDecision: (
    decision: 'ACCEPT' | 'REJECT',
    payload: Record<string, unknown> | null,
  ) => Promise<void>;
}) {
  const proposal = queryProposalFromPending(project);
  const [confirmedJoinIds, setConfirmedJoinIds] = useState<string[]>([]);
  const [confirmedFilterIds, setConfirmedFilterIds] = useState<string[]>([]);
  if (!proposal) {
    return (
      <p className="border-s-2 border-state-error ps-3 text-sm text-state-error">
        Агент не сформировал проверяемое предложение. Отклоните его и повторите
        шаг.
      </p>
    );
  }
  const selectedTables = (
    (
      project.artifacts?.schema?.schema_snapshot as
        | {
            requirements?: Array<{
              selected_table?: {
                id: string;
                display_name?: string;
                name: string;
                columns: Array<{
                  id: string;
                  description?: string;
                  name: string;
                }>;
              };
            }>;
          }
        | undefined
    )?.requirements || []
  ).flatMap((item) => (item.selected_table ? [item.selected_table] : []));
  const fieldName = (columnId: string) => {
    const column = selectedTables
      .flatMap((table) => table.columns)
      .find((item) => item.id === columnId);
    const description = column?.description?.split(/[.;]/)[0].trim();
    return description && !description.startsWith('Поле `')
      ? description
      : column?.name || columnId.split('.').at(-1);
  };
  const selectName = new Map(
    proposal.select.map((item) => [item.id, item.alias]),
  );
  return (
    <div className="space-y-7" data-testid="sql-agent-query-review">
      <div>
        <p className="text-xs font-medium uppercase tracking-[0.14em] text-accent-primary">
          Проверка ответа
        </p>
        <h3 className="mt-1 text-xl font-semibold">Что будет посчитано</h3>
        <p className="mt-1 text-sm text-text-secondary">
          Сверьте показатели, разбивку и условия с вашим вопросом. Затем система
          подготовит и проверит запрос.
        </p>
      </div>
      <div className="grid gap-7 lg:grid-cols-2">
        <section>
          <h4 className="flex items-center gap-2 text-sm font-semibold">
            <Table2 className="size-4" /> Столбцы ответа
          </h4>
          <div className="mt-3 divide-y divide-border-button border-y border-border-button">
            {proposal.select.map((item) => (
              <div key={item.id} className="py-3 text-xs">
                <span className="font-medium">
                  {resultOperationLabel(item.kind)}
                </span>
                <span className="ms-2 text-text-secondary">
                  {fieldName(item.column_id)}
                </span>
                <span className="mt-1 block text-text-disabled">
                  Столбец результата: {item.alias}
                </span>
              </div>
            ))}
          </div>
        </section>
        <section>
          <h4 className="flex items-center gap-2 text-sm font-semibold">
            <Database className="size-4" /> Связанные данные
          </h4>
          <div className="mt-3 space-y-3">
            {proposal.joins.length ? (
              proposal.joins.map((join) => (
                <div
                  key={join.id}
                  className="border-s-2 border-border-button ps-3 text-xs"
                >
                  <p className="font-medium">
                    {join.join_type} ·{' '}
                    {selectedTables.find((table) => table.id === join.entity_id)
                      ?.display_name || join.entity_id}
                  </p>
                  <p className="mt-1 break-all text-text-secondary">
                    {join.left_column_id} = {join.right_column_id}
                  </p>
                  <p className="mt-1 text-text-disabled">{join.description}</p>
                  <label className="mt-2 flex items-start gap-2 text-text-secondary">
                    <Checkbox
                      checked={confirmedJoinIds.includes(join.id)}
                      onCheckedChange={(checked) =>
                        setConfirmedJoinIds((current) =>
                          checked
                            ? [...current, join.id]
                            : current.filter((id) => id !== join.id),
                        )
                      }
                    />
                    <span>
                      Подтверждаю связь: она может изменить число строк.
                    </span>
                  </label>
                </div>
              ))
            ) : (
              <p className="text-xs text-text-disabled">
                Соединения не требуются.
              </p>
            )}
          </div>
        </section>
        <section>
          <h4 className="flex items-center gap-2 text-sm font-semibold">
            <Code2 className="size-4" /> Условия отбора
          </h4>
          <div className="mt-3 space-y-3">
            {proposal.filters.length ? (
              proposal.filters.map((filter) => (
                <div
                  key={filter.id}
                  className="border-s-2 border-border-button ps-3 text-xs"
                >
                  <p className="font-medium">{filter.description}</p>
                  <details className="mt-1 text-text-secondary">
                    <summary className="cursor-pointer">
                      Техническое условие
                    </summary>
                    <p className="mt-1 break-all font-mono">
                      {filter.column_id} {filter.operator}{' '}
                      {filter.parameter_name ? `:${filter.parameter_name}` : ''}
                    </p>
                  </details>
                  <label className="mt-2 flex items-start gap-2 text-text-secondary">
                    <Checkbox
                      checked={confirmedFilterIds.includes(filter.id)}
                      onCheckedChange={(checked) =>
                        setConfirmedFilterIds((current) =>
                          checked
                            ? [...current, filter.id]
                            : current.filter((id) => id !== filter.id),
                        )
                      }
                    />
                    <span>Подтверждаю исключение строк этим условием.</span>
                  </label>
                </div>
              ))
            ) : (
              <p className="text-xs text-text-disabled">Фильтры не заданы.</p>
            )}
          </div>
        </section>
        <section>
          <h4 className="text-sm font-semibold">Ограничения</h4>
          <dl className="mt-3 grid grid-cols-[120px_1fr] gap-y-2 border-y border-border-button py-3 text-xs">
            <dt className="text-text-secondary">Лимит</dt>
            <dd>{proposal.row_limit} строк</dd>
            {proposal.top_per_group && (
              <>
                <dt className="text-text-secondary">В каждой группе</dt>
                <dd>
                  Лимит строк: {proposal.top_per_group.row_limit}. Группы:{' '}
                  {proposal.top_per_group.partition_by
                    .map((id) => selectName.get(id) || id)
                    .join(', ')}
                  . Порядок выбора:{' '}
                  {proposal.top_per_group.order_by
                    .map(
                      (item) =>
                        `${selectName.get(item.select_item_id) || item.select_item_id} ${item.direction === 'DESC' ? 'по убыванию' : 'по возрастанию'}`,
                    )
                    .join(', ')}
                  .
                </dd>
              </>
            )}
            <dt className="text-text-secondary">Сортировка</dt>
            <dd>
              {proposal.order_by
                .map(
                  (item) =>
                    `${selectName.get(item.select_item_id) || item.select_item_id} ${item.direction === 'DESC' ? 'по убыванию' : 'по возрастанию'}`,
                )
                .join(', ') || 'не задана'}
            </dd>
          </dl>
        </section>
      </div>
      <DecisionBar
        busy={busy}
        acceptDisabled={
          confirmedJoinIds.length !== proposal.joins.length ||
          confirmedFilterIds.length !== proposal.filters.length
        }
        acceptLabel="Подтвердить и собрать SQL"
        onAccept={() =>
          onDecision('ACCEPT', {
            confirmed_join_ids: confirmedJoinIds,
            confirmed_filter_ids: confirmedFilterIds,
          })
        }
        onReject={() => onDecision('REJECT', null)}
      />
    </div>
  );
}

function resultOperationLabel(kind: string) {
  return (
    (
      {
        column: 'Поле',
        count: 'Количество',
        count_distinct: 'Количество уникальных значений',
        sum: 'Сумма',
        avg: 'Среднее',
        min: 'Минимум',
        max: 'Максимум',
        date_bucket: 'Период',
      } as Record<string, string>
    )[kind] || kind
  );
}

function DecisionBar({
  busy,
  acceptDisabled,
  acceptLabel,
  onAccept,
  onReject,
}: {
  busy: boolean;
  acceptDisabled: boolean;
  acceptLabel: string;
  onAccept: () => void | Promise<void>;
  onReject: () => void | Promise<void>;
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border-button pt-5">
      <Button variant="ghost" disabled={busy} onClick={onReject}>
        <X className="size-4" />
        Отклонить и повторить
      </Button>
      <Button disabled={busy || acceptDisabled} onClick={onAccept}>
        {busy ? (
          <LoaderCircle className="size-4 animate-spin" />
        ) : (
          <Check className="size-4" />
        )}
        {acceptLabel}
      </Button>
    </div>
  );
}

function loadResultPreview(
  projectId: string,
  runId: string,
  lifetime: ReturnType<typeof useSqlResultDeadline>,
  onData: (value: SqlProjectPreview) => void,
  onFailure: (failure: unknown) => void,
) {
  const controller = new AbortController();
  void previewBusinessDocumentSqlRun(projectId, runId, 0, controller.signal)
    .then((result) => {
      if (
        controller.signal.aborted ||
        lifetime.isExpired() ||
        result.run_id !== runId
      )
        return;
      if (resultHasExpired(result.expires_at)) {
        lifetime.expire();
        return;
      }
      onData(result);
    })
    .catch((failure) => {
      if (controller.signal.aborted || lifetime.isExpired()) return;
      if (isExpiredResultError(failure)) lifetime.expire();
      else onFailure(failure);
    });
  return () => controller.abort();
}

function CompletedProject({
  project,
  compiled,
  compileError,
  onRefresh,
  onExpiryRefresh,
}: {
  project: SqlAgentProject;
  compiled: SqlQueryCompileResponse | null;
  compileError: string | null;
  onRefresh: () => Promise<void>;
  onExpiryRefresh: () => Promise<void>;
}) {
  const { t: tUi, i18n } = useUiTranslation();
  const [showSql, setShowSql] = useState(false);
  const [showRepeat, setShowRepeat] = useState(false);
  const [preflight, setPreflight] = useState<SqlProjectPreflight | null>(null);
  const [preflightError, setPreflightError] = useState<string | null>(null);
  const [preflightEpoch, setPreflightEpoch] = useState(0);
  const connection = useSqlProjectConnection(
    project,
    onRefresh,
    preflight?.binding,
  );
  const { selectedProfileId, binding, canConfigure } = connection;
  const canEdit = !project.archived_at && project.capabilities.edit !== false;
  useEffect(() => {
    const restored = (event: PageTransitionEvent) => {
      if (event.persisted) setPreflightEpoch((value) => value + 1);
    };
    window.addEventListener('pageshow', restored);
    return () => window.removeEventListener('pageshow', restored);
  }, []);
  const [storedPreview, setPreview] = useState<SqlProjectPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  useEffect(() => setActionError(null), [project.id, project.state_version]);
  const [pythonOpen, setPythonOpen] = useState(false);
  const [pythonCode, setPythonCode] = useState(
    'def main(columns, rows):\n    return {"columns": columns, "rows": rows}\n',
  );
  const [storedPythonPreview, setPythonPreview] =
    useState<SqlProjectPreview | null>(null);
  const [lookupOpen, setLookupOpen] = useState(false);
  const [lookupSourceColumn, setLookupSourceColumn] = useState('');
  const [lookupTargetId, setLookupTargetId] = useState('');
  const [lookupKeyId, setLookupKeyId] = useState('');
  const [lookupValueId, setLookupValueId] = useState('');
  const [storedLookupPreview, setLookupPreview] =
    useState<SqlProjectPreview | null>(null);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const pageRequest = useRef<AbortController | null>(null);
  const [conclusionText, setConclusionText] = useState('');
  const run =
    project.runs?.find((item) => item.id === selectedRunId) ||
    project.latest_run;
  const runId = run?.id;
  const runStatus = run?.status;
  useEffect(() => setSelectedRunId(null), [project.id]);
  const document = project.documents
    ? project.documents.find((item) => item.payload?.run_id === runId)
    : project.document;
  const conclusion =
    project.conclusions?.find((item) => item.payload.source_run_id === runId) ||
    (project.latest_conclusion?.payload.source_run_id === runId
      ? project.latest_conclusion
      : null);
  const rootDeadline = earliestResultDeadline(
    run?.expires_at,
    storedPreview && storedPreview.run_id === runId
      ? storedPreview.expires_at
      : undefined,
  );
  const rootLifetime = useSqlResultDeadline(
    `${project.id}:${runId || ''}:${runStatus || ''}`,
    rootDeadline,
    () => {
      setPreview(null);
      setPythonPreview(null);
      setLookupPreview(null);
      setConclusionText('');
      setPythonOpen(false);
      setLookupOpen(false);
      pageRequest.current?.abort();
      if (runStatus === 'READY') void onExpiryRefresh();
    },
  );
  const rootExpired = run?.status === 'EXPIRED' || rootLifetime.expired;
  useEffect(
    () => () => pageRequest.current?.abort(),
    [project.id, runId, runStatus],
  );
  const handleResultFailure = (failure: unknown) => {
    if (isExpiredResultError(failure)) rootLifetime.expire();
    else setActionError(errorMessage(failure));
  };
  const pythonRun = project.derived_runs?.find(
    (item) =>
      item.kind === 'PYTHON' &&
      item.status === 'READY' &&
      item.source_run_id === runId,
  );
  const lookupRun = project.derived_runs?.find(
    (item) =>
      item.kind === 'LOOKUP' &&
      item.status === 'READY' &&
      item.source_run_id === runId,
  );
  const pythonLifetime = useSqlResultDeadline(
    `${project.id}:${pythonRun?.id || ''}`,
    pythonRun
      ? earliestResultDeadline(
          rootDeadline,
          pythonRun.expires_at,
          storedPythonPreview?.run_id === pythonRun.id
            ? storedPythonPreview.expires_at
            : undefined,
        )
      : undefined,
    () => {
      setPythonPreview(null);
      if (!rootLifetime.isExpired() && runStatus === 'READY')
        void onExpiryRefresh();
    },
  );
  const lookupLifetime = useSqlResultDeadline(
    `${project.id}:${lookupRun?.id || ''}`,
    lookupRun
      ? earliestResultDeadline(
          rootDeadline,
          lookupRun.expires_at,
          storedLookupPreview?.run_id === lookupRun.id
            ? storedLookupPreview.expires_at
            : undefined,
        )
      : undefined,
    () => {
      setLookupPreview(null);
      if (!rootLifetime.isExpired() && runStatus === 'READY')
        void onExpiryRefresh();
    },
  );
  const canChangeResult =
    canEdit && !connection.locked && !busy && !rootExpired;
  const preview =
    !rootExpired && runStatus === 'READY' && storedPreview?.run_id === runId
      ? storedPreview
      : null;
  const pythonPreview =
    !rootExpired &&
    !pythonLifetime.expired &&
    storedPythonPreview?.run_id === pythonRun?.id
      ? storedPythonPreview
      : null;
  const lookupPreview =
    !rootExpired &&
    !lookupLifetime.expired &&
    storedLookupPreview?.run_id === lookupRun?.id
      ? storedLookupPreview
      : null;
  useEffect(() => {
    setConclusionText(rootExpired ? '' : conclusion?.payload.text || '');
  }, [rootExpired, conclusion?.id, conclusion?.payload.text]);
  const acceptedTableIds = new Set(
    (
      project.artifacts?.schema?.accepted_schema as
        | Array<{ entity_id: string }>
        | undefined
    )?.map((item) => item.entity_id) || [],
  );
  const lookupTables = (
    (
      project.artifacts?.schema?.schema_snapshot as
        | {
            requirements?: Array<{
              selected_table?: {
                id: string;
                name: string;
                columns: Array<{ id: string; name: string }>;
              };
            }>;
          }
        | undefined
    )?.requirements || []
  )
    .map((item) => item.selected_table)
    .filter(
      (
        item,
      ): item is {
        id: string;
        name: string;
        columns: Array<{ id: string; name: string }>;
      } => Boolean(item && acceptedTableIds.has(item.id)),
    );
  const lookupTable = lookupTables.find((item) => item.id === lookupTargetId);
  useEffect(() => {
    if (!compiled?.sql) return;
    let active = true;
    const controller = new AbortController();
    const abort = () => controller.abort();
    window.addEventListener('pagehide', abort);
    setPreflight(null);
    setPreflightError(null);
    void preflightBusinessDocumentSqlProject(
      project.id,
      selectedProfileId,
      controller.signal,
    )
      .then((result) => {
        if (active && !controller.signal.aborted) {
          setPreflight(result);
        }
      })
      .catch((error) => {
        if (active && !controller.signal.aborted)
          setPreflightError(errorMessage(error));
      });
    return () => {
      active = false;
      controller.abort();
      window.removeEventListener('pagehide', abort);
    };
  }, [
    project.id,
    project.state_version,
    compiled?.sql,
    selectedProfileId,
    document?.id,
    preflightEpoch,
    project.capabilities.execute,
    project.capabilities.edit,
  ]);
  useEffect(() => {
    if (!runId || runStatus !== 'READY' || rootExpired) {
      setPreview(null);
      return;
    }
    return loadResultPreview(
      project.id,
      runId,
      rootLifetime,
      setPreview,
      (failure) => setActionError(errorMessage(failure)),
    );
  }, [project.id, runId, runStatus, rootExpired, rootLifetime]);
  useEffect(() => {
    if (!pythonRun || rootExpired || pythonLifetime.expired) {
      setPythonPreview(null);
      return;
    }
    return loadResultPreview(
      project.id,
      pythonRun.id,
      pythonLifetime,
      setPythonPreview,
      (failure) => setActionError(errorMessage(failure)),
    );
  }, [project.id, pythonRun, rootExpired, pythonLifetime]);
  useEffect(() => {
    if (!lookupRun || rootExpired || lookupLifetime.expired) {
      setLookupPreview(null);
      return;
    }
    return loadResultPreview(
      project.id,
      lookupRun.id,
      lookupLifetime,
      setLookupPreview,
      (failure) => setActionError(errorMessage(failure)),
    );
  }, [project.id, lookupRun, rootExpired, lookupLifetime]);
  const canRun = Boolean(
    compiled?.sql &&
    canConfigure &&
    preflight?.binding.status === 'BOUND' &&
    preflight.binding.selection?.profile.id &&
    !preflight?.blocker &&
    connection.canUseSelection &&
    !connection.locked &&
    !busy,
  );
  const execute = async () => {
    if (!canRun) return;
    setBusy(true);
    setActionError(null);
    try {
      await runBusinessDocumentSqlProject(
        project.id,
        project.state_version,
        commandKey('sql-run'),
        selectedProfileId,
      );
      setSelectedRunId(null);
      await onRefresh();
    } catch (error) {
      handleResultFailure(error);
    } finally {
      setBusy(false);
    }
  };
  const finish = async (runId?: string) => {
    if (
      !canChangeResult ||
      !compiled ||
      (runId &&
        (run?.id !== runId ||
          run.status !== 'READY' ||
          run.compilation_id !== project.compilation?.id))
    )
      return;
    setBusy(true);
    setActionError(null);
    try {
      await completeBusinessDocumentSqlProject(
        project.id,
        project.state_version,
        commandKey('sql-complete'),
        runId,
      );
      setPreview(null);
      await onRefresh();
    } catch (error) {
      handleResultFailure(error);
    } finally {
      setBusy(false);
    }
  };
  const cancelResult = async () => {
    if (
      !canEdit ||
      busy ||
      !run ||
      !['READY', 'QUEUED', 'RUNNING'].includes(run.status)
    )
      return;
    setBusy(true);
    setActionError(null);
    try {
      await cancelBusinessDocumentSqlRun(
        project.id,
        project.state_version,
        commandKey('sql-cancel'),
        run.id,
      );
      setPreview(null);
      await onRefresh();
    } catch (error) {
      handleResultFailure(error);
    } finally {
      setBusy(false);
    }
  };
  const moreRows = async () => {
    if (!run || !preview) return;
    pageRequest.current?.abort();
    const controller = new AbortController();
    pageRequest.current = controller;
    try {
      const next = await previewBusinessDocumentSqlRun(
        project.id,
        run.id,
        preview.rows.length,
        controller.signal,
      );
      if (controller.signal.aborted || rootLifetime.isExpired()) return;
      if (resultHasExpired(next.expires_at)) {
        rootLifetime.expire();
        return;
      }
      if (next.run_id === run.id)
        setPreview({
          ...next,
          rows: [...preview.rows, ...next.rows],
          offset: 0,
        });
    } catch (error) {
      if (controller.signal.aborted || rootLifetime.isExpired()) return;
      if (isExpiredResultError(error)) rootLifetime.expire();
      else handleResultFailure(error);
    }
  };
  const runPython = async () => {
    if (!canChangeResult || !run || run.status !== 'READY') return;
    setBusy(true);
    setActionError(null);
    try {
      const result = await runBusinessDocumentSqlPython(
        project.id,
        run.id,
        project.state_version,
        commandKey('sql-python'),
        pythonCode,
      );
      if (rootLifetime.isExpired()) return;
      const next = await previewBusinessDocumentSqlRun(
        project.id,
        result.run_id,
      );
      if (rootLifetime.isExpired()) return;
      if (next.run_id !== result.run_id) return;
      if (resultHasExpired(next.expires_at)) {
        setPythonPreview(null);
        return;
      }
      setPythonPreview(next);
      await onRefresh();
    } catch (error) {
      handleResultFailure(error);
    } finally {
      setBusy(false);
    }
  };
  const runLookup = async () => {
    if (!canChangeResult || !canConfigure || !run || run.status !== 'READY')
      return;
    setBusy(true);
    setActionError(null);
    try {
      const result = await runBusinessDocumentSqlLookup(
        project.id,
        run.id,
        project.state_version,
        commandKey('sql-lookup'),
        {
          source_column: lookupSourceColumn,
          target_entity_id: lookupTargetId,
          target_key_column_id: lookupKeyId,
          target_value_column_ids: [lookupValueId],
        },
      );
      if (rootLifetime.isExpired()) return;
      const next = await previewBusinessDocumentSqlRun(
        project.id,
        result.run_id,
      );
      if (rootLifetime.isExpired()) return;
      if (next.run_id !== result.run_id) return;
      if (resultHasExpired(next.expires_at)) {
        setLookupPreview(null);
        return;
      }
      setLookupPreview(next);
      await onRefresh();
    } catch (error) {
      handleResultFailure(error);
    } finally {
      setBusy(false);
    }
  };
  const generateConclusion = async () => {
    if (!canChangeResult || !run || run.status !== 'READY') return;
    setBusy(true);
    setActionError(null);
    try {
      await proposeBusinessDocumentSqlConclusion(
        project.id,
        run.id,
        project.state_version,
        commandKey('sql-conclusion'),
      );
      await onRefresh();
    } catch (error) {
      handleResultFailure(error);
    } finally {
      setBusy(false);
    }
  };
  const confirmConclusion = async () => {
    if (
      !canChangeResult ||
      !run ||
      run.status !== 'READY' ||
      !conclusion ||
      conclusion.payload.status !== 'DRAFT'
    )
      return;
    setBusy(true);
    setActionError(null);
    try {
      await confirmBusinessDocumentSqlConclusion(
        project.id,
        run.id,
        project.state_version,
        commandKey('sql-conclusion-confirm'),
        conclusion.id,
        conclusionText,
      );
      await onRefresh();
    } catch (error) {
      handleResultFailure(error);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div id="sql-next-action" data-testid="sql-agent-complete">
      {project.artifact_ids.schema && (
        <SqlProjectConnectionPanel
          connection={connection}
          busy={busy}
          hasResult={run?.status === 'READY'}
          resultSource={
            run?.profile_id
              ? {
                  profile_id: run.profile_id,
                  profile_name: run.profile_name,
                  profile_version: run.profile_version,
                }
              : undefined
          }
        >
          <p className="text-xs text-text-secondary">
            {binding?.status === 'BOUND'
              ? `Выбран профиль: ${binding.selection?.profile.name}`
              : binding?.status === 'NEEDS_SELECTION'
                ? 'Подходит несколько источников. Выберите один.'
                : binding?.status === 'UNAVAILABLE'
                  ? 'Доступного подключения нет. Автоматическая подмена источника не выполняется.'
                  : preflightError
                    ? 'Проверка запроса не завершена.'
                    : compiled?.sql
                      ? 'Проверяем запрос и доступ к источнику…'
                      : 'Исправьте SQL для проверки запроса. Подключение можно проверить отдельно.'}
          </p>
          {preflightError && (
            <div className="space-y-2">
              <p role="alert" className="text-sm text-state-error">
                {preflightError}
              </p>
              <Button
                variant="outline"
                disabled={busy}
                onClick={() => setPreflightEpoch((value) => value + 1)}
              >
                Повторить проверку запроса
              </Button>
            </div>
          )}
          {preflight?.blocker && (
            <p className="text-sm text-state-error">
              {preflight.blocker.message}
            </p>
          )}
          {preflight?.warning && (
            <p className="text-xs text-text-secondary">
              Каталог давно не обновлялся; перед запуском схема PostgreSQL
              проверена напрямую.
            </p>
          )}
        </SqlProjectConnectionPanel>
      )}
      {project.runs && project.runs.length > 1 && (
        <section className="mb-6 border-b border-border-button pb-4">
          <h4 className="text-sm font-semibold">История запусков</h4>
          <div className="mt-2 flex flex-wrap gap-2">
            {project.runs.map((item, index) => (
              <Button
                key={item.id}
                size="sm"
                variant={run?.id === item.id ? 'default' : 'outline'}
                onClick={() => setSelectedRunId(item.id)}
              >
                Запуск {project.runs!.length - index} ·{' '}
                {item.status === 'EXPIRED' ||
                (item.status === 'READY' && resultHasExpired(item.expires_at))
                  ? 'срок хранения истёк'
                  : item.status === 'READY'
                    ? 'данные доступны'
                    : item.status === 'FAILED'
                      ? 'ошибка'
                      : item.status === 'PURGED'
                        ? 'строки удалены'
                        : 'выполняется'}
              </Button>
            ))}
          </div>
        </section>
      )}
      <p className="text-xs font-medium uppercase tracking-[0.14em] text-accent-primary">
        {rootExpired
          ? 'Срок хранения'
          : run?.status === 'READY'
            ? 'Результат'
            : run &&
                ['QUEUED', 'RUNNING', 'CANCEL_REQUESTED'].includes(run.status)
              ? 'Выполнение'
              : document
                ? 'Завершённый проект'
                : 'Проверка и запуск'}
      </p>
      <h3 className="mt-1 text-2xl font-semibold">
        {rootExpired
          ? 'Срок хранения результата истёк'
          : run?.status === 'READY'
            ? 'Данные получены'
            : run &&
                ['QUEUED', 'RUNNING', 'CANCEL_REQUESTED'].includes(run.status)
              ? 'Запрос выполняется'
              : document
                ? 'Документ сохранён, строки удалены'
                : 'Проверьте запрос перед запуском'}
      </h3>
      {run?.status === 'READY' &&
        run.compilation_id !== project.compilation?.id && (
          <p
            className="mt-3 border-s-2 border-state-warning ps-3 text-sm"
            role="status"
            data-testid="sql-historical-result"
          >
            Этот результат получен для предыдущей проверенной версии запроса.
            Текущий SQL можно изменить и выполнить отдельно; строки и источник
            сохранённого результата остаются прежними.
          </p>
        )}
      <p className="mt-2 text-sm text-text-secondary">
        {rootExpired
          ? 'Строки удалены. Чтобы получить данные заново, выполните запрос. Сохранённые SQL и документы доступны.'
          : run?.status === 'READY'
            ? `${run.row_count} строк · ${run.duration_ms} мс · ${run.checks?.completeness === 'LIMITED' ? 'Показан ограниченный набор' : 'Полный набор в пределах запроса'} · проверка результата пройдена. Строки доступны ${rootDeadline ? `до ${new Date(rootDeadline).toLocaleString(i18n.resolvedLanguage || i18n.language)}` : 'временно'} или до завершения проекта.`
            : run?.status === 'FAILED'
              ? `Данные не получены: ${run.error?.message || 'проверка или выполнение завершились ошибкой'}.`
              : run &&
                  ['QUEUED', 'RUNNING', 'CANCEL_REQUESTED'].includes(run.status)
                ? 'Ожидаем результат. Можно отменить выполнение.'
                : document
                  ? `Ревизия ${document.revision}. Повторный запрос создаст новый запуск и документ.`
                  : compileError
                    ? 'SQL требует исправления. Подключение можно проверить отдельно.'
                    : 'SQL проверен. Данные ещё не получены.'}
      </p>
      {compileError && (
        <p className="mt-6 border-s-2 border-state-error ps-3 text-sm text-state-error">
          {compileError}
        </p>
      )}
      {!compiled && !compileError && (
        <div className="mt-8 flex items-center gap-2 text-sm text-text-secondary">
          <LoaderCircle className="size-4 animate-spin" />
          Компилируем SQL…
        </div>
      )}
      {actionError && (
        <p className="mt-5 border-s-2 border-state-error ps-3 text-sm text-state-error">
          {actionError}
        </p>
      )}
      {document && run?.status !== 'READY' && (
        <section
          className="mt-6 border-y border-border-button py-5"
          data-testid="sql-completed-document"
        >
          <h4 className="text-sm font-semibold">
            Документ · ревизия {document.revision}
          </h4>
          <p className="mt-2 text-sm text-text-secondary">
            {String(
              document.payload?.requirements || project.source_request || '',
            )}
          </p>
          {document.payload?.execution_status === 'NOT_EXECUTED' ? (
            <p className="mt-2 text-xs text-text-secondary">
              SQL проверен. Запрос не выполнялся; результат и Python-обработка
              не получены.
            </p>
          ) : (
            <p className="mt-2 text-xs text-text-secondary">
              Получено {String(document.payload?.row_count ?? '—')} строк ·
              результат{' '}
              {(
                document.payload?.checks as
                  | { completeness?: string }
                  | undefined
              )?.completeness === 'LIMITED'
                ? 'ограничен'
                : 'полный в пределах запроса'}{' '}
              · строки удалены.
            </p>
          )}
          {(
            document.payload?.confirmed_conclusion as
              | { text?: string }
              | undefined
          )?.text && (
            <p className="mt-3 text-sm">
              {
                (document.payload?.confirmed_conclusion as { text: string })
                  .text
              }
            </p>
          )}
          {canEdit && (
            <Button
              className="mt-4"
              size="sm"
              variant="outline"
              onClick={() =>
                window.document
                  .getElementById('sql-revision-workspace')
                  ?.scrollIntoView({ behavior: 'smooth' })
              }
            >
              Доработать
            </Button>
          )}
        </section>
      )}
      {canEdit && run && ['QUEUED', 'RUNNING'].includes(run.status) && (
        <Button
          className="mt-5"
          variant="outline"
          disabled={busy}
          onClick={() => void cancelResult()}
        >
          Отменить выполнение
        </Button>
      )}
      {preview && (
        <section className="mt-7" data-testid="sql-result-table">
          <SqlExportButton
            projectId={project.id}
            stateVersion={project.state_version}
            command={{
              schema_version: '1',
              kind: 'RESULT',
              format: 'json',
              run_id: preview.run_id,
            }}
            label="Скачать результат JSON"
            onExpired={rootLifetime.expire}
          />
          <div className="overflow-x-auto rounded-md border border-border-button">
            <table className="w-full text-left text-sm">
              <thead className="bg-bg-accent">
                <tr>
                  {preview.columns.map((column, index) => (
                    <th
                      key={`${column}-${index}`}
                      className="px-3 py-2 font-medium"
                    >
                      {column}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {preview.rows.map((row, rowIndex) => (
                  <tr key={rowIndex} className="border-t border-border-button">
                    {row.map((value, index) => (
                      <td key={index} className="px-3 py-2">
                        {value == null ? '—' : String(value)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="mt-2 text-xs text-text-secondary">
            Показаны первые {preview.rows.length} из {preview.row_count} строк.
            Лимит применён к SQL-запросу.
          </p>
          {preview.rows.length < preview.row_count && (
            <Button
              variant="ghost"
              className="mt-3"
              onClick={() => void moreRows()}
            >
              Показать ещё
            </Button>
          )}
          {canEdit && (
            <>
              <div className="mt-5 flex gap-2">
                <Button
                  disabled={
                    !canChangeResult ||
                    run?.compilation_id !== project.compilation?.id
                  }
                  onClick={() => void finish(run?.id)}
                >
                  {busy && <LoaderCircle className="size-4 animate-spin" />}
                  Завершить
                </Button>
                <AlertDialog>
                  <AlertDialogTrigger asChild>
                    <Button variant="ghost" disabled={!canChangeResult}>
                      Удалить данные этого запуска
                    </Button>
                  </AlertDialogTrigger>
                  <AlertDialogContent>
                    <AlertDialogHeader>
                      <AlertDialogTitle>
                        Удалить полученные данные?
                      </AlertDialogTitle>
                      <AlertDialogDescription>
                        Строки этого запуска будут удалены. Документ не
                        создастся; чтобы снова увидеть данные, потребуется
                        выполнить запрос ещё раз.
                      </AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                      <AlertDialogCancel>Оставить данные</AlertDialogCancel>
                      <AlertDialogAction onClick={() => void cancelResult()}>
                        Удалить данные
                      </AlertDialogAction>
                    </AlertDialogFooter>
                  </AlertDialogContent>
                </AlertDialog>
              </div>
              <p className="mt-2 text-xs text-text-secondary">
                «Завершить» сохранит документ с вопросом, SQL и проверками,
                затем очистит временные строки результата. Документ можно
                открыть позже.
              </p>
            </>
          )}
          {(canEdit || conclusion?.payload.status === 'CONFIRMED') && (
            <section className="mt-5 border-t border-border-button pt-4">
              {canEdit && (
                <>
                  <Button
                    variant="ghost"
                    disabled={!canChangeResult}
                    onClick={() => void generateConclusion()}
                  >
                    Сформировать вывод
                  </Button>
                  <p className="mt-1 text-xs text-text-secondary">
                    По нажатию модели передаются не более 25 строк и 12 полей
                    проверенного результата. Текст включается в документ только
                    после вашего подтверждения.
                  </p>
                </>
              )}
              {canEdit && conclusion?.payload.status === 'DRAFT' && (
                <div className="mt-3 space-y-3">
                  <Textarea
                    aria-label="Предложенный вывод"
                    value={conclusionText}
                    onChange={(event) => setConclusionText(event.target.value)}
                    className="min-h-28"
                  />
                  <p className="text-xs text-text-secondary">
                    Источники:{' '}
                    {conclusion.payload.citations
                      .map(
                        (item) =>
                          `строка ${item.row_index + 1}, ${item.column}`,
                      )
                      .join('; ') || 'строки отсутствуют'}
                    . Числовые утверждения сверяются с указанными ячейками.
                  </p>
                  <Button
                    disabled={!canChangeResult || !conclusionText.trim()}
                    onClick={() => void confirmConclusion()}
                  >
                    Подтвердить вывод
                  </Button>
                </div>
              )}
              {conclusion?.payload.status === 'CONFIRMED' && (
                <p className="mt-3 text-sm">{conclusion.payload.text}</p>
              )}
            </section>
          )}
          {canEdit &&
            project.capabilities.python_agent &&
            (run?.result_bytes ?? 0) <= 10_000_000 && (
              <section className="mt-5 border-t border-border-button pt-4">
                <Button
                  variant="ghost"
                  onClick={() => setPythonOpen((value) => !value)}
                >
                  Преобразовать в Python
                </Button>
                {pythonOpen && (
                  <div className="mt-3 space-y-3">
                    <p className="text-xs text-text-secondary">
                      Код получает только копию проверенного результата.
                      Контейнер не имеет доступа к БД и сети. Функция
                      main(columns, rows) должна вернуть таблицу.
                    </p>
                    <Textarea
                      aria-label="Код Python"
                      value={pythonCode}
                      onChange={(event) => setPythonCode(event.target.value)}
                      className="min-h-36 font-mono text-xs"
                    />
                    <Button
                      disabled={!canChangeResult || !pythonCode.trim()}
                      onClick={() => void runPython()}
                    >
                      Выполнить преобразование
                    </Button>
                  </div>
                )}
              </section>
            )}
          {canConfigure && lookupTables.length > 0 && (
            <section className="mt-5 border-t border-border-button pt-4">
              <Button
                variant="ghost"
                onClick={() => setLookupOpen((value) => !value)}
              >
                Дополнить данные
              </Button>
              {lookupOpen && (
                <div className="mt-3 grid gap-3 sm:grid-cols-2">
                  <label className="text-xs">
                    Ключ исходного результата
                    <select
                      className="mt-1 block w-full rounded-md border border-border-button bg-bg-base p-2"
                      value={lookupSourceColumn}
                      onChange={(event) =>
                        setLookupSourceColumn(event.target.value)
                      }
                    >
                      <option value="">Выберите поле</option>
                      {preview.columns.map((column) => (
                        <option key={column} value={column}>
                          {column}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="text-xs">
                    Таблица справочника
                    <select
                      className="mt-1 block w-full rounded-md border border-border-button bg-bg-base p-2"
                      value={lookupTargetId}
                      onChange={(event) => {
                        setLookupTargetId(event.target.value);
                        setLookupKeyId('');
                        setLookupValueId('');
                      }}
                    >
                      <option value="">Выберите таблицу</option>
                      {lookupTables.map((table) => (
                        <option key={table.id} value={table.id}>
                          {table.name}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="text-xs">
                    Ключ справочника
                    <select
                      className="mt-1 block w-full rounded-md border border-border-button bg-bg-base p-2"
                      value={lookupKeyId}
                      onChange={(event) => setLookupKeyId(event.target.value)}
                    >
                      <option value="">Выберите поле</option>
                      {lookupTable?.columns.map((column) => (
                        <option key={column.id} value={column.id}>
                          {column.name}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="text-xs">
                    Добавить поле
                    <select
                      className="mt-1 block w-full rounded-md border border-border-button bg-bg-base p-2"
                      value={lookupValueId}
                      onChange={(event) => setLookupValueId(event.target.value)}
                    >
                      <option value="">Выберите поле</option>
                      {lookupTable?.columns
                        .filter((column) => column.id !== lookupKeyId)
                        .map((column) => (
                          <option key={column.id} value={column.id}>
                            {column.name}
                          </option>
                        ))}
                    </select>
                  </label>
                  <p className="text-xs text-text-secondary sm:col-span-2">
                    Исходный результат сохранится. Если ключ справочника не
                    уникален, дополнение будет отклонено.
                  </p>
                  <Button
                    className="w-fit sm:col-span-2"
                    disabled={
                      !canChangeResult ||
                      !lookupSourceColumn ||
                      !lookupTargetId ||
                      !lookupKeyId ||
                      !lookupValueId
                    }
                    onClick={() => void runLookup()}
                  >
                    Создать дополненный результат
                  </Button>
                </div>
              )}
            </section>
          )}
          {pythonPreview && (
            <section className="mt-5" data-testid="sql-python-result-table">
              <SqlExportButton
                projectId={project.id}
                stateVersion={project.state_version}
                command={{
                  schema_version: '1',
                  kind: 'RESULT',
                  format: 'json',
                  run_id: pythonPreview.run_id,
                }}
                label="Скачать результат Python JSON"
                onExpired={pythonLifetime.expire}
              />
              <h4 className="text-sm font-semibold">
                Производный результат Python · {pythonPreview.row_count} строк
              </h4>
              <div className="mt-2 overflow-x-auto rounded-md border border-border-button">
                <table className="w-full text-left text-sm">
                  <thead className="bg-bg-accent">
                    <tr>
                      {pythonPreview.columns.map((column, index) => (
                        <th key={`${column}-${index}`} className="px-3 py-2">
                          {column}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {pythonPreview.rows.map((row, rowIndex) => (
                      <tr
                        key={rowIndex}
                        className="border-t border-border-button"
                      >
                        {row.map((value, index) => (
                          <td key={index} className="px-3 py-2">
                            {value == null ? '—' : String(value)}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}
          {lookupPreview && (
            <section className="mt-5" data-testid="sql-lookup-result-table">
              <SqlExportButton
                projectId={project.id}
                stateVersion={project.state_version}
                command={{
                  schema_version: '1',
                  kind: 'RESULT',
                  format: 'json',
                  run_id: lookupPreview.run_id,
                }}
                label="Скачать дополненный результат JSON"
                onExpired={lookupLifetime.expire}
              />
              <h4 className="text-sm font-semibold">
                Дополненный результат · {lookupPreview.row_count} строк
              </h4>
              <div className="mt-2 overflow-x-auto rounded-md border border-border-button">
                <table className="w-full text-left text-sm">
                  <thead className="bg-bg-accent">
                    <tr>
                      {lookupPreview.columns.map((column, index) => (
                        <th key={`${column}-${index}`} className="px-3 py-2">
                          {column}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {lookupPreview.rows.map((row, rowIndex) => (
                      <tr
                        key={rowIndex}
                        className="border-t border-border-button"
                      >
                        {row.map((value, index) => (
                          <td key={index} className="px-3 py-2">
                            {value == null ? '—' : String(value)}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}
        </section>
      )}
      {document && run?.status !== 'READY' && !showRepeat && (
        <Button
          className="mt-5"
          variant="outline"
          onClick={() => setShowRepeat(true)}
        >
          Повторить запрос
        </Button>
      )}
      {(!document || showRepeat) &&
        !connection.runsInProgress &&
        compiled?.sql && (
          <section className="mt-7 space-y-4">
            {binding?.status === 'UNAVAILABLE' &&
              preflight?.blocker?.code !== 'SQL_EXECUTION_FORBIDDEN' && (
                <p className="text-sm text-state-error">
                  Для выбранных данных нет доступного источника PostgreSQL.
                </p>
              )}
            {preflight?.blocker?.code === 'SQL_EXECUTION_FORBIDDEN' ? (
              <Button onClick={() => setShowSql(true)}>
                Открыть сохранённый проверенный SQL
              </Button>
            ) : (
              <Button disabled={!canRun} onClick={() => void execute()}>
                {busy && <LoaderCircle className="size-4 animate-spin" />}
                Выполнить запрос
              </Button>
            )}
          </section>
        )}
      {compiled?.sql && (
        <section className="mt-7 border-t border-border-button pt-4">
          {canEdit && !document && !run && (
            <div className="mb-4 space-y-2">
              <Button
                variant="outline"
                disabled={!canChangeResult}
                onClick={() => void finish()}
              >
                Сохранить документ без выполнения
              </Button>
              <p className="text-xs text-text-secondary">
                Документ сохранит проверенный SQL и отметит выполнение и Python
                как не выполненные.
              </p>
            </div>
          )}
          <div className="flex items-center justify-between gap-3">
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setShowSql((value) => !value)}
            >
              <Code2 className="size-4" />
              {showSql ? 'Скрыть SQL' : 'Открыть SQL'}
            </Button>
            {showSql &&
              project.compilation?.id &&
              project.artifact_ids.query && (
                <div className="flex flex-wrap gap-2">
                  <SqlExportButton
                    projectId={project.id}
                    stateVersion={project.state_version}
                    command={{
                      schema_version: '1',
                      kind: 'QUERY',
                      format: 'json',
                      compilation_id: project.compilation.id,
                      query_artifact_id: project.artifact_ids.query,
                    }}
                    label="Копировать"
                    copySql
                  />
                  <SqlExportButton
                    projectId={project.id}
                    stateVersion={project.state_version}
                    command={{
                      schema_version: '1',
                      kind: 'QUERY',
                      format: 'json',
                      compilation_id: project.compilation.id,
                      query_artifact_id: project.artifact_ids.query,
                    }}
                    label="Скачать SQL и параметры"
                  />
                </div>
              )}
          </div>
          {showSql && (
            <>
              <pre className="mt-3 max-h-[440px] overflow-auto rounded-md bg-bg-accent p-5 text-xs leading-6 text-text-primary">
                <code>{compiled.sql}</code>
              </pre>
              <div className="mt-4 grid gap-4 sm:grid-cols-2">
                <div className="border-s-2 border-state-success ps-3">
                  <p className="text-xs text-text-secondary">
                    {tUi('common.sqlGuard')}
                  </p>
                  <p className="mt-1 text-sm font-medium">
                    {compiled.guard.status === 'PASS'
                      ? 'Read-only · проверка пройдена'
                      : 'Проверка не запускалась'}
                  </p>
                </div>
                <div className="border-s-2 border-border-button ps-3">
                  <p className="text-xs text-text-secondary">Параметры</p>
                  <p className="mt-1 text-sm font-medium">
                    {Object.keys(compiled.parameters).length} значений
                  </p>
                </div>
              </div>
            </>
          )}
        </section>
      )}
      {!connection.runsInProgress && (
        <GuidedSqlForm
          project={project}
          initialSql={compiled?.sql || ''}
          onSaved={onRefresh}
        />
      )}
    </div>
  );
}

export function SqlAgentWorkbench({
  projectId,
  onProjectSelected,
}: {
  projectId?: string | null;
  onProjectSelected?: (id: string) => void;
} = {}) {
  const [projects, setProjects] = useState<SqlAgentProject[]>([]);
  const selectedProjectId = useRef<string | null>(projectId || null);
  const routeProject = useRef(projectId);
  const selectionCallback = useRef(onProjectSelected);
  selectionCallback.current = onProjectSelected;
  const announceSelection = (id: string) => {
    routeProject.current = id;
    selectionCallback.current?.(id);
  };
  const projectLoad = useRef(0);
  const registryLoad = useRef(0);
  const [registryOpen, setRegistryOpen] = useState(false);
  const registryTrigger = useRef<HTMLButtonElement>(null);
  const registrySearch = useRef<HTMLInputElement>(null);
  const registryPanel = useRef<HTMLElement>(null);
  useEffect(() => {
    if (registryOpen) {
      registrySearch.current?.focus();
    } else if (registryPanel.current?.contains(document.activeElement)) {
      registryTrigger.current?.focus();
    }
  }, [registryOpen]);
  const closeRegistry = () => {
    setRegistryOpen(false);
    registryTrigger.current?.focus();
  };
  const [search, setSearch] = useState('');
  const [showArchived, setShowArchived] = useState(false);
  const archivedFilter = useRef(false);
  const [editingTitle, setEditingTitle] = useState(false);
  const [titleDraft, setTitleDraft] = useState('');
  const registryAttempt = useRef<{ signature: string; key: string } | null>(
    null,
  );
  const [project, setProjectState] = useState<SqlAgentProject | null>(null);
  const setProject = useCallback((next: SqlAgentProject | null) => {
    if (next && next.id !== selectedProjectId.current) return;
    setProjectState(next);
  }, []);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const beginCreate = () => {
    ++projectLoad.current;
    selectedProjectId.current = null;
    setLoading(false);
    setCreating(true);
    setRegistryOpen(false);
    setError(null);
  };
  const cancelCreate = () => {
    selectedProjectId.current = project?.id || null;
    setCreating(false);
  };
  const [terms, setTerms] = useState('');
  const [editingQuestion, setEditingQuestion] = useState(false);
  useEffect(() => {
    if (editingQuestion) {
      document
        .getElementById('sql-question-revision')
        ?.scrollIntoView?.({ block: 'nearest', behavior: 'smooth' });
    }
  }, [editingQuestion]);
  const [questionDraft, setQuestionDraft] = useState('');
  const [compiled, setCompiled] = useState<SqlQueryCompileResponse | null>(
    null,
  );
  const [compileError, setCompileError] = useState<string | null>(null);
  const [schemaPreview, setSchemaPreview] = useState<Record<
    string,
    unknown
  > | null>(null);
  const [graphChange, setGraphChange] = useState<SqlGraphChangeRequest | null>(
    null,
  );
  const [graphRevision, setGraphRevision] = useState<{
    id: string;
    compilation_id?: string;
    revision: number;
    sql: string;
  } | null>(null);
  useEffect(() => {
    setTerms('');
    setSchemaPreview(null);
    setGraphRevision(null);
    setGraphChange(null);
    setEditingTitle(false);
    setEditingQuestion(false);
    setRegistryOpen(false);
  }, [project?.id]);
  useEffect(() => {
    registryAttempt.current = null;
  }, [project?.id, project?.state_version]);

  const loadProjects = useCallback(
    async (preferredId?: string, background = false) => {
      if (preferredId && selectedProjectId.current !== preferredId) return;
      const generation = ++projectLoad.current;
      const listGeneration = ++registryLoad.current;
      if (!background) setLoading(true);
      try {
        const items = await listBusinessDocumentSqlAgentProjects(
          archivedFilter.current ? { archived: true } : undefined,
        );
        if (generation !== projectLoad.current) return;
        if (listGeneration === registryLoad.current) setProjects(items);
        const id = preferredId || selectedProjectId.current || items[0]?.id;
        selectedProjectId.current = id || null;
        if (id) {
          const loaded = await fetchBusinessDocumentSqlAgentProject(id);
          if (generation !== projectLoad.current) return;
          setProject(loaded);
          if (!routeProject.current) {
            routeProject.current = loaded.id;
            selectionCallback.current?.(loaded.id);
          }
        } else {
          setProject(null);
        }
        setError(null);
      } catch (nextError) {
        if (generation === projectLoad.current)
          setError(errorMessage(nextError));
      } finally {
        if (generation === projectLoad.current) setLoading(false);
      }
    },
    [setProject],
  );

  useEffect(() => {
    const pendingLoads = { project: projectLoad, registry: registryLoad };
    void loadProjects();
    return () => {
      ++pendingLoads.project.current;
      ++pendingLoads.registry.current;
    };
  }, [loadProjects]);

  useEffect(() => {
    if (projectId === routeProject.current) return;
    routeProject.current = projectId;
    selectedProjectId.current = projectId || null;
    setProject(null);
    setCreating(false);
    void loadProjects(projectId || undefined);
  }, [projectId, loadProjects, setProject]);

  const changeArchiveFilter = async (archived: boolean) => {
    archivedFilter.current = archived;
    setShowArchived(archived);
    const generation = ++registryLoad.current;
    try {
      const items = await listBusinessDocumentSqlAgentProjects(
        archived ? { archived: true } : undefined,
      );
      if (generation === registryLoad.current) setProjects(items);
    } catch (nextError) {
      if (generation === registryLoad.current)
        setError(errorMessage(nextError));
    }
  };

  const runningProjectId =
    project?.operation_state === 'RUNNING' ||
    (project?.latest_run &&
      ['QUEUED', 'RUNNING', 'CANCEL_REQUESTED'].includes(
        project.latest_run.status,
      ))
      ? project.id
      : null;
  useEffect(() => {
    if (!runningProjectId) return;
    let cancelled = false;
    const timer = window.setInterval(async () => {
      try {
        const refreshed =
          await fetchBusinessDocumentSqlAgentProject(runningProjectId);
        if (cancelled || selectedProjectId.current !== runningProjectId) return;
        setProject(refreshed);
        setProjects((current) =>
          current.map((item) => (item.id === refreshed.id ? refreshed : item)),
        );
      } catch (nextError) {
        if (!cancelled && selectedProjectId.current === runningProjectId)
          setError(errorMessage(nextError));
      }
    }, 1200);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [runningProjectId, setProject]);

  const compileProjectId = project?.id;
  const compileStage = project?.stage;
  const compileVersion = project?.state_version;
  const compileArtifactId = project?.artifact_ids.query;
  const canEditProject = Boolean(
    project && !project.archived_at && project.capabilities.edit !== false,
  );
  const savedCompilation = project?.compilation?.result;
  useEffect(() => {
    setCompiled(null);
    setCompileError(null);
    if (
      !compileProjectId ||
      compileStage !== 'COMPLETE' ||
      !compileVersion ||
      !compileArtifactId
    )
      return;
    if (savedCompilation) {
      setCompiled(savedCompilation);
      return;
    }
    if (!canEditProject) return;
    let cancelled = false;
    const compile = async () => {
      try {
        const response = await compileBusinessDocumentSqlProject(
          compileProjectId,
          compileVersion,
          `compile-${compileArtifactId}`,
        );
        if (cancelled || selectedProjectId.current !== compileProjectId) return;
        const result = response.compilation;
        if (result.status !== 'READY' || !result.sql) {
          throw new Error(
            result.blocking_issues[0]?.message || 'SQL не сформирован.',
          );
        }
        setProject(response.project);
        setCompiled(result);
      } catch (nextError) {
        if (!cancelled && selectedProjectId.current === compileProjectId)
          setCompileError(errorMessage(nextError));
      }
    };
    void compile();
    return () => {
      cancelled = true;
    };
  }, [
    compileProjectId,
    compileStage,
    compileVersion,
    compileArtifactId,
    savedCompilation,
    canEditProject,
    setProject,
  ]);

  const selectProject = async (projectId: string) => {
    const generation = ++projectLoad.current;
    selectedProjectId.current = projectId;
    setLoading(false);
    setBusy(true);
    try {
      const loaded = await fetchBusinessDocumentSqlAgentProject(projectId);
      if (generation !== projectLoad.current) return;
      setProject(loaded);
      announceSelection(loaded.id);
      setEditingQuestion(false);
      setCreating(false);
      setError(null);
    } catch (nextError) {
      if (generation === projectLoad.current) {
        selectedProjectId.current = project?.id || null;
        setError(errorMessage(nextError));
      }
    } finally {
      if (generation === projectLoad.current) setBusy(false);
    }
  };

  const manageProject = async (
    changes: { title?: string; archived?: boolean } | 'copy',
  ) => {
    if (!project) return;
    const sourceId = project.id;
    const signature = JSON.stringify([
      sourceId,
      project.state_version,
      changes,
    ]);
    if (registryAttempt.current?.signature !== signature) {
      registryAttempt.current = {
        signature,
        key: commandKey(changes === 'copy' ? 'copy-project' : 'update-project'),
      };
    }
    const key = registryAttempt.current.key;
    const generation = ++projectLoad.current;
    ++registryLoad.current;
    setBusy(true);
    try {
      const updated =
        changes === 'copy'
          ? await copyBusinessDocumentSqlAgentProject(
              sourceId,
              project.state_version,
              key,
            )
          : await updateBusinessDocumentSqlAgentProject(
              sourceId,
              project.state_version,
              key,
              changes,
            );
      if (
        generation !== projectLoad.current ||
        selectedProjectId.current !== sourceId
      )
        return;
      selectedProjectId.current = updated.id;
      setProject(updated);
      announceSelection(updated.id);
      setEditingTitle(false);
      registryAttempt.current = null;
      setError(null);
      setSearch('');
      if (changes === 'copy' && archivedFilter.current) {
        archivedFilter.current = false;
        setShowArchived(false);
      }
      setProjects((current) => {
        const rest = current.filter((item) => item.id !== updated.id);
        return Boolean(updated.archived_at) === archivedFilter.current
          ? [updated, ...rest]
          : rest;
      });
      if (changes === 'copy') void changeArchiveFilter(false);
    } catch (nextError) {
      if (generation === projectLoad.current) setError(errorMessage(nextError));
    } finally {
      if (generation === projectLoad.current) setBusy(false);
    }
  };

  const saveQuestion = async () => {
    if (!project || !canEditProject || !questionDraft.trim()) return;
    setBusy(true);
    try {
      const revised = await reviseBusinessDocumentSqlQuestion(
        project.id,
        project.state_version,
        commandKey('revise-question'),
        questionDraft.trim(),
      );
      setProject(revised);
      setEditingQuestion(false);
      const started = await requestBusinessDocumentSqlAgent(revised.id, {
        schema_version: '1',
        expected_state_version: revised.state_version,
        idempotency_key: commandKey('run-requirements'),
        kind: 'REQUIREMENTS',
        payload: { locale: revised.locale },
      });
      setProject(started);
      setProjects((current) =>
        current.map((item) => (item.id === started.id ? started : item)),
      );
      setError(null);
    } catch (nextError) {
      setError(errorMessage(nextError));
    } finally {
      setBusy(false);
    }
  };

  const createProject = async (title: string, sourceRequest: string) => {
    const generation = ++projectLoad.current;
    selectedProjectId.current = null;
    setBusy(true);
    try {
      const created = await createBusinessDocumentSqlAgentProject({
        schema_version: '1',
        title,
        source_request: sourceRequest,
        locale: 'ru',
      });
      if (generation === projectLoad.current)
        selectedProjectId.current = created.id;
      setProject(created);
      if (generation === projectLoad.current) announceSelection(created.id);
      setProjects((current) => [created, ...current]);
      setCreating(false);
      setError(null);
      const started = await requestBusinessDocumentSqlAgent(created.id, {
        schema_version: '1',
        expected_state_version: created.state_version,
        idempotency_key: commandKey('run-requirements'),
        kind: 'REQUIREMENTS',
        payload: { locale: created.locale },
      });
      setProject(started);
      setProjects((current) =>
        current.map((item) => (item.id === started.id ? started : item)),
      );
    } catch (nextError) {
      setError(errorMessage(nextError));
    } finally {
      setBusy(false);
    }
  };

  const runAgent = async (kind: SqlAgentKind) => {
    if (!project || !canEditProject) return;
    const parsedTerms = schemaSearchTerms(terms);
    setBusy(true);
    try {
      const updated = await requestBusinessDocumentSqlAgent(project.id, {
        schema_version: '1',
        expected_state_version: project.state_version,
        idempotency_key: commandKey(`run-${kind.toLowerCase()}`),
        kind,
        payload:
          kind === 'SCHEMA'
            ? { locale: project.locale, terms: parsedTerms }
            : { locale: project.locale },
      });
      setProject(updated);
      setError(null);
    } catch (nextError) {
      setError(errorMessage(nextError));
    } finally {
      setBusy(false);
    }
  };

  const decide = async (
    decision: 'ACCEPT' | 'REJECT',
    artifactPayload: Record<string, unknown> | null,
    repeatSchema = false,
  ) => {
    if (!project?.pending_proposal || !canEditProject) return;
    setBusy(true);
    try {
      const updated = await decideBusinessDocumentSqlAgentProposal(
        project.id,
        project.pending_proposal.id,
        {
          schema_version: '1',
          expected_state_version: project.state_version,
          idempotency_key: commandKey(`decision-${decision.toLowerCase()}`),
          decision,
          artifact_payload: artifactPayload,
        },
      );
      setProject(updated);
      setProjects((current) =>
        current.map((item) => (item.id === updated.id ? updated : item)),
      );
      setError(null);
      if (decision === 'ACCEPT' && updated.next_agent === 'SCHEMA') {
        setTerms('');
      }
      if (
        (decision === 'ACCEPT' && updated.next_agent === 'QUERY') ||
        (repeatSchema &&
          decision === 'REJECT' &&
          updated.next_agent === 'SCHEMA')
      ) {
        const started = await requestBusinessDocumentSqlAgent(updated.id, {
          schema_version: '1',
          expected_state_version: updated.state_version,
          idempotency_key: commandKey(
            `run-${updated.next_agent.toLowerCase()}`,
          ),
          kind: updated.next_agent,
          payload:
            updated.next_agent === 'SCHEMA'
              ? { locale: updated.locale, terms: schemaSearchTerms(terms) }
              : { locale: updated.locale },
        });
        setProject(started);
        setProjects((current) =>
          current.map((item) => (item.id === started.id ? started : item)),
        );
      }
    } catch (nextError) {
      setError(errorMessage(nextError));
    } finally {
      setBusy(false);
    }
  };

  const preparing = Boolean(
    project && KIND_ORDER[project.stage] < KIND_ORDER.QUERY,
  );
  const editQuestion = () => {
    if (!project || !canEditProject) return;
    setQuestionDraft(project.source_request || '');
    setEditingQuestion(true);
  };
  const currentAgent = project?.next_agent;
  const proposalKind = project?.pending_proposal?.kind;
  const visibleProjects = projects.filter((item) =>
    item.title.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()),
  );
  const canManage =
    project &&
    (project.capabilities.manage ?? project.capabilities.edit) !== false;
  const registryBusy =
    busy ||
    project?.operation_state !== 'IDLE' ||
    project?.runs?.some((run) =>
      ['QUEUED', 'RUNNING', 'CANCEL_REQUESTED'].includes(run.status),
    );

  const projectContext = project && (
    <details
      key="project-context"
      className="sql-project-context text-text-secondary"
      data-preparing={preparing}
      open={!preparing}
    >
      <summary className="text-sm">
        Требования, история и обсуждения · ER-схема
      </summary>
      <div className="text-text-primary">
        {preparing && project.artifact_ids.requirements && (
          <section className="mb-5 space-y-2 text-sm">
            <h3 className="font-semibold">Подтверждённые требования</h3>
            <p className="whitespace-pre-wrap break-words leading-6 text-text-secondary">
              {acceptedRequirementsText(project)}
            </p>
          </section>
        )}
        <SqlSchemaGraph
          key={`graph:${project.id}`}
          project={project}
          sql={
            graphRevision?.sql ||
            compiled?.sql ||
            project.compilation?.result.sql ||
            ''
          }
          viewLabel={
            graphRevision
              ? `Версия запроса ${graphRevision.revision}`
              : undefined
          }
          readOnlyView={Boolean(graphRevision)}
          exportSource={
            graphRevision
              ? graphRevision.compilation_id
                ? {
                    compilation_id: graphRevision.compilation_id,
                    query_artifact_id: graphRevision.id,
                  }
                : undefined
              : project.compilation?.id && project.artifact_ids.query
                ? {
                    compilation_id: project.compilation.id,
                    query_artifact_id: project.artifact_ids.query,
                  }
                : undefined
          }
          schema={
            project.pending_proposal?.kind === 'SCHEMA'
              ? schemaPreview
              : (
                  project.pending_proposal?.payload.agent_result as
                    | {
                        source?: {
                          proposed_schema?: Record<string, unknown>;
                        };
                      }
                    | undefined
                )?.source?.proposed_schema ||
                project.artifacts?.schema ||
                null
          }
          candidateSql={
            !graphRevision
              ? (
                  project.pending_proposal?.payload.agent_result as
                    | { proposal?: { sql?: string } }
                    | undefined
                )?.proposal?.sql
              : undefined
          }
          onRequestChange={setGraphChange}
        />
        {project.artifact_ids.schema && project.stage !== 'COMPLETE' && (
          <PreQueryConnection
            key={project.id}
            project={project}
            onRefresh={() => loadProjects(project.id)}
            busy={busy}
          />
        )}
        <SqlRevisionWorkspacePanel
          project={project}
          onRefresh={() => loadProjects(project.id)}
          changeRequest={graphChange}
          onQueryViewChange={setGraphRevision}
          onSchemaRequested={(suggested) => setTerms(suggested.join('\n'))}
        />
      </div>
    </details>
  );

  return (
    <div
      className="sql-agent-workbench"
      data-testid="sql-agent-workbench"
      onKeyDown={(event) => {
        if (event.key === 'Escape' && registryOpen) {
          event.preventDefault();
          closeRegistry();
        }
      }}
    >
      <Button
        ref={registryTrigger}
        variant="ghost"
        className="sql-project-switcher"
        aria-expanded={registryOpen}
        aria-controls="sql-project-registry"
        onClick={() => setRegistryOpen(!registryOpen)}
      >
        {registryOpen ? 'Закрыть список запросов' : 'Запросы · выбрать проект'}
      </Button>
      <aside
        ref={registryPanel}
        id="sql-project-registry"
        aria-label="SQL-проекты"
        data-open={registryOpen}
        className="sql-project-registry border-e border-border-button bg-bg-base"
        onBlur={(event) => {
          if (
            registryOpen &&
            !event.currentTarget.contains(event.relatedTarget) &&
            event.relatedTarget !== registryTrigger.current
          ) {
            setRegistryOpen(false);
          }
        }}
      >
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border-button px-4 py-4">
          <div>
            <p className="text-sm font-semibold">Запросы</p>
            <p className="text-xs text-text-disabled">
              {visibleProjects.length} {showArchived ? 'в архиве' : 'в списке'}
            </p>
          </div>
          <Button
            className="w-full"
            size="sm"
            variant="outline"
            onClick={beginCreate}
          >
            <Plus className="size-4" />
            Новый запрос
          </Button>
        </div>
        <div className="space-y-3 border-b border-border-button px-4 py-3">
          <Input
            ref={registrySearch}
            aria-label="Поиск проектов"
            placeholder="Найти по названию"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
          <label className="flex items-center gap-2 text-xs text-text-secondary">
            <input
              type="checkbox"
              checked={showArchived}
              onChange={(event) =>
                void changeArchiveFilter(event.target.checked)
              }
            />
            Показать архив
          </label>
        </div>
        <div className="divide-y divide-border-button">
          {visibleProjects.map((item) => (
            <button
              key={item.id}
              type="button"
              className={`w-full px-4 py-3 text-left transition-colors ${
                project?.id === item.id && !creating
                  ? 'bg-bg-accent'
                  : 'hover:bg-bg-accent/60'
              }`}
              onClick={() => {
                if (registryOpen) closeRegistry();
                void selectProject(item.id);
              }}
            >
              <span className="block break-words text-sm font-medium leading-5">
                {item.title}
              </span>
              <span className="mt-1 flex items-center gap-1.5 text-xs text-text-disabled">
                {item.operation_state === 'RUNNING' && (
                  <LoaderCircle className="size-3 animate-spin" />
                )}
                {item.archived_at
                  ? 'В архиве'
                  : item.next_action === 'OPEN_DOCUMENT'
                    ? 'Завершён'
                    : item.next_action === 'VIEW_RESULT'
                      ? 'Есть результат'
                      : item.stage === 'COMPLETE'
                        ? 'Ожидает запуска'
                        : STAGES[KIND_ORDER[item.stage]]?.label || item.stage}
              </span>
            </button>
          ))}
        </div>
      </aside>

      <div className="sql-project-content">
        {error && !project && (
          <p
            role="alert"
            className="m-6 border-s-2 border-state-error ps-3 text-sm text-state-error"
          >
            {error}
          </p>
        )}
        {loading ? (
          <div className="flex h-full items-center justify-center">
            <LoaderCircle className="size-5 animate-spin text-accent-primary" />
          </div>
        ) : creating ? (
          <CreateProject
            busy={busy}
            onCancel={cancelCreate}
            onSubmit={createProject}
          />
        ) : !project ? (
          <EmptyProjects onCreate={beginCreate} />
        ) : (
          <div className="min-h-full">
            <header className="sql-project-header">
              <div className="relative flex items-start justify-between gap-4">
                <div className="min-w-0 flex-1 pe-16">
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-medium text-text-secondary">
                      SQL-проекты / запрос к данным
                    </span>
                  </div>
                  <h2 className="mt-1 break-words text-[22px] font-semibold leading-8 tracking-tight xl:text-2xl">
                    {project.title}
                  </h2>
                  {project.archived_at && (
                    <p
                      className="mt-2 text-sm text-text-secondary"
                      role="status"
                    >
                      Проект в архиве. Для изменения и выполнения восстановите
                      его.
                    </p>
                  )}
                  {editingTitle ? (
                    <div className="mt-3 flex flex-wrap items-center gap-2">
                      <Input
                        aria-label="Название проекта"
                        maxLength={255}
                        value={titleDraft}
                        onChange={(event) => setTitleDraft(event.target.value)}
                      />
                      <Button
                        size="sm"
                        disabled={registryBusy || !titleDraft.trim()}
                        onClick={() =>
                          void manageProject({ title: titleDraft.trim() })
                        }
                      >
                        Сохранить название
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => setEditingTitle(false)}
                      >
                        Отменить переименование
                      </Button>
                    </div>
                  ) : (
                    <details
                      className={
                        preparing ? 'absolute end-0 top-0 z-10' : 'mt-2'
                      }
                      open={!preparing}
                    >
                      <summary
                        className={
                          preparing
                            ? 'cursor-pointer list-none rounded px-3 py-2 text-sm'
                            : 'hidden'
                        }
                      >
                        Ещё
                      </summary>
                      <div
                        className={
                          preparing
                            ? 'absolute end-0 flex w-52 flex-col items-stretch gap-1 rounded-md border border-border-button bg-bg-base p-2 shadow-lg'
                            : 'flex flex-wrap items-center gap-2'
                        }
                      >
                        {canManage && !project.archived_at && (
                          <Button
                            size="sm"
                            variant="ghost"
                            disabled={registryBusy}
                            onClick={() => {
                              setTitleDraft(project.title);
                              setEditingTitle(true);
                            }}
                          >
                            Переименовать
                          </Button>
                        )}
                        {(project.capabilities.copy ??
                          project.capabilities.execute) !== false && (
                          <Button
                            size="sm"
                            variant="ghost"
                            disabled={busy}
                            onClick={() => void manageProject('copy')}
                          >
                            Создать копию
                          </Button>
                        )}
                        {canManage && (
                          <Button
                            size="sm"
                            variant="ghost"
                            disabled={registryBusy}
                            onClick={() =>
                              void manageProject({
                                archived: !project.archived_at,
                              })
                            }
                          >
                            {project.archived_at
                              ? 'Восстановить из архива'
                              : 'В архив'}
                          </Button>
                        )}
                        <Button
                          size="sm"
                          variant="ghost"
                          disabled={busy}
                          onClick={() => void loadProjects(project.id)}
                        >
                          <RefreshCw className="size-4" />
                          Обновить
                        </Button>
                      </div>
                    </details>
                  )}
                  {!preparing &&
                    project.operation_state !== 'RUNNING' &&
                    project.capabilities.edit !== false && (
                      <Button
                        size="sm"
                        variant="ghost"
                        disabled={busy}
                        onClick={() => {
                          setQuestionDraft(project.source_request || '');
                          setEditingQuestion(true);
                        }}
                      >
                        Изменить задачу
                      </Button>
                    )}
                </div>
              </div>
              <ol
                className="sql-preparation-steps text-sm text-text-secondary"
                aria-label="Этапы подготовки"
              >
                {STAGES.map((step, index) => (
                  <li
                    key={step.kind}
                    aria-current={
                      project.stage === step.kind ? 'step' : undefined
                    }
                    className={
                      project.stage === step.kind
                        ? 'bg-accent-primary/15 font-semibold text-accent-primary'
                        : ''
                    }
                  >
                    {index + 1} {step.label}
                    {KIND_ORDER[project.stage] > index ? ' · готовы' : ''}
                  </li>
                ))}
              </ol>
              <details className="sql-question-summary bg-bg-card text-sm">
                <summary>
                  <span>{project.source_request}</span>
                  <span>Вопрос целиком</span>
                </summary>
                <p className="mt-3 whitespace-pre-wrap break-words leading-6 text-text-secondary">
                  {project.source_request}
                </p>
                {preparing &&
                  canEditProject &&
                  proposalKind !== 'SCHEMA' &&
                  project.operation_state !== 'RUNNING' && (
                    <Button
                      className="mt-3"
                      variant="outline"
                      disabled={busy}
                      onClick={editQuestion}
                    >
                      Изменить задачу
                    </Button>
                  )}
              </details>
            </header>

            <main className="sql-project-main" aria-live="polite">
              {!preparing && projectContext}
              {editingQuestion && (
                <section className="mb-6 max-w-2xl space-y-3 border-y border-border-button py-4">
                  <label
                    className="block text-sm font-medium"
                    htmlFor="sql-question-revision"
                  >
                    Уточнённая задача
                  </label>
                  <Textarea
                    id="sql-question-revision"
                    value={questionDraft}
                    onChange={(event) => setQuestionDraft(event.target.value)}
                  />
                  <p className="text-xs text-text-secondary">
                    Анализ начнётся заново; прежние решения останутся в истории
                    проекта.
                  </p>
                  <div className="flex gap-2">
                    <Button
                      disabled={busy || !questionDraft.trim()}
                      onClick={() => void saveQuestion()}
                    >
                      Сохранить и проанализировать
                    </Button>
                    <Button
                      variant="ghost"
                      onClick={() => setEditingQuestion(false)}
                    >
                      Отмена
                    </Button>
                  </div>
                </section>
              )}
              {error && (
                <div className="mb-6 flex items-start gap-2 border-s-2 border-state-error ps-3 text-sm text-state-error">
                  <AlertCircle className="mt-0.5 size-4 shrink-0" />
                  <span>{error}</span>
                </div>
              )}
              {(project.blockers?.length
                ? project.blockers
                : project.last_error?.code || project.last_error?.message
                  ? [
                      {
                        ...project.last_error,
                        code: project.last_error.code || 'SQL_FAILED',
                        message:
                          project.last_error.message || 'Операция не выполнена',
                      },
                    ]
                  : []
              ).map((blocker, index) => (
                <div
                  key={`${blocker.code}-${index}`}
                  className="mb-6 border-s-2 border-state-error ps-3 text-sm text-state-error"
                >
                  <p>{blocker.message}</p>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() =>
                      document
                        .getElementById(
                          blocker.action === 'CHECK_QUERY'
                            ? 'sql-check-query'
                            : 'sql-next-action',
                        )
                        ?.scrollIntoView({ behavior: 'smooth' })
                    }
                  >
                    Перейти к исправлению
                  </Button>
                </div>
              ))}

              {project.operation_state === 'RUNNING' && project.current_job && (
                <section className="mx-auto max-w-2xl py-12 text-center">
                  <LoaderCircle className="mx-auto size-7 animate-spin text-accent-primary" />
                  <h3 className="mt-4 text-lg font-semibold">
                    Работает агент «
                    {STAGES.find(
                      (item) => item.kind === project.current_job?.kind,
                    )?.label || project.current_job.kind}
                    »
                  </h3>
                  <p className="mt-1 text-sm text-text-secondary">
                    {project.current_job.progress_message ||
                      'Анализируем подтверждённый контекст проекта…'}
                  </p>
                  <Progress
                    className="mx-auto mt-5 h-1.5 max-w-sm"
                    value={Math.max(project.current_job.progress, 8)}
                  />
                  <p className="mt-3 text-xs text-text-disabled">
                    Попытка {project.current_job.attempt + 1} из{' '}
                    {project.current_job.max_attempts}
                  </p>
                </section>
              )}

              {project.operation_state === 'REVIEW' &&
                proposalKind !== 'REVISION' && (
                  <fieldset
                    className="min-w-0"
                    aria-label="Подтверждение предложения агента"
                    disabled={!canEditProject && proposalKind !== 'SCHEMA'}
                  >
                    {proposalKind === 'REQUIREMENTS' && (
                      <RequirementsReview
                        project={project}
                        busy={busy}
                        onDecision={decide}
                      />
                    )}
                    {proposalKind === 'SCHEMA' && (
                      <SqlSchemaReview
                        key={`${project.id}:${project.pending_proposal?.id}`}
                        readOnly={!canEditProject}
                        onEditQuestion={
                          canEditProject ? editQuestion : undefined
                        }
                        onPreview={setSchemaPreview}
                        project={project}
                        busy={busy}
                        onDecision={(decision, payload) =>
                          decide(decision, payload, decision === 'REJECT')
                        }
                      />
                    )}
                    {proposalKind === 'QUERY' && (
                      <QueryReview
                        project={project}
                        busy={busy}
                        onDecision={decide}
                      />
                    )}
                    {project.stage === 'QUERY' && proposalKind === 'QUERY' && (
                      <div className="mx-auto max-w-2xl pb-8">
                        <GuidedSqlForm
                          project={project}
                          onSaved={() => loadProjects(project.id)}
                        />
                      </div>
                    )}
                  </fieldset>
                )}

              {project.operation_state === 'IDLE' &&
                project.capabilities.edit !== false &&
                currentAgent && (
                  <section
                    id="sql-next-action"
                    className="mx-auto max-w-2xl py-7"
                  >
                    <div className="flex size-10 items-center justify-center rounded-full bg-accent-primary/10 text-accent-primary">
                      {currentAgent === 'REQUIREMENTS' ? (
                        <FileText className="size-5" />
                      ) : currentAgent === 'SCHEMA' ? (
                        <Database className="size-5" />
                      ) : (
                        <Code2 className="size-5" />
                      )}
                    </div>
                    <h3 className="mt-4 text-xl font-semibold">
                      {currentAgent === 'REQUIREMENTS'
                        ? 'Разобрать исходные требования'
                        : currentAgent === 'SCHEMA'
                          ? 'Найти подходящие данные'
                          : 'Собрать план SQL-запроса'}
                    </h3>
                    <p className="mt-2 text-sm leading-6 text-text-secondary">
                      {currentAgent === 'REQUIREMENTS'
                        ? 'Агент выделит требования к выводу, соединениям, фильтрации, сортировке и лимитам.'
                        : currentAgent === 'SCHEMA'
                          ? 'Агент выделит нужные данные из подтверждённых требований и найдёт источники в доступном каталоге. Имена таблиц вводить не нужно.'
                          : 'Агент использует только подтверждённые требования и снимок схемы.'}
                    </p>
                    {currentAgent === 'SCHEMA' && (
                      <details className="mt-5 text-sm">
                        <summary className="cursor-pointer">
                          Уточнить поиск (необязательно)
                        </summary>
                        <label className="mt-3 block font-medium">
                          Темы для уточнения поиска
                          <Textarea
                            className="mt-2 min-h-36"
                            value={terms}
                            placeholder={'запуски импорта глоссария'}
                            onChange={(event) => setTerms(event.target.value)}
                          />
                          <span className="mt-2 block text-xs text-text-disabled">
                            До 8 тем для поиска за один шаг. Имя таблицы не
                            требуется.
                          </span>
                        </label>
                      </details>
                    )}
                    {currentAgent === 'QUERY' && (
                      <div className="mt-5 border-y border-border-button py-4 text-sm">
                        <p className="font-medium">Контекст зафиксирован</p>
                        <p className="mt-1 text-xs text-text-secondary">
                          Требования и схема имеют неизменяемые версии. Агент не
                          сможет использовать таблицы вне снимка.
                        </p>
                      </div>
                    )}
                    <Button
                      className="mt-6"
                      disabled={busy}
                      data-testid={`sql-agent-run-${currentAgent.toLowerCase()}`}
                      onClick={() => void runAgent(currentAgent)}
                    >
                      {busy ? (
                        <LoaderCircle className="size-4 animate-spin" />
                      ) : (
                        <Sparkles className="size-4" />
                      )}
                      {currentAgent === 'SCHEMA'
                        ? 'Найти данные'
                        : 'Запустить агента'}
                    </Button>
                    {currentAgent === 'QUERY' && (
                      <GuidedSqlForm
                        project={project}
                        onSaved={() => loadProjects(project.id)}
                      />
                    )}
                  </section>
                )}

              {project.stage === 'COMPLETE' &&
                project.operation_state === 'IDLE' && (
                  <CompletedProject
                    key={project.id}
                    project={project}
                    compiled={compiled}
                    compileError={compileError}
                    onRefresh={() => loadProjects(project.id)}
                    onExpiryRefresh={() => loadProjects(project.id, true)}
                  />
                )}
              {preparing && projectContext}
            </main>
          </div>
        )}
      </div>
    </div>
  );
}
