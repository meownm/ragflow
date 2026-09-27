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
  createBusinessDocumentSqlAgentProject,
  decideBusinessDocumentSqlAgentProposal,
  fetchBusinessDocumentSqlAgentProject,
  listBusinessDocumentSqlAgentProjects,
  loadBusinessDocumentSqlSchemaEntities,
  preflightBusinessDocumentSqlProject,
  previewBusinessDocumentSqlRun,
  proposeBusinessDocumentSqlConclusion,
  requestBusinessDocumentSqlAgent,
  reviseBusinessDocumentSqlQuestion,
  runBusinessDocumentSqlLookup,
  runBusinessDocumentSqlProject,
  runBusinessDocumentSqlPython,
  saveBusinessDocumentSqlManualQuery,
} from '@/services/business-document-service';
import {
  AlertCircle,
  ArrowRight,
  Bot,
  Check,
  CheckCircle2,
  ChevronRight,
  Clipboard,
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
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  applySchemaCandidateDetails,
  buildSchemaSnapshot,
  chooseSchemaCandidate,
  toggleSchemaColumn,
  type SchemaWorkspaceState,
} from './schema-workspace';
import {
  queryProposalFromPending,
  requirementsProposal,
  schemaArtifactFromWorkspace,
  schemaWorkspaceFromProposal,
} from './sql-agent-document';

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
    label: 'Проверка запроса',
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

function schemaTermsFromRequirements(project: SqlAgentProject): string[] {
  const requirements = project.artifacts?.requirements?.requirements;
  const statements = Array.isArray(requirements)
    ? requirements
        .filter((item) => item && typeof item === 'object')
        .map((item) =>
          String((item as { statement?: unknown }).statement || ''),
        )
    : [];
  const stop = new Set([
    'вывести',
    'показать',
    'получить',
    'найти',
    'посчитать',
    'сумму',
    'количество',
    'данные',
    'таблицы',
    'полей',
    'строки',
    'месяц',
    'период',
    'последний',
    'последние',
    'только',
    'которые',
    'сортировать',
    'ограничить',
    'строк',
    'для',
    'или',
    'the',
    'from',
    'with',
    'show',
    'count',
    'total',
  ]);
  const words =
    (statements.join(' ') || project.source_request || '')
      .toLocaleLowerCase()
      .match(/[\p{L}\p{N}_]{4,}/gu) || [];
  return [...new Set(words.filter((word) => !stop.has(word)))].slice(0, 8);
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

function SchemaReview({
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
  const [workspace, setWorkspace] = useState<SchemaWorkspaceState>(() =>
    schemaWorkspaceFromProposal(project),
  );
  const [loadingEntity, setLoadingEntity] = useState<string | null>(null);
  const [localError, setLocalError] = useState<string | null>(null);
  useEffect(() => {
    setWorkspace(schemaWorkspaceFromProposal(project));
    setLocalError(null);
  }, [project]);
  const snapshot = useMemo(
    () => buildSchemaSnapshot(workspace.resolutions, workspace.requirements),
    [workspace],
  );

  const selectCandidate = async (resolutionIndex: number, entityId: string) => {
    setLocalError(null);
    setWorkspace((current) => ({
      ...current,
      resolutions: current.resolutions.map((resolution, index) =>
        index === resolutionIndex
          ? chooseSchemaCandidate(resolution, entityId)
          : resolution,
      ),
    }));
    setLoadingEntity(entityId);
    try {
      const response = await loadBusinessDocumentSqlSchemaEntities({
        entity_ids: [entityId],
        locale: project.locale,
      });
      const details = response.entities.find(
        (item) => item.entity_id === entityId,
      );
      if (!details?.entity || details.lookup.status === 'ERROR') {
        throw new Error(
          details?.lookup.message || 'Схема выбранной таблицы недоступна.',
        );
      }
      setWorkspace((current) => ({
        ...current,
        resolutions: current.resolutions.map((resolution, index) => {
          if (index !== resolutionIndex) return resolution;
          const applied = applySchemaCandidateDetails(
            chooseSchemaCandidate(resolution, entityId),
            details.entity!,
            {
              freshness: details.freshness,
              retrieval: details.retrieval,
              sources: details.sources,
              warnings: details.warnings,
            },
          );
          const recommended = new Set(
            applied.interpretation?.recommendedColumnIds ?? [],
          );
          return {
            ...applied,
            selectedColumnIds:
              applied.candidates
                .find((candidate) => candidate.id === entityId)
                ?.columns.filter((column) => recommended.has(column.id))
                .map((column) => column.id) ?? [],
          };
        }),
      }));
    } catch (error) {
      setLocalError(errorMessage(error));
    } finally {
      setLoadingEntity(null);
    }
  };

  const toggleColumn = (resolutionIndex: number, columnId: string) => {
    setWorkspace((current) => ({
      ...current,
      resolutions: current.resolutions.map((resolution, index) =>
        index === resolutionIndex
          ? toggleSchemaColumn(resolution, columnId)
          : resolution,
      ),
    }));
  };

  return (
    <div className="space-y-7" data-testid="sql-agent-schema-review">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-xs font-medium uppercase tracking-[0.14em] text-accent-primary">
            Предложение агента схемы
          </p>
          <h3 className="mt-1 text-xl font-semibold">
            Проверьте найденные данные
          </h3>
          <p className="mt-1 text-sm text-text-secondary">
            По описанию вопроса каталог предложил источники. Выберите подходящий
            по смыслу, затем отметьте поля для расчёта и условий.
          </p>
        </div>
        <Badge
          variant="outline"
          className={
            snapshot.status === 'READY'
              ? 'border-state-success text-state-success'
              : ''
          }
        >
          {snapshot.status === 'READY' ? 'Снимок готов' : 'Нужно уточнение'}
        </Badge>
      </div>

      {localError && (
        <p className="border-s-2 border-state-error ps-3 text-sm text-state-error">
          {localError}
        </p>
      )}

      <div className="divide-y divide-border-button border-y border-border-button">
        {workspace.resolutions.map((resolution, resolutionIndex) => {
          const selected = resolution.candidates.find(
            (candidate) => candidate.id === resolution.selectedEntityId,
          );
          return (
            <section
              key={`${resolution.term}-${resolutionIndex}`}
              className="py-5"
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <p className="text-xs text-text-disabled">Что ищем</p>
                  <h4 className="text-base font-semibold">{resolution.term}</h4>
                </div>
                {resolution.interpretation?.reason && (
                  <p className="max-w-xl text-xs leading-5 text-text-secondary">
                    {resolution.interpretation.reason}
                  </p>
                )}
              </div>
              <div className="mt-3 grid gap-2 lg:grid-cols-2">
                {resolution.candidates.map((candidate) => (
                  <button
                    key={candidate.id}
                    type="button"
                    className={`flex min-w-0 items-center justify-between gap-3 rounded-md border px-3 py-3 text-left transition-colors ${
                      resolution.selectedEntityId === candidate.id
                        ? 'border-accent-primary bg-accent-primary/5'
                        : 'border-border-button hover:border-border-default'
                    }`}
                    onClick={() =>
                      void selectCandidate(resolutionIndex, candidate.id)
                    }
                  >
                    <span className="min-w-0">
                      <span className="block truncate text-sm font-medium">
                        {candidate.displayName || candidate.name}
                      </span>
                      <span className="block text-xs text-text-disabled">
                        {candidate.description ||
                          'Описание источника не указано'}
                      </span>
                    </span>
                    {loadingEntity === candidate.id ? (
                      <LoaderCircle className="size-4 shrink-0 animate-spin" />
                    ) : resolution.selectedEntityId === candidate.id ? (
                      <CheckCircle2 className="size-4 shrink-0 text-accent-primary" />
                    ) : (
                      <ChevronRight className="size-4 shrink-0 text-text-disabled" />
                    )}
                  </button>
                ))}
              </div>
              {selected?.schemaStatus === 'summary' && (
                <Button
                  className="mt-3"
                  size="sm"
                  variant="outline"
                  disabled={loadingEntity !== null}
                  onClick={() =>
                    void selectCandidate(resolutionIndex, selected.id)
                  }
                >
                  <Database className="size-4" />
                  Загрузить поля
                </Button>
              )}
              {selected?.schemaStatus === 'loaded' && (
                <div className="mt-4 border-s-2 border-border-button ps-4">
                  <p className="text-xs font-medium">
                    Поля для ответа и расчёта · выбрано{' '}
                    {resolution.selectedColumnIds.length}
                  </p>
                  <p className="mt-1 text-xs text-text-secondary">
                    Отметьте поля, по которым нужно группировать, считать или
                    отбирать строки. Это ещё не столбцы готового ответа: их
                    можно проверить на следующем шаге.
                  </p>
                  <div className="mt-3 grid max-h-56 gap-x-5 gap-y-2 overflow-y-auto pe-2 sm:grid-cols-2">
                    {selected.columns.map((column) => (
                      <label
                        key={column.id}
                        className="flex cursor-pointer items-start gap-2 text-xs"
                      >
                        <Checkbox
                          className="mt-0.5"
                          checked={resolution.selectedColumnIds.includes(
                            column.id,
                          )}
                          onCheckedChange={() =>
                            toggleColumn(resolutionIndex, column.id)
                          }
                        />
                        <span className="min-w-0">
                          <span className="block truncate font-medium">
                            {column.displayName || column.name}
                          </span>
                          <span className="block truncate text-text-disabled">
                            {column.description ||
                              column.dataType ||
                              'Описание не указано'}
                          </span>
                        </span>
                      </label>
                    ))}
                  </div>
                </div>
              )}
            </section>
          );
        })}
      </div>

      <DecisionBar
        busy={busy || loadingEntity !== null}
        acceptDisabled={snapshot.status !== 'READY'}
        acceptLabel="Подтвердить схему"
        onAccept={() =>
          onDecision(
            'ACCEPT',
            schemaArtifactFromWorkspace(workspace) as Record<string, unknown>,
          )
        }
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

function ManualSqlForm({
  project,
  initialSql,
  onSaved,
}: {
  project: SqlAgentProject;
  initialSql?: string;
  onSaved: () => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [sql, setSql] = useState(initialSql || '');
  const [parameters, setParameters] = useState(
    '[{"name":"row_limit","type":"integer","value":100}]',
  );
  const [aligned, setAligned] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      const parsed = JSON.parse(parameters) as Array<{
        name: string;
        type: string;
        value: unknown;
      }>;
      if (!Array.isArray(parsed))
        throw new Error('Параметры должны быть массивом JSON.');
      await saveBusinessDocumentSqlManualQuery(
        project.id,
        project.state_version,
        commandKey('manual-sql'),
        sql,
        parsed,
      );
      setOpen(false);
      await onSaved();
    } catch (nextError) {
      setError(errorMessage(nextError));
    } finally {
      setBusy(false);
    }
  };
  return (
    <section
      id="sql-check-query"
      className="mt-6 border-t border-border-button pt-4"
    >
      <Button variant="ghost" onClick={() => setOpen((value) => !value)}>
        <Code2 className="size-4" />
        {open ? 'Скрыть ручной SQL' : 'Ручной SQL'}
      </Button>
      {open && (
        <div className="mt-4 max-w-3xl space-y-3">
          <p className="text-xs text-text-secondary">
            Экспертный режим принимает один PostgreSQL SELECT с явными алиасами
            полей и LIMIT :row_limit. CTE, подзапросы, HAVING, окна и UNION
            проверяются по принятому каталогу.
          </p>
          <Textarea
            aria-label="Ручной SQL"
            value={sql}
            onChange={(event) => setSql(event.target.value)}
            className="min-h-48 font-mono text-xs"
          />
          <label className="block text-xs">
            Параметры JSON
            <Textarea
              aria-label="Параметры ручного SQL"
              value={parameters}
              onChange={(event) => setParameters(event.target.value)}
              className="mt-1 min-h-20 font-mono text-xs"
            />
          </label>
          <label className="flex items-center gap-2 text-xs">
            <Checkbox
              checked={aligned}
              onCheckedChange={(value) => setAligned(value === true)}
            />
            Этот SQL соответствует принятой задаче и выбранным данным
          </label>
          {error && <p className="text-xs text-state-error">{error}</p>}
          <Button
            disabled={busy || !aligned || !sql.trim()}
            onClick={() => void save()}
          >
            {busy && <LoaderCircle className="size-4 animate-spin" />}Проверить
            и сохранить SQL
          </Button>
        </div>
      )}
    </section>
  );
}

function CompletedProject({
  project,
  compiled,
  compileError,
  onRefresh,
}: {
  project: SqlAgentProject;
  compiled: SqlQueryCompileResponse | null;
  compileError: string | null;
  onRefresh: () => Promise<void>;
}) {
  const [copied, setCopied] = useState(false);
  const [showSql, setShowSql] = useState(false);
  const [showRepeat, setShowRepeat] = useState(false);
  const [preflight, setPreflight] = useState<SqlProjectPreflight | null>(null);
  const [selectedProfileId, setSelectedProfileId] = useState<string | null>(
    null,
  );
  const [preview, setPreview] = useState<SqlProjectPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [pythonOpen, setPythonOpen] = useState(false);
  const [pythonCode, setPythonCode] = useState(
    'def main(columns, rows):\n    return {"columns": columns, "rows": rows}\n',
  );
  const [pythonPreview, setPythonPreview] = useState<SqlProjectPreview | null>(
    null,
  );
  const [lookupOpen, setLookupOpen] = useState(false);
  const [lookupSourceColumn, setLookupSourceColumn] = useState('');
  const [lookupTargetId, setLookupTargetId] = useState('');
  const [lookupKeyId, setLookupKeyId] = useState('');
  const [lookupValueId, setLookupValueId] = useState('');
  const [lookupPreview, setLookupPreview] = useState<SqlProjectPreview | null>(
    null,
  );
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
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
  useEffect(() => {
    setConclusionText(conclusion?.payload.text || '');
  }, [conclusion?.id, conclusion?.payload.text]);
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
    setPreflight(null);
    void preflightBusinessDocumentSqlProject(project.id, selectedProfileId)
      .then((result) => {
        if (active) setPreflight(result);
      })
      .catch((error) => {
        if (active) setActionError(errorMessage(error));
      });
    return () => {
      active = false;
    };
  }, [
    project.id,
    project.state_version,
    compiled?.sql,
    selectedProfileId,
    document?.id,
  ]);
  useEffect(() => {
    if (!runId || runStatus !== 'READY') {
      setPreview(null);
      return;
    }
    let active = true;
    void previewBusinessDocumentSqlRun(project.id, runId)
      .then((result) => {
        if (active) setPreview(result);
      })
      .catch((error) => {
        if (active) setActionError(errorMessage(error));
      });
    return () => {
      active = false;
    };
  }, [project.id, runId, runStatus]);
  useEffect(() => {
    const derived = project.derived_runs?.find(
      (item) => item.kind === 'PYTHON' && item.status === 'READY',
    );
    if (!derived || derived.source_run_id !== run?.id) {
      setPythonPreview(null);
      return;
    }
    let active = true;
    void previewBusinessDocumentSqlRun(project.id, derived.id)
      .then((result) => {
        if (active) setPythonPreview(result);
      })
      .catch((error) => {
        if (active) setActionError(errorMessage(error));
      });
    return () => {
      active = false;
    };
  }, [project.id, project.derived_runs, run?.id]);
  useEffect(() => {
    const derived = project.derived_runs?.find(
      (item) => item.kind === 'LOOKUP' && item.status === 'READY',
    );
    if (!derived || derived.source_run_id !== run?.id) {
      setLookupPreview(null);
      return;
    }
    let active = true;
    void previewBusinessDocumentSqlRun(project.id, derived.id)
      .then((result) => {
        if (active) setLookupPreview(result);
      })
      .catch((error) => {
        if (active) setActionError(errorMessage(error));
      });
    return () => {
      active = false;
    };
  }, [project.id, project.derived_runs, run?.id]);
  const copySql = async () => {
    if (!compiled?.sql) return;
    await navigator.clipboard.writeText(compiled.sql);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1500);
  };
  const execute = async () => {
    if (!compiled?.sql) return;
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
      setActionError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  };
  const finish = async () => {
    if (!run || run.status !== 'READY') return;
    setBusy(true);
    setActionError(null);
    try {
      await completeBusinessDocumentSqlProject(
        project.id,
        project.state_version,
        commandKey('sql-complete'),
        run.id,
      );
      setPreview(null);
      await onRefresh();
    } catch (error) {
      setActionError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  };
  const cancelResult = async () => {
    if (!run || !['READY', 'QUEUED', 'RUNNING'].includes(run.status)) return;
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
      setActionError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  };
  const moreRows = async () => {
    if (!run || !preview) return;
    try {
      const next = await previewBusinessDocumentSqlRun(
        project.id,
        run.id,
        preview.rows.length,
      );
      setPreview({ ...next, rows: [...preview.rows, ...next.rows], offset: 0 });
    } catch (error) {
      setActionError(errorMessage(error));
    }
  };
  const runPython = async () => {
    if (!run || run.status !== 'READY') return;
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
      setPythonPreview(
        await previewBusinessDocumentSqlRun(project.id, result.run_id),
      );
      await onRefresh();
    } catch (error) {
      setActionError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  };
  const runLookup = async () => {
    if (!run || run.status !== 'READY') return;
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
      setLookupPreview(
        await previewBusinessDocumentSqlRun(project.id, result.run_id),
      );
      await onRefresh();
    } catch (error) {
      setActionError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  };
  const generateConclusion = async () => {
    if (!run || run.status !== 'READY') return;
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
      setActionError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  };
  const confirmConclusion = async () => {
    if (
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
      setActionError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  };
  const binding = preflight?.binding;
  const canRun = Boolean(
    compiled?.sql &&
    binding?.status === 'BOUND' &&
    !preflight?.blocker &&
    !busy,
  );
  return (
    <div id="sql-next-action" data-testid="sql-agent-complete">
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
                {item.status === 'READY'
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
        {run?.status === 'READY'
          ? 'Результат'
          : run &&
              ['QUEUED', 'RUNNING', 'CANCEL_REQUESTED'].includes(run.status)
            ? 'Выполнение'
            : document
              ? 'Завершённый проект'
              : 'Проверка и запуск'}
      </p>
      <h3 className="mt-1 text-2xl font-semibold">
        {run?.status === 'READY'
          ? 'Данные получены'
          : run &&
              ['QUEUED', 'RUNNING', 'CANCEL_REQUESTED'].includes(run.status)
            ? 'Запрос выполняется'
            : document
              ? 'Документ сохранён, строки удалены'
              : 'Проверьте запрос перед запуском'}
      </h3>
      <p className="mt-2 text-sm text-text-secondary">
        {run?.status === 'READY'
          ? `${run.row_count} строк · ${run.duration_ms} мс · ${run.checks?.completeness === 'LIMITED' ? 'Показан ограниченный набор' : 'Полный набор в пределах запроса'} · проверка результата пройдена. Строки доступны до завершения проекта.`
          : run?.status === 'FAILED'
            ? `Данные не получены: ${run.error?.message || 'проверка или выполнение завершились ошибкой'}.`
            : run &&
                ['QUEUED', 'RUNNING', 'CANCEL_REQUESTED'].includes(run.status)
              ? 'Ожидаем результат. Можно отменить выполнение.'
              : document
                ? `Ревизия ${document.revision}. Повторный запрос создаст новый запуск и документ.`
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
          <p className="mt-2 text-xs text-text-secondary">
            Получено {String(document.payload?.row_count ?? '—')} строк ·
            результат{' '}
            {(document.payload?.checks as { completeness?: string } | undefined)
              ?.completeness === 'LIMITED'
              ? 'ограничен'
              : 'полный в пределах запроса'}{' '}
            · строки удалены.
          </p>
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
        </section>
      )}
      {run && ['QUEUED', 'RUNNING'].includes(run.status) && (
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
          <div className="mt-5 flex gap-2">
            <Button disabled={busy} onClick={() => void finish()}>
              {busy && <LoaderCircle className="size-4 animate-spin" />}
              Завершить
            </Button>
            <AlertDialog>
              <AlertDialogTrigger asChild>
                <Button variant="ghost" disabled={busy}>
                  Удалить данные этого запуска
                </Button>
              </AlertDialogTrigger>
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle>
                    Удалить полученные данные?
                  </AlertDialogTitle>
                  <AlertDialogDescription>
                    Строки этого запуска будут удалены. Документ не создастся;
                    чтобы снова увидеть данные, потребуется выполнить запрос ещё
                    раз.
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
            «Завершить» сохранит документ с вопросом, SQL и проверками, затем
            очистит временные строки результата. Документ можно открыть позже.
          </p>
          <section className="mt-5 border-t border-border-button pt-4">
            <Button
              variant="ghost"
              disabled={busy}
              onClick={() => void generateConclusion()}
            >
              Сформировать вывод
            </Button>
            <p className="mt-1 text-xs text-text-secondary">
              По нажатию модели передаются не более 25 строк и 12 полей
              проверенного результата. Текст включается в документ только после
              вашего подтверждения.
            </p>
            {conclusion?.payload.status === 'DRAFT' && (
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
                      (item) => `строка ${item.row_index + 1}, ${item.column}`,
                    )
                    .join('; ') || 'строки отсутствуют'}
                  . Числовые утверждения сверяются с указанными ячейками.
                </p>
                <Button
                  disabled={busy || !conclusionText.trim()}
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
          {project.capabilities.python_agent &&
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
                      disabled={busy || !pythonCode.trim()}
                      onClick={() => void runPython()}
                    >
                      Выполнить преобразование
                    </Button>
                  </div>
                )}
              </section>
            )}
          {lookupTables.length > 0 && (
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
                      busy ||
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
        !['READY', 'QUEUED', 'RUNNING', 'CANCEL_REQUESTED'].includes(
          run?.status || '',
        ) &&
        compiled?.sql && (
          <section className="mt-7 space-y-4">
            <p className="text-sm">
              Профиль PostgreSQL:{' '}
              {binding?.selection?.profile.name || 'не выбран'}
            </p>
            {binding?.status === 'NEEDS_SELECTION' && (
              <label className="block text-sm">
                Источник данных
                <select
                  className="mt-2 block w-full rounded-md border border-border-button bg-bg-base p-2"
                  value={selectedProfileId || ''}
                  onChange={(event) =>
                    setSelectedProfileId(event.target.value || null)
                  }
                >
                  <option value="">Выберите источник</option>
                  {binding.candidates.map((profile) => (
                    <option key={profile.id} value={profile.id}>
                      {profile.name}
                    </option>
                  ))}
                </select>
              </label>
            )}
            {binding?.status === 'UNAVAILABLE' &&
              preflight?.blocker?.code !== 'SQL_EXECUTION_FORBIDDEN' && (
                <p className="text-sm text-state-error">
                  Для выбранных данных нет доступного источника PostgreSQL.
                </p>
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
            {binding?.selection && (
              <p className="text-xs text-text-secondary">
                Лимит {binding.selection.profile.max_rows} строк · timeout{' '}
                {binding.selection.profile.statement_timeout_ms} мс
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
          <div className="flex items-center justify-between gap-3">
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setShowSql((value) => !value)}
            >
              <Code2 className="size-4" />
              {showSql ? 'Скрыть SQL' : 'Открыть SQL'}
            </Button>
            {showSql && (
              <Button size="sm" variant="ghost" onClick={() => void copySql()}>
                {copied ? (
                  <Check className="size-4" />
                ) : (
                  <Clipboard className="size-4" />
                )}
                {copied ? 'Скопировано' : 'Копировать'}
              </Button>
            )}
          </div>
          {showSql && (
            <>
              <pre className="mt-3 max-h-[440px] overflow-auto rounded-md bg-bg-accent p-5 text-xs leading-6 text-text-primary">
                <code>{compiled.sql}</code>
              </pre>
              <div className="mt-4 grid gap-4 sm:grid-cols-2">
                <div className="border-s-2 border-state-success ps-3">
                  <p className="text-xs text-text-secondary">SQL Guard</p>
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
      {!['READY', 'QUEUED', 'RUNNING', 'CANCEL_REQUESTED'].includes(
        run?.status || '',
      ) && (
        <ManualSqlForm
          project={project}
          initialSql={compiled?.sql || ''}
          onSaved={onRefresh}
        />
      )}
    </div>
  );
}

export function SqlAgentWorkbench() {
  const [projects, setProjects] = useState<SqlAgentProject[]>([]);
  const [project, setProject] = useState<SqlAgentProject | null>(null);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [terms, setTerms] = useState('');
  const [editingQuestion, setEditingQuestion] = useState(false);
  const [questionDraft, setQuestionDraft] = useState('');
  const [compiled, setCompiled] = useState<SqlQueryCompileResponse | null>(
    null,
  );
  const [compileError, setCompileError] = useState<string | null>(null);

  const loadProjects = useCallback(async (preferredId?: string) => {
    setLoading(true);
    try {
      const items = await listBusinessDocumentSqlAgentProjects();
      setProjects(items);
      const id = preferredId || items[0]?.id;
      if (id) {
        setProject(await fetchBusinessDocumentSqlAgentProject(id));
      } else {
        setProject(null);
      }
      setError(null);
    } catch (nextError) {
      setError(errorMessage(nextError));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadProjects();
  }, [loadProjects]);

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
        if (cancelled) return;
        setProject(refreshed);
        setProjects((current) =>
          current.map((item) => (item.id === refreshed.id ? refreshed : item)),
        );
      } catch (nextError) {
        if (!cancelled) setError(errorMessage(nextError));
      }
    }, 1200);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [runningProjectId]);

  const compileProjectId = project?.id;
  const compileStage = project?.stage;
  const compileVersion = project?.state_version;
  const compileArtifactId = project?.artifact_ids.query;
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
    let cancelled = false;
    const compile = async () => {
      try {
        const response = await compileBusinessDocumentSqlProject(
          compileProjectId,
          compileVersion,
          `compile-${compileArtifactId}`,
        );
        if (cancelled) return;
        const result = response.compilation;
        if (result.status !== 'READY' || !result.sql) {
          throw new Error(
            result.blocking_issues[0]?.message || 'SQL не сформирован.',
          );
        }
        setProject(response.project);
        setCompiled(result);
      } catch (nextError) {
        if (!cancelled) setCompileError(errorMessage(nextError));
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
  ]);

  const selectProject = async (projectId: string) => {
    setBusy(true);
    try {
      setProject(await fetchBusinessDocumentSqlAgentProject(projectId));
      setEditingQuestion(false);
      setCreating(false);
      setError(null);
    } catch (nextError) {
      setError(errorMessage(nextError));
    } finally {
      setBusy(false);
    }
  };

  const saveQuestion = async () => {
    if (!project || !questionDraft.trim()) return;
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
    setBusy(true);
    try {
      const created = await createBusinessDocumentSqlAgentProject({
        schema_version: '1',
        title,
        source_request: sourceRequest,
        locale: 'ru',
      });
      setProject(created);
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
    if (!project) return;
    const parsedTerms = terms
      .split(/\r?\n/)
      .map((item) => item.replace(/^\s*[-*•]\s+/, '').trim())
      .filter(Boolean);
    if (kind === 'SCHEMA' && !parsedTerms.length) {
      setError('Опишите, какие данные нужно найти в каталоге.');
      return;
    }
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
  ) => {
    if (!project?.pending_proposal) return;
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
      if (
        decision === 'ACCEPT' &&
        (updated.next_agent === 'SCHEMA' || updated.next_agent === 'QUERY')
      ) {
        const schemaTerms =
          updated.next_agent === 'SCHEMA'
            ? schemaTermsFromRequirements(updated)
            : [];
        if (updated.next_agent === 'SCHEMA' && schemaTerms.length === 0) {
          setError('Уточните, какие данные нужно найти в каталоге.');
          return;
        }
        if (schemaTerms.length) setTerms(schemaTerms.join('\n'));
        const started = await requestBusinessDocumentSqlAgent(updated.id, {
          schema_version: '1',
          expected_state_version: updated.state_version,
          idempotency_key: commandKey(
            `run-${updated.next_agent.toLowerCase()}`,
          ),
          kind: updated.next_agent,
          payload:
            updated.next_agent === 'SCHEMA'
              ? { locale: updated.locale, terms: schemaTerms }
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

  const currentAgent = project?.next_agent;
  const proposalKind = project?.pending_proposal?.kind;

  return (
    <div
      className="grid h-full min-h-0 lg:grid-cols-[260px_minmax(0,1fr)]"
      data-testid="sql-agent-workbench"
    >
      <aside className="min-h-0 border-e border-border-button bg-bg-base lg:overflow-y-auto">
        <div className="flex items-center justify-between gap-2 border-b border-border-button px-4 py-4">
          <div>
            <p className="text-sm font-semibold">SQL-проекты</p>
            <p className="text-xs text-text-disabled">
              {projects.length} всего
            </p>
          </div>
          <Button
            size="icon"
            variant="ghost"
            aria-label="Новый SQL-проект"
            onClick={() => setCreating(true)}
          >
            <Plus className="size-4" />
          </Button>
        </div>
        <div className="divide-y divide-border-button">
          {projects.map((item) => (
            <button
              key={item.id}
              type="button"
              className={`w-full px-4 py-3 text-left transition-colors ${
                project?.id === item.id && !creating
                  ? 'bg-bg-accent'
                  : 'hover:bg-bg-accent/60'
              }`}
              onClick={() => void selectProject(item.id)}
            >
              <span className="block truncate text-sm font-medium">
                {item.title}
              </span>
              <span className="mt-1 flex items-center gap-1.5 text-xs text-text-disabled">
                {item.operation_state === 'RUNNING' && (
                  <LoaderCircle className="size-3 animate-spin" />
                )}
                {item.next_action === 'OPEN_DOCUMENT'
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

      <div className="min-h-0 overflow-y-auto">
        {loading ? (
          <div className="flex h-full items-center justify-center">
            <LoaderCircle className="size-5 animate-spin text-accent-primary" />
          </div>
        ) : creating ? (
          <CreateProject
            busy={busy}
            onCancel={() => setCreating(false)}
            onSubmit={createProject}
          />
        ) : !project ? (
          <EmptyProjects onCreate={() => setCreating(true)} />
        ) : (
          <div className="min-h-full">
            <header className="px-5 py-5 lg:px-7">
              <div className="flex flex-wrap items-start justify-between gap-4">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <Bot className="size-4 text-accent-primary" />
                    <span className="text-xs font-medium text-text-secondary">
                      Агентский проект
                    </span>
                  </div>
                  <h2 className="mt-1 truncate text-2xl font-semibold tracking-tight">
                    {project.title}
                  </h2>
                  <p className="mt-1 line-clamp-2 max-w-3xl text-sm leading-6 text-text-secondary">
                    {project.source_request}
                  </p>
                  {project.stage !== 'COMPLETE' &&
                    project.operation_state !== 'RUNNING' && (
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
            </header>

            <main className="px-5 py-7 lg:px-7" aria-live="polite">
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
                proposalKind === 'REQUIREMENTS' && (
                  <RequirementsReview
                    project={project}
                    busy={busy}
                    onDecision={decide}
                  />
                )}
              {project.operation_state === 'REVIEW' &&
                proposalKind === 'SCHEMA' && (
                  <SchemaReview
                    project={project}
                    busy={busy}
                    onDecision={decide}
                  />
                )}
              {project.operation_state === 'REVIEW' &&
                proposalKind === 'QUERY' && (
                  <QueryReview
                    project={project}
                    busy={busy}
                    onDecision={decide}
                  />
                )}
              {project.stage === 'QUERY' &&
                project.operation_state === 'REVIEW' &&
                proposalKind === 'QUERY' && (
                  <div className="mx-auto max-w-2xl pb-8">
                    <ManualSqlForm
                      project={project}
                      onSaved={() => loadProjects(project.id)}
                    />
                  </div>
                )}

              {project.operation_state === 'IDLE' && currentAgent && (
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
                        ? 'Опишите, какие данные нужны для ответа. Каталог предложит подходящие источники и поля.'
                        : 'Агент использует только подтверждённые требования и снимок схемы.'}
                  </p>
                  {currentAgent === 'SCHEMA' && (
                    <label className="mt-5 block text-sm font-medium">
                      Что искать в данных?
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
                    Запустить агента
                  </Button>
                  {currentAgent === 'QUERY' && (
                    <ManualSqlForm
                      project={project}
                      onSaved={() => loadProjects(project.id)}
                    />
                  )}
                </section>
              )}

              {project.stage === 'COMPLETE' &&
                project.operation_state === 'IDLE' && (
                  <CompletedProject
                    project={project}
                    compiled={compiled}
                    compileError={compileError}
                    onRefresh={() => loadProjects(project.id)}
                  />
                )}
            </main>
          </div>
        )}
      </div>
    </div>
  );
}
