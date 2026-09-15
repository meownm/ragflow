import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
import { Progress } from '@/components/ui/progress';
import { Textarea } from '@/components/ui/textarea';
import type {
  SqlAgentKind,
  SqlAgentProject,
  SqlQueryCompileResponse,
} from '@/pages/business-documents/types';
import {
  compileBusinessDocumentSqlQuery,
  createBusinessDocumentSqlAgentProject,
  decideBusinessDocumentSqlAgentProposal,
  fetchBusinessDocumentSqlAgentProject,
  listBusinessDocumentSqlAgentProjects,
  loadBusinessDocumentSqlSchemaEntities,
  requestBusinessDocumentSqlAgent,
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
  Download,
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
  buildCompileRequestFromProject,
  buildSqlDocumentMarkdown,
  queryProposalFromPending,
  requirementsProposal,
  schemaArtifactFromWorkspace,
  schemaWorkspaceFromProposal,
} from './sql-agent-document';

const STAGES: Array<{
  kind: SqlAgentKind;
  label: string;
  short: string;
  description: string;
}> = [
  {
    kind: 'REQUIREMENTS',
    label: 'Требования',
    short: '01',
    description: 'Что получить и как ограничить данные',
  },
  {
    kind: 'SCHEMA',
    label: 'Схема данных',
    short: '02',
    description: 'Таблицы, поля и связи',
  },
  {
    kind: 'QUERY',
    label: 'SQL-запрос',
    short: '03',
    description: 'SELECT, JOIN, WHERE и лимиты',
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

function downloadText(filename: string, content: string, type: string) {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
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

function ProjectSteps({ project }: { project: SqlAgentProject }) {
  const current = KIND_ORDER[project.stage];
  return (
    <ol className="border-b border-border-button px-5 py-4 lg:px-7">
      <div className="grid gap-3 md:grid-cols-3">
        {STAGES.map((stage, index) => {
          const complete = index < current || project.stage === 'COMPLETE';
          const active = index === current && project.stage !== 'COMPLETE';
          return (
            <li
              key={stage.kind}
              className={`relative border-s-2 py-1 ps-4 transition-colors duration-300 ${
                complete
                  ? 'border-state-success'
                  : active
                    ? 'border-accent-primary'
                    : 'border-border-button'
              }`}
            >
              <div className="flex items-center gap-2 text-xs font-medium text-text-secondary">
                {complete ? (
                  <Check className="size-3.5 text-state-success" />
                ) : (
                  <span>{stage.short}</span>
                )}
                <span className={active ? 'text-accent-primary' : ''}>
                  {stage.label}
                </span>
              </div>
              <p className="mt-1 text-xs text-text-disabled">
                {stage.description}
              </p>
            </li>
          );
        })}
      </div>
    </ol>
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
          Соберите SQL-запрос по требованиям
        </h2>
        <p className="mt-2 text-sm leading-6 text-text-secondary">
          Три агента последовательно разберут задачу, найдут таблицы в каталоге
          и предложат безопасную структуру запроса. Каждый переход подтверждаете
          вы.
        </p>
        <Button className="mt-6" onClick={onCreate}>
          <Plus className="size-4" />
          Новый SQL-проект
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
  const valid = title.trim().length > 0 && source.trim().length > 0;
  return (
    <section
      className="mx-auto w-full max-w-3xl px-6 py-10 lg:px-10"
      data-testid="sql-agent-create-project"
    >
      <p className="text-xs font-medium uppercase tracking-[0.16em] text-accent-primary">
        Новый SQL-проект
      </p>
      <h2 className="mt-2 text-3xl font-semibold tracking-tight">
        Опишите результат на естественном языке
      </h2>
      <p className="mt-2 max-w-2xl text-sm leading-6 text-text-secondary">
        Добавьте сущности, нужные поля, период, фильтры и лимит. Неизвестные
        детали агент вынесет в отдельные вопросы.
      </p>
      <label className="mt-8 block text-sm font-medium">
        Название проекта
        <Input
          className="mt-2"
          value={title}
          maxLength={255}
          placeholder="Например, Активные клиенты по регионам"
          onChange={(event) => setTitle(event.target.value)}
        />
      </label>
      <label className="mt-5 block text-sm font-medium">
        Исходные требования
        <Textarea
          className="mt-2 min-h-52 leading-6"
          value={source}
          maxLength={20000}
          resize="vertical"
          placeholder="Получить активных клиентов и сумму их заказов за последние 90 дней. Вывести клиента, регион, сумму и дату последнего заказа. Исключить тестовые аккаунты, отсортировать по сумме, вернуть не более 500 строк."
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
            onClick={() => onSubmit(title.trim(), source.trim())}
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

  const selectAll = (resolutionIndex: number) => {
    setWorkspace((current) => ({
      ...current,
      resolutions: current.resolutions.map((resolution, index) => {
        if (index !== resolutionIndex) return resolution;
        const selected = resolution.candidates.find(
          (candidate) => candidate.id === resolution.selectedEntityId,
        );
        return selected
          ? {
              ...resolution,
              selectedColumnIds: selected.columns.map((column) => column.id),
            }
          : resolution;
      }),
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
            Выберите таблицы и нужные поля
          </h3>
          <p className="mt-1 text-sm text-text-secondary">
            Источник истины — каталог OpenMetadata. Неоднозначные соответствия
            подтверждаются вручную.
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
                  <p className="text-xs text-text-disabled">Сущность</p>
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
                      <span className="block truncate text-xs text-text-disabled">
                        {candidate.fqn}
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
                  <div className="flex items-center justify-between gap-3">
                    <p className="text-xs font-medium">
                      Поля для SELECT, JOIN и WHERE · выбрано{' '}
                      {resolution.selectedColumnIds.length}
                    </p>
                    <button
                      type="button"
                      className="text-xs text-accent-primary hover:underline"
                      onClick={() => selectAll(resolutionIndex)}
                    >
                      Выбрать все
                    </button>
                  </div>
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
                            {column.name}
                          </span>
                          <span className="block truncate text-text-disabled">
                            {column.dataType || 'тип не указан'}
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
  if (!proposal) {
    return (
      <p className="border-s-2 border-state-error ps-3 text-sm text-state-error">
        Агент не сформировал проверяемое предложение. Отклоните его и повторите
        шаг.
      </p>
    );
  }
  return (
    <div className="space-y-7" data-testid="sql-agent-query-review">
      <div>
        <p className="text-xs font-medium uppercase tracking-[0.14em] text-accent-primary">
          Предложение агента SQL
        </p>
        <h3 className="mt-1 text-xl font-semibold">Проверьте план запроса</h3>
        <p className="mt-1 text-sm text-text-secondary">
          Подтверждение фиксирует JOIN и WHERE как решения пользователя. Затем
          компилятор сформирует read-only SQL.
        </p>
      </div>
      <div className="grid gap-7 lg:grid-cols-2">
        <section>
          <h4 className="flex items-center gap-2 text-sm font-semibold">
            <Table2 className="size-4" /> SELECT
          </h4>
          <div className="mt-3 divide-y divide-border-button border-y border-border-button">
            {proposal.select.map((item) => (
              <div key={item.id} className="py-3 text-xs">
                <span className="font-medium">{item.alias}</span>
                <span className="ms-2 text-text-secondary">
                  {item.kind} · {item.column_id}
                </span>
              </div>
            ))}
          </div>
        </section>
        <section>
          <h4 className="flex items-center gap-2 text-sm font-semibold">
            <Database className="size-4" /> JOIN
          </h4>
          <div className="mt-3 space-y-3">
            {proposal.joins.length ? (
              proposal.joins.map((join) => (
                <div
                  key={join.id}
                  className="border-s-2 border-border-button ps-3 text-xs"
                >
                  <p className="font-medium">
                    {join.join_type} · {join.entity_id}
                  </p>
                  <p className="mt-1 break-all text-text-secondary">
                    {join.left_column_id} = {join.right_column_id}
                  </p>
                  <p className="mt-1 text-text-disabled">{join.description}</p>
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
            <Code2 className="size-4" /> WHERE
          </h4>
          <div className="mt-3 space-y-3">
            {proposal.filters.length ? (
              proposal.filters.map((filter) => (
                <div
                  key={filter.id}
                  className="border-s-2 border-border-button ps-3 text-xs"
                >
                  <p className="font-medium">{filter.description}</p>
                  <p className="mt-1 break-all font-mono text-text-secondary">
                    {filter.column_id} {filter.operator}{' '}
                    {filter.parameter_name ? `:${filter.parameter_name}` : ''}
                  </p>
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
                .map((item) => `${item.select_item_id} ${item.direction}`)
                .join(', ') || 'не задана'}
            </dd>
          </dl>
        </section>
      </div>
      <DecisionBar
        busy={busy}
        acceptDisabled={false}
        acceptLabel="Подтвердить и собрать SQL"
        onAccept={() =>
          onDecision('ACCEPT', proposal as unknown as Record<string, unknown>)
        }
        onReject={() => onDecision('REJECT', null)}
      />
    </div>
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

function CompletedProject({
  project,
  compiled,
  compileError,
}: {
  project: SqlAgentProject;
  compiled: SqlQueryCompileResponse | null;
  compileError: string | null;
}) {
  const [copied, setCopied] = useState(false);
  const markdown = compiled
    ? buildSqlDocumentMarkdown(project, compiled)
    : null;
  const copySql = async () => {
    if (!compiled?.sql) return;
    await navigator.clipboard.writeText(compiled.sql);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1500);
  };
  return (
    <div data-testid="sql-agent-complete">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="text-xs font-medium uppercase tracking-[0.14em] text-state-success">
            Проект готов
          </p>
          <h3 className="mt-1 text-2xl font-semibold">
            SQL и спецификация собраны
          </h3>
          <p className="mt-1 text-sm text-text-secondary">
            Запрос прошёл детерминированную компиляцию и read-only проверку.
          </p>
        </div>
        {markdown && (
          <Button
            variant="outline"
            onClick={() =>
              downloadText(
                `${project.title.replace(/[^a-zа-я0-9_-]+/gi, '-') || 'sql-project'}.md`,
                markdown,
                'text/markdown;charset=utf-8',
              )
            }
          >
            <Download className="size-4" />
            Документ .md
          </Button>
        )}
      </div>
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
      {compiled?.sql && (
        <section className="mt-7">
          <div className="flex items-center justify-between gap-3">
            <h4 className="flex items-center gap-2 text-sm font-semibold">
              <Code2 className="size-4" /> Итоговый SQL
            </h4>
            <Button size="sm" variant="ghost" onClick={() => void copySql()}>
              {copied ? (
                <Check className="size-4" />
              ) : (
                <Clipboard className="size-4" />
              )}
              {copied ? 'Скопировано' : 'Копировать'}
            </Button>
          </div>
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
        </section>
      )}
      <section className="mt-8 border-t border-border-button pt-5">
        <h4 className="text-sm font-semibold">Постобработка на Python</h4>
        <p className="mt-1 text-sm text-text-secondary">
          Раздел включён в документ, выполнение появится в следующей версии.
        </p>
        <Badge className="mt-3" variant="outline">
          Запланировано
        </Badge>
      </section>
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
    project?.operation_state === 'RUNNING' ? project.id : null;
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

  useEffect(() => {
    setCompiled(null);
    setCompileError(null);
    if (!project || project.stage !== 'COMPLETE') return;
    let cancelled = false;
    const compile = async () => {
      try {
        const result = await compileBusinessDocumentSqlQuery(
          buildCompileRequestFromProject(project),
        );
        if (cancelled) return;
        if (result.status !== 'READY' || !result.sql) {
          throw new Error(
            result.blocking_issues[0]?.message || 'SQL не сформирован.',
          );
        }
        setCompiled(result);
      } catch (nextError) {
        if (!cancelled) setCompileError(errorMessage(nextError));
      }
    };
    void compile();
    return () => {
      cancelled = true;
    };
  }, [project]);

  const selectProject = async (projectId: string) => {
    setBusy(true);
    try {
      setProject(await fetchBusinessDocumentSqlAgentProject(projectId));
      setCreating(false);
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
      setError('Укажите хотя бы одну сущность для поиска в каталоге.');
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
                {item.stage === 'COMPLETE'
                  ? 'Готов'
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

            <ProjectSteps project={project} />

            <main className="px-5 py-7 lg:px-7" aria-live="polite">
              {error && (
                <div className="mb-6 flex items-start gap-2 border-s-2 border-state-error ps-3 text-sm text-state-error">
                  <AlertCircle className="mt-0.5 size-4 shrink-0" />
                  <span>{error}</span>
                </div>
              )}
              {project.last_error && (
                <div className="mb-6 border-s-2 border-state-error ps-3 text-sm text-state-error">
                  {project.last_error.message || project.last_error.code}
                </div>
              )}

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

              {project.operation_state === 'IDLE' && currentAgent && (
                <section className="mx-auto max-w-2xl py-7">
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
                        ? 'Найти сущности в каталоге'
                        : 'Собрать план SQL-запроса'}
                  </h3>
                  <p className="mt-2 text-sm leading-6 text-text-secondary">
                    {currentAgent === 'REQUIREMENTS'
                      ? 'Агент выделит требования к выводу, соединениям, фильтрации, сортировке и лимитам.'
                      : currentAgent === 'SCHEMA'
                        ? 'Перечислите бизнес-сущности по одной на строку. Агент предложит соответствующие таблицы и поля.'
                        : 'Агент использует только подтверждённые требования и снимок схемы.'}
                  </p>
                  {currentAgent === 'SCHEMA' && (
                    <label className="mt-5 block text-sm font-medium">
                      Сущности и понятия
                      <Textarea
                        className="mt-2 min-h-36"
                        value={terms}
                        placeholder={'клиент\nзаказ\nрегион'}
                        onChange={(event) => setTerms(event.target.value)}
                      />
                      <span className="mt-2 block text-xs text-text-disabled">
                        До 8 сущностей за один шаг
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
                </section>
              )}

              {project.stage === 'COMPLETE' &&
                project.operation_state === 'IDLE' && (
                  <CompletedProject
                    project={project}
                    compiled={compiled}
                    compileError={compileError}
                  />
                )}
            </main>
          </div>
        )}
      </div>
    </div>
  );
}
