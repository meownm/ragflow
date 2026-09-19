import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import type {
  SqlQueryCompileResponse,
  SqlQueryPlanResponse,
} from '@/pages/business-documents/types';
import {
  compileBusinessDocumentSqlQuery,
  planBusinessDocumentSqlQuery,
} from '@/services/business-document-service';
import { AlertTriangle, Braces, Plus, Trash2 } from 'lucide-react';
import { useMemo, useState } from 'react';
import type { DocumentConstructorStorageScope } from './draft-storage';
import { ExecutionBindingPanel } from './execution-binding-panel';
import { QueryCompilerPanel } from './query-compiler-panel';
import { QueryPlannerPanel } from './query-planner-panel';
import {
  applyQueryPlanProposal,
  buildQueryCompileRequest,
  buildQueryPlanRequest,
  changeQueryBaseTable,
  createQueryFilter,
  createQuerySpecificationDraft,
  selectedSchemaTables,
  validateQuerySpecificationDraft,
  type QueryFilterOperator,
  type QueryParameterType,
  type QuerySelectKind,
  type QuerySpecificationDraft,
} from './query-specification';
import {
  buildSchemaSnapshot,
  type SchemaWorkspaceState,
} from './schema-workspace';
import {
  createSchemaWorkspaceStorageKey,
  emptySchemaWorkspace,
  loadSchemaWorkspace,
} from './schema-workspace-storage';

const SELECT_KINDS: Array<{ value: QuerySelectKind; label: string }> = [
  { value: 'column', label: 'Поле без преобразования' },
  { value: 'sum', label: 'SUM' },
  { value: 'avg', label: 'AVG' },
  { value: 'min', label: 'MIN' },
  { value: 'max', label: 'MAX' },
  { value: 'count', label: 'COUNT' },
  { value: 'count_distinct', label: 'COUNT DISTINCT' },
  { value: 'date_bucket', label: 'Период даты' },
];

const FILTER_OPERATORS: Array<{
  value: QueryFilterOperator;
  label: string;
}> = [
  { value: 'eq', label: '= равно' },
  { value: 'ne', label: '<> не равно' },
  { value: 'gt', label: '> больше' },
  { value: 'gte', label: '>= не меньше' },
  { value: 'lt', label: '< меньше' },
  { value: 'lte', label: '<= не больше' },
  { value: 'like', label: 'LIKE' },
  { value: 'ilike', label: 'ILIKE без учёта регистра' },
  { value: 'in', label: 'IN — входит в список' },
  { value: 'not_in', label: 'NOT IN — не входит в список' },
  { value: 'is_null', label: 'IS NULL' },
  { value: 'is_not_null', label: 'IS NOT NULL' },
];

const SCALAR_PARAMETER_TYPES: Array<{
  value: QueryParameterType;
  label: string;
}> = [
  { value: 'text', label: 'Текст' },
  { value: 'integer', label: 'Целое число' },
  { value: 'decimal', label: 'Десятичное число' },
  { value: 'boolean', label: 'Логическое true/false' },
  { value: 'date', label: 'Дата YYYY-MM-DD' },
  { value: 'datetime', label: 'Дата и время ISO 8601' },
];

const LIST_PARAMETER_TYPES: Array<{
  value: QueryParameterType;
  label: string;
}> = [
  { value: 'text_list', label: 'Список текстов' },
  { value: 'integer_list', label: 'Список целых чисел' },
];

const SELECT_CLASS =
  'h-8 w-full rounded-md border border-border-button bg-bg-input px-3 text-sm text-text-primary outline-none focus:ring-1 focus:ring-accent-primary';

function browserStorage() {
  try {
    return typeof window === 'undefined' ? undefined : window.localStorage;
  } catch {
    return undefined;
  }
}

function nextId(prefix: string, ids: string[]) {
  let index = ids.length + 1;
  while (ids.includes(`${prefix}-${index}`)) index += 1;
  return `${prefix}-${index}`;
}

function safeColumnAlias(name: string, aliases: string[]) {
  const base =
    name
      .trim()
      .replace(/[^A-Za-z0-9_$]+/g, '_')
      .replace(/^[^A-Za-z_]+/, '') || 'field';
  const used = new Set(aliases.map((alias) => alias.toLocaleLowerCase()));
  let result = base.slice(0, 120);
  let suffix = 2;
  while (used.has(result.toLocaleLowerCase())) {
    result = `${base.slice(0, 115)}_${suffix}`;
    suffix += 1;
  }
  return result;
}

function isParameterless(operator: QueryFilterOperator) {
  return operator === 'is_null' || operator === 'is_not_null';
}

function isListOperator(operator: QueryFilterOperator) {
  return operator === 'in' || operator === 'not_in';
}

export function QuerySpecificationDialog({
  scope,
}: {
  scope: DocumentConstructorStorageScope;
}) {
  const [open, setOpen] = useState(false);
  const [workspace, setWorkspace] = useState<SchemaWorkspaceState>(() =>
    emptySchemaWorkspace(),
  );
  const [draft, setDraft] = useState<QuerySpecificationDraft | null>(null);
  const [snapshotIdentity, setSnapshotIdentity] = useState('');
  const [newSelectColumnId, setNewSelectColumnId] = useState('');
  const [result, setResult] = useState<SqlQueryCompileResponse | null>(null);
  const [planResult, setPlanResult] = useState<SqlQueryPlanResponse | null>(
    null,
  );
  const [error, setError] = useState<string | null>(null);
  const [plannerError, setPlannerError] = useState<string | null>(null);
  const [compiling, setCompiling] = useState(false);
  const [planning, setPlanning] = useState(false);

  const tables = useMemo(() => selectedSchemaTables(workspace), [workspace]);
  const columns = useMemo(
    () =>
      tables.flatMap(({ table }) =>
        table.columns.map((column) => ({
          ...column,
          tableId: table.id,
          label: `${table.fqn}.${column.name}`,
        })),
      ),
    [tables],
  );
  const snapshot = useMemo(
    () => buildSchemaSnapshot(workspace.resolutions, workspace.requirements),
    [workspace],
  );
  const issues = useMemo(
    () => (draft ? validateQuerySpecificationDraft(workspace, draft) : []),
    [draft, workspace],
  );

  const loadCurrentSchema = () => {
    const key = createSchemaWorkspaceStorageKey(scope);
    const currentWorkspace = loadSchemaWorkspace(browserStorage(), key, scope);
    const currentSnapshot = buildSchemaSnapshot(
      currentWorkspace.resolutions,
      currentWorkspace.requirements,
    );
    const identity = JSON.stringify(currentSnapshot);
    setWorkspace(currentWorkspace);
    if (!draft || identity !== snapshotIdentity) {
      const nextDraft = createQuerySpecificationDraft(currentWorkspace);
      setDraft(nextDraft);
      setNewSelectColumnId(
        selectedSchemaTables(currentWorkspace)[0]?.table.columns[0]?.id || '',
      );
      setSnapshotIdentity(identity);
      setResult(null);
      setPlanResult(null);
      setError(null);
      setPlannerError(null);
    }
  };

  const handleOpenChange = (nextOpen: boolean) => {
    setOpen(nextOpen);
    if (nextOpen) loadCurrentSchema();
  };

  const changeDraft = (
    update: (current: QuerySpecificationDraft) => QuerySpecificationDraft,
  ) => {
    setDraft((current) => (current ? update(current) : current));
    setResult(null);
    setError(null);
  };

  const addSelectItem = () => {
    const column = columns.find((item) => item.id === newSelectColumnId);
    if (!column) return;
    changeDraft((current) => {
      const id = nextId(
        'select',
        current.select.map((item) => item.id),
      );
      const item = {
        id,
        columnId: column.id,
        kind: 'column' as const,
        alias: safeColumnAlias(
          column.name,
          current.select.map((existing) => existing.alias),
        ),
        grain: null,
      };
      return {
        ...current,
        select: [...current.select, item],
        orderBy: current.orderBy.length
          ? current.orderBy
          : [{ selectItemId: id, direction: 'ASC' }],
      };
    });
  };

  const addFilter = () => {
    changeDraft((current) => {
      const id = nextId(
        'filter',
        current.filters.map((item) => item.id),
      );
      const index = Number(id.split('-').at(-1));
      const filter = createQueryFilter(index);
      filter.id = id;
      filter.columnId = columns[0]?.id || '';
      return { ...current, filters: [...current.filters, filter] };
    });
  };

  const addOrderItem = () => {
    changeDraft((current) => {
      const used = new Set(current.orderBy.map((item) => item.selectItemId));
      const selectItem = current.select.find((item) => !used.has(item.id));
      if (!selectItem) return current;
      return {
        ...current,
        orderBy: [
          ...current.orderBy,
          { selectItemId: selectItem.id, direction: 'ASC' },
        ],
      };
    });
  };

  const compile = async () => {
    if (!draft || issues.length) return;
    setCompiling(true);
    setError(null);
    setResult(null);
    try {
      setResult(
        await compileBusinessDocumentSqlQuery(
          buildQueryCompileRequest(workspace, draft),
        ),
      );
    } catch (compileError) {
      setError(
        compileError instanceof Error
          ? compileError.message
          : 'Не удалось проверить и скомпилировать запрос.',
      );
    } finally {
      setCompiling(false);
    }
  };

  const plan = async () => {
    if (snapshot.status !== 'READY') return;
    setPlanning(true);
    setPlannerError(null);
    setResult(null);
    try {
      const nextResult = await planBusinessDocumentSqlQuery(
        buildQueryPlanRequest(workspace),
      );
      setPlanResult(nextResult);
      if (nextResult.proposal) {
        setDraft(applyQueryPlanProposal(nextResult.proposal));
      }
    } catch (planError) {
      setPlannerError(
        planError instanceof Error
          ? planError.message
          : 'Не удалось получить структурированный план запроса.',
      );
    } finally {
      setPlanning(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogTrigger asChild>
        <Button
          size="sm"
          variant="outline"
          data-testid="open-query-specification"
        >
          <Braces className="size-4" />
          <span className="hidden sm:inline">SQL-запрос</span>
        </Button>
      </DialogTrigger>
      <DialogContent className="max-h-[94vh] max-w-[min(1120px,calc(100vw-2rem))] overflow-hidden">
        <DialogHeader>
          <DialogTitle>Спецификация SQL-запроса</DialogTitle>
          <DialogDescription className="text-text-secondary">
            Настройте SELECT, JOIN, отдельные WHERE-условия, сортировку и лимит.
            SQL появится только после подтверждения всех решений и серверной
            проверки.
          </DialogDescription>
        </DialogHeader>

        <div
          className="min-h-0 space-y-6 overflow-y-auto pe-2"
          data-testid="query-specification-workspace"
        >
          {snapshot.status !== 'READY' || !draft ? (
            <section
              className="rounded-md border border-state-warning/40 bg-state-warning/5 p-4"
              role="alert"
            >
              <div className="flex items-start gap-3">
                <AlertTriangle className="mt-0.5 size-5 shrink-0 text-state-warning" />
                <div>
                  <h3 className="text-sm font-semibold">
                    Сначала подготовьте снимок схемы
                  </h3>
                  <p className="mt-1 text-xs text-text-secondary">
                    В разделе «Схема данных» укажите исходные требования,
                    выберите физические таблицы и хотя бы одно поле. Статус
                    снимка должен быть READY и freshness — актуальным.
                  </p>
                </div>
              </div>
            </section>
          ) : (
            <>
              <ExecutionBindingPanel
                key={snapshotIdentity}
                request={buildQueryPlanRequest(workspace)}
              />

              <QueryPlannerPanel
                result={planResult}
                error={plannerError}
                planning={planning}
                onPlan={plan}
              />

              <section className="space-y-3">
                <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border-button pb-3">
                  <div>
                    <h3 className="text-sm font-semibold">
                      1. Таблицы и псевдонимы
                    </h3>
                    <p className="mt-1 text-xs text-text-secondary">
                      Снимок схемы зафиксирован. Смена базовой таблицы
                      сбрасывает подтверждения JOIN.
                    </p>
                  </div>
                  <Badge
                    variant="outline"
                    className="border-state-success/40 text-state-success"
                  >
                    Снимок READY · {tables.length}{' '}
                    {tables.length === 1 ? 'таблица' : 'таблиц'}
                  </Badge>
                </div>
                <div className="grid gap-3 md:grid-cols-2">
                  <label className="space-y-1 text-xs font-medium">
                    <span>Базовая таблица FROM</span>
                    <select
                      aria-label="Базовая таблица FROM"
                      className={SELECT_CLASS}
                      value={draft.baseEntityId}
                      onChange={(event) =>
                        changeDraft((current) =>
                          changeQueryBaseTable(
                            workspace,
                            current,
                            event.target.value,
                          ),
                        )
                      }
                    >
                      {tables.map(({ table }) => (
                        <option key={table.id} value={table.id}>
                          {table.fqn}
                        </option>
                      ))}
                    </select>
                  </label>
                  <div className="grid gap-2 sm:grid-cols-2">
                    {tables.map(({ table }) => (
                      <label
                        className="space-y-1 text-xs font-medium"
                        key={table.id}
                      >
                        <span className="block truncate" title={table.fqn}>
                          Alias · {table.fqn}
                        </span>
                        <Input
                          aria-label={`Alias таблицы ${table.fqn}`}
                          value={draft.aliases[table.id] || ''}
                          maxLength={128}
                          onChange={(event) =>
                            changeDraft((current) => ({
                              ...current,
                              aliases: {
                                ...current.aliases,
                                [table.id]: String(event.target.value),
                              },
                              joins: current.joins.map((join) =>
                                join.entityId === table.id
                                  ? {
                                      ...join,
                                      alias: String(event.target.value),
                                      confirmed: false,
                                    }
                                  : join,
                              ),
                            }))
                          }
                        />
                      </label>
                    ))}
                  </div>
                </div>
              </section>

              <section className="space-y-3">
                <div className="flex flex-wrap items-end justify-between gap-3">
                  <div>
                    <h3 className="text-sm font-semibold">
                      2. Что вывести — SELECT
                    </h3>
                    <p className="mt-1 text-xs text-text-secondary">
                      Каждая строка — отдельное поле или агрегат результата.
                    </p>
                  </div>
                  <div className="flex min-w-[280px] flex-1 gap-2 sm:max-w-xl">
                    <select
                      aria-label="Поле для добавления в SELECT"
                      className={SELECT_CLASS}
                      value={newSelectColumnId}
                      onChange={(event) =>
                        setNewSelectColumnId(event.target.value)
                      }
                    >
                      {columns.map((column) => (
                        <option key={column.id} value={column.id}>
                          {column.label}
                        </option>
                      ))}
                    </select>
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      onClick={addSelectItem}
                      disabled={!newSelectColumnId}
                    >
                      <Plus className="size-4" />
                      Добавить
                    </Button>
                  </div>
                </div>
                <div className="space-y-2">
                  {draft.select.map((item, index) => (
                    <article
                      key={item.id}
                      className="grid gap-2 rounded-md border border-border-button p-3 md:grid-cols-[minmax(240px,2fr)_minmax(180px,1fr)_minmax(140px,1fr)_auto]"
                      data-testid="query-select-item"
                    >
                      <label className="space-y-1 text-xs font-medium">
                        <span>Поле</span>
                        <select
                          aria-label={`Поле SELECT ${index + 1}`}
                          className={SELECT_CLASS}
                          value={item.columnId}
                          onChange={(event) =>
                            changeDraft((current) => ({
                              ...current,
                              select: current.select.map((currentItem) =>
                                currentItem.id === item.id
                                  ? {
                                      ...currentItem,
                                      columnId: event.target.value,
                                      alias: safeColumnAlias(
                                        columns.find(
                                          (column) =>
                                            column.id === event.target.value,
                                        )?.name || currentItem.alias,
                                        current.select
                                          .filter(
                                            (existing) =>
                                              existing.id !== currentItem.id,
                                          )
                                          .map((existing) => existing.alias),
                                      ),
                                    }
                                  : currentItem,
                              ),
                            }))
                          }
                        >
                          {columns.map((column) => (
                            <option key={column.id} value={column.id}>
                              {column.label}
                            </option>
                          ))}
                        </select>
                      </label>
                      <label className="space-y-1 text-xs font-medium">
                        <span>Преобразование</span>
                        <select
                          aria-label={`Преобразование SELECT ${index + 1}`}
                          className={SELECT_CLASS}
                          value={item.kind}
                          onChange={(event) => {
                            const kind = event.target.value as QuerySelectKind;
                            changeDraft((current) => ({
                              ...current,
                              select: current.select.map((currentItem) =>
                                currentItem.id === item.id
                                  ? {
                                      ...currentItem,
                                      kind,
                                      grain:
                                        kind === 'date_bucket'
                                          ? currentItem.grain || 'month'
                                          : null,
                                    }
                                  : currentItem,
                              ),
                            }));
                          }}
                        >
                          {SELECT_KINDS.map((kind) => (
                            <option key={kind.value} value={kind.value}>
                              {kind.label}
                            </option>
                          ))}
                        </select>
                      </label>
                      <label className="space-y-1 text-xs font-medium">
                        <span>Alias результата</span>
                        <Input
                          aria-label={`Alias SELECT ${index + 1}`}
                          value={item.alias}
                          maxLength={128}
                          onChange={(event) =>
                            changeDraft((current) => ({
                              ...current,
                              select: current.select.map((currentItem) =>
                                currentItem.id === item.id
                                  ? {
                                      ...currentItem,
                                      alias: String(event.target.value),
                                    }
                                  : currentItem,
                              ),
                            }))
                          }
                        />
                      </label>
                      <div className="flex items-end gap-2">
                        {item.kind === 'date_bucket' && (
                          <select
                            aria-label={`Период SELECT ${index + 1}`}
                            className={SELECT_CLASS}
                            value={item.grain || 'month'}
                            onChange={(event) =>
                              changeDraft((current) => ({
                                ...current,
                                select: current.select.map((currentItem) =>
                                  currentItem.id === item.id
                                    ? {
                                        ...currentItem,
                                        grain: event.target
                                          .value as NonNullable<
                                          typeof currentItem.grain
                                        >,
                                      }
                                    : currentItem,
                                ),
                              }))
                            }
                          >
                            <option value="day">День</option>
                            <option value="week">Неделя</option>
                            <option value="month">Месяц</option>
                            <option value="quarter">Квартал</option>
                            <option value="year">Год</option>
                          </select>
                        )}
                        <Button
                          type="button"
                          size="icon"
                          variant="ghost"
                          aria-label={`Удалить SELECT ${index + 1}`}
                          onClick={() =>
                            changeDraft((current) => ({
                              ...current,
                              select: current.select.filter(
                                (currentItem) => currentItem.id !== item.id,
                              ),
                              orderBy: current.orderBy.filter(
                                (order) => order.selectItemId !== item.id,
                              ),
                            }))
                          }
                        >
                          <Trash2 className="size-4" />
                        </Button>
                      </div>
                    </article>
                  ))}
                </div>
              </section>

              <section className="space-y-3">
                <div>
                  <h3 className="text-sm font-semibold">
                    3. Как соединить таблицы — JOIN
                  </h3>
                  <p className="mt-1 text-xs text-text-secondary">
                    Предложение по совпадающим ключам — только подсказка. Каждое
                    соединение требует явного подтверждения.
                  </p>
                </div>
                {draft.joins.length ? (
                  <div className="space-y-3">
                    {draft.joins.map((join, index) => {
                      const target = tables.find(
                        ({ table }) => table.id === join.entityId,
                      );
                      const leftTableIds = new Set([
                        draft.baseEntityId,
                        ...draft.joins
                          .slice(0, index)
                          .map((item) => item.entityId),
                      ]);
                      const leftColumns = columns.filter((column) =>
                        leftTableIds.has(column.tableId),
                      );
                      const rightColumns = columns.filter(
                        (column) => column.tableId === join.entityId,
                      );
                      return (
                        <article
                          key={join.id}
                          className="space-y-3 rounded-md border border-border-button p-3"
                          data-testid="query-join-item"
                        >
                          <div className="flex flex-wrap items-center justify-between gap-2">
                            <h4 className="text-xs font-semibold">
                              {index + 1}. {target?.table.fqn || join.entityId}
                            </h4>
                            <label className="flex items-center gap-2 text-xs font-medium">
                              <input
                                type="checkbox"
                                aria-label={`Подтвердить JOIN ${index + 1}`}
                                checked={join.confirmed}
                                onChange={(event) =>
                                  changeDraft((current) => ({
                                    ...current,
                                    joins: current.joins.map((currentJoin) =>
                                      currentJoin.id === join.id
                                        ? {
                                            ...currentJoin,
                                            confirmed: event.target.checked,
                                          }
                                        : currentJoin,
                                    ),
                                  }))
                                }
                                className="accent-[var(--color-accent-primary)]"
                              />
                              Решение подтверждено
                            </label>
                          </div>
                          <div className="grid gap-2 md:grid-cols-[140px_1fr_1fr]">
                            <label className="space-y-1 text-xs font-medium">
                              <span>Тип</span>
                              <select
                                aria-label={`Тип JOIN ${index + 1}`}
                                className={SELECT_CLASS}
                                value={join.joinType}
                                onChange={(event) =>
                                  changeDraft((current) => ({
                                    ...current,
                                    joins: current.joins.map((currentJoin) =>
                                      currentJoin.id === join.id
                                        ? {
                                            ...currentJoin,
                                            joinType: event.target.value as
                                              | 'INNER'
                                              | 'LEFT',
                                            confirmed: false,
                                          }
                                        : currentJoin,
                                    ),
                                  }))
                                }
                              >
                                <option value="INNER">INNER JOIN</option>
                                <option value="LEFT">LEFT JOIN</option>
                              </select>
                            </label>
                            <label className="space-y-1 text-xs font-medium">
                              <span>Поле уже связанной таблицы</span>
                              <select
                                aria-label={`Левое поле JOIN ${index + 1}`}
                                className={SELECT_CLASS}
                                value={join.leftColumnId}
                                onChange={(event) =>
                                  changeDraft((current) => ({
                                    ...current,
                                    joins: current.joins.map((currentJoin) =>
                                      currentJoin.id === join.id
                                        ? {
                                            ...currentJoin,
                                            leftColumnId: event.target.value,
                                            confirmed: false,
                                          }
                                        : currentJoin,
                                    ),
                                  }))
                                }
                              >
                                <option value="">Выберите поле</option>
                                {leftColumns.map((column) => (
                                  <option key={column.id} value={column.id}>
                                    {column.label}
                                  </option>
                                ))}
                              </select>
                            </label>
                            <label className="space-y-1 text-xs font-medium">
                              <span>Поле присоединяемой таблицы</span>
                              <select
                                aria-label={`Правое поле JOIN ${index + 1}`}
                                className={SELECT_CLASS}
                                value={join.rightColumnId}
                                onChange={(event) =>
                                  changeDraft((current) => ({
                                    ...current,
                                    joins: current.joins.map((currentJoin) =>
                                      currentJoin.id === join.id
                                        ? {
                                            ...currentJoin,
                                            rightColumnId: event.target.value,
                                            confirmed: false,
                                          }
                                        : currentJoin,
                                    ),
                                  }))
                                }
                              >
                                <option value="">Выберите поле</option>
                                {rightColumns.map((column) => (
                                  <option key={column.id} value={column.id}>
                                    {column.label}
                                  </option>
                                ))}
                              </select>
                            </label>
                          </div>
                          <label className="space-y-1 text-xs font-medium">
                            <span>Описание на естественном языке</span>
                            <Textarea
                              aria-label={`Описание JOIN ${index + 1}`}
                              rows={2}
                              maxLength={2000}
                              resize="vertical"
                              value={join.description}
                              onChange={(event) =>
                                changeDraft((current) => ({
                                  ...current,
                                  joins: current.joins.map((currentJoin) =>
                                    currentJoin.id === join.id
                                      ? {
                                          ...currentJoin,
                                          description: event.target.value,
                                          confirmed: false,
                                        }
                                      : currentJoin,
                                  ),
                                }))
                              }
                            />
                          </label>
                        </article>
                      );
                    })}
                  </div>
                ) : (
                  <p className="text-xs text-text-secondary">
                    Выбрана одна таблица — соединения не требуются.
                  </p>
                )}
              </section>

              <section className="space-y-3">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <h3 className="text-sm font-semibold">
                      4. Как ограничить данные — WHERE
                    </h3>
                    <p className="mt-1 text-xs text-text-secondary">
                      Каждое условие — отдельная карточка; значение передаётся
                      параметром, а не вставляется в SQL.
                    </p>
                  </div>
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    onClick={addFilter}
                  >
                    <Plus className="size-4" />
                    Условие WHERE
                  </Button>
                </div>
                {draft.filters.map((filter, index) => {
                  const listOperator = isListOperator(filter.operator);
                  const parameterless = isParameterless(filter.operator);
                  const parameterTypes = listOperator
                    ? LIST_PARAMETER_TYPES
                    : SCALAR_PARAMETER_TYPES;
                  return (
                    <article
                      key={filter.id}
                      className="space-y-3 rounded-md border border-border-button p-3"
                      data-testid="query-filter-item"
                    >
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <h4 className="text-xs font-semibold">
                          Условие {index + 1}
                        </h4>
                        <div className="flex items-center gap-2">
                          <label className="flex items-center gap-2 text-xs font-medium">
                            <input
                              type="checkbox"
                              aria-label={`Подтвердить WHERE ${index + 1}`}
                              checked={filter.confirmed}
                              onChange={(event) =>
                                changeDraft((current) => ({
                                  ...current,
                                  filters: current.filters.map(
                                    (currentFilter) =>
                                      currentFilter.id === filter.id
                                        ? {
                                            ...currentFilter,
                                            confirmed: event.target.checked,
                                          }
                                        : currentFilter,
                                  ),
                                }))
                              }
                              className="accent-[var(--color-accent-primary)]"
                            />
                            Решение подтверждено
                          </label>
                          <Button
                            type="button"
                            size="icon"
                            variant="ghost"
                            aria-label={`Удалить WHERE ${index + 1}`}
                            onClick={() =>
                              changeDraft((current) => ({
                                ...current,
                                filters: current.filters.filter(
                                  (currentFilter) =>
                                    currentFilter.id !== filter.id,
                                ),
                              }))
                            }
                          >
                            <Trash2 className="size-4" />
                          </Button>
                        </div>
                      </div>
                      <div className="grid gap-2 md:grid-cols-2 lg:grid-cols-4">
                        <label className="space-y-1 text-xs font-medium lg:col-span-2">
                          <span>Поле</span>
                          <select
                            aria-label={`Поле WHERE ${index + 1}`}
                            className={SELECT_CLASS}
                            value={filter.columnId}
                            onChange={(event) =>
                              changeDraft((current) => ({
                                ...current,
                                filters: current.filters.map((currentFilter) =>
                                  currentFilter.id === filter.id
                                    ? {
                                        ...currentFilter,
                                        columnId: event.target.value,
                                        confirmed: false,
                                      }
                                    : currentFilter,
                                ),
                              }))
                            }
                          >
                            {columns.map((column) => (
                              <option key={column.id} value={column.id}>
                                {column.label}
                              </option>
                            ))}
                          </select>
                        </label>
                        <label className="space-y-1 text-xs font-medium">
                          <span>Оператор</span>
                          <select
                            aria-label={`Оператор WHERE ${index + 1}`}
                            className={SELECT_CLASS}
                            value={filter.operator}
                            onChange={(event) => {
                              const operator = event.target
                                .value as QueryFilterOperator;
                              changeDraft((current) => ({
                                ...current,
                                filters: current.filters.map((currentFilter) =>
                                  currentFilter.id === filter.id
                                    ? {
                                        ...currentFilter,
                                        operator,
                                        parameterType: isListOperator(operator)
                                          ? currentFilter.parameterType ===
                                            'integer'
                                            ? 'integer_list'
                                            : 'text_list'
                                          : currentFilter.parameterType.endsWith(
                                                '_list',
                                              )
                                            ? currentFilter.parameterType ===
                                              'integer_list'
                                              ? 'integer'
                                              : 'text'
                                            : currentFilter.parameterType,
                                        confirmed: false,
                                      }
                                    : currentFilter,
                                ),
                              }));
                            }}
                          >
                            {FILTER_OPERATORS.map((operator) => (
                              <option
                                key={operator.value}
                                value={operator.value}
                              >
                                {operator.label}
                              </option>
                            ))}
                          </select>
                        </label>
                        {!parameterless && (
                          <label className="space-y-1 text-xs font-medium">
                            <span>Тип значения</span>
                            <select
                              aria-label={`Тип параметра WHERE ${index + 1}`}
                              className={SELECT_CLASS}
                              value={filter.parameterType}
                              onChange={(event) =>
                                changeDraft((current) => ({
                                  ...current,
                                  filters: current.filters.map(
                                    (currentFilter) =>
                                      currentFilter.id === filter.id
                                        ? {
                                            ...currentFilter,
                                            parameterType: event.target
                                              .value as QueryParameterType,
                                            confirmed: false,
                                          }
                                        : currentFilter,
                                  ),
                                }))
                              }
                            >
                              {parameterTypes.map((type) => (
                                <option key={type.value} value={type.value}>
                                  {type.label}
                                </option>
                              ))}
                            </select>
                          </label>
                        )}
                      </div>
                      <label className="space-y-1 text-xs font-medium">
                        <span>Описание на естественном языке</span>
                        <Textarea
                          aria-label={`Описание WHERE ${index + 1}`}
                          rows={2}
                          maxLength={2000}
                          resize="vertical"
                          value={filter.description}
                          onChange={(event) =>
                            changeDraft((current) => ({
                              ...current,
                              filters: current.filters.map((currentFilter) =>
                                currentFilter.id === filter.id
                                  ? {
                                      ...currentFilter,
                                      description: event.target.value,
                                      confirmed: false,
                                    }
                                  : currentFilter,
                              ),
                            }))
                          }
                        />
                      </label>
                      {!parameterless && (
                        <div className="grid gap-2 md:grid-cols-2">
                          <label className="space-y-1 text-xs font-medium">
                            <span>Имя параметра</span>
                            <Input
                              aria-label={`Имя параметра WHERE ${index + 1}`}
                              maxLength={128}
                              value={filter.parameterName}
                              onChange={(event) =>
                                changeDraft((current) => ({
                                  ...current,
                                  filters: current.filters.map(
                                    (currentFilter) =>
                                      currentFilter.id === filter.id
                                        ? {
                                            ...currentFilter,
                                            parameterName: String(
                                              event.target.value,
                                            ),
                                            confirmed: false,
                                          }
                                        : currentFilter,
                                  ),
                                }))
                              }
                            />
                          </label>
                          <label className="space-y-1 text-xs font-medium">
                            <span>
                              Значение
                              {listOperator
                                ? ' — элементы через запятую или с новой строки'
                                : ''}
                            </span>
                            <Input
                              aria-label={`Значение параметра WHERE ${index + 1}`}
                              value={filter.parameterValue}
                              maxLength={5000}
                              onChange={(event) =>
                                changeDraft((current) => ({
                                  ...current,
                                  filters: current.filters.map(
                                    (currentFilter) =>
                                      currentFilter.id === filter.id
                                        ? {
                                            ...currentFilter,
                                            parameterValue: String(
                                              event.target.value,
                                            ),
                                            confirmed: false,
                                          }
                                        : currentFilter,
                                  ),
                                }))
                              }
                            />
                          </label>
                        </div>
                      )}
                    </article>
                  );
                })}
              </section>

              <section className="space-y-3">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <h3 className="text-sm font-semibold">
                      5. Сортировка и лимит
                    </h3>
                    <p className="mt-1 text-xs text-text-secondary">
                      Стабильный ORDER BY обязателен; лимит — от 1 до 10000.
                    </p>
                  </div>
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    onClick={addOrderItem}
                    disabled={draft.orderBy.length >= draft.select.length}
                  >
                    <Plus className="size-4" />
                    Поле сортировки
                  </Button>
                </div>
                <div className="grid gap-3 lg:grid-cols-[2fr_1fr]">
                  <div className="space-y-2">
                    {draft.orderBy.map((order, index) => (
                      <div
                        key={`${order.selectItemId}-${index}`}
                        className="grid gap-2 sm:grid-cols-[1fr_140px_auto]"
                      >
                        <select
                          aria-label={`Поле ORDER BY ${index + 1}`}
                          className={SELECT_CLASS}
                          value={order.selectItemId}
                          onChange={(event) =>
                            changeDraft((current) => ({
                              ...current,
                              orderBy: current.orderBy.map(
                                (currentOrder, currentIndex) =>
                                  currentIndex === index
                                    ? {
                                        ...currentOrder,
                                        selectItemId: event.target.value,
                                      }
                                    : currentOrder,
                              ),
                            }))
                          }
                        >
                          {draft.select.map((item) => (
                            <option key={item.id} value={item.id}>
                              {item.alias || item.id}
                            </option>
                          ))}
                        </select>
                        <select
                          aria-label={`Направление ORDER BY ${index + 1}`}
                          className={SELECT_CLASS}
                          value={order.direction}
                          onChange={(event) =>
                            changeDraft((current) => ({
                              ...current,
                              orderBy: current.orderBy.map(
                                (currentOrder, currentIndex) =>
                                  currentIndex === index
                                    ? {
                                        ...currentOrder,
                                        direction: event.target.value as
                                          | 'ASC'
                                          | 'DESC',
                                      }
                                    : currentOrder,
                              ),
                            }))
                          }
                        >
                          <option value="ASC">ASC</option>
                          <option value="DESC">DESC</option>
                        </select>
                        <Button
                          type="button"
                          size="icon"
                          variant="ghost"
                          aria-label={`Удалить ORDER BY ${index + 1}`}
                          onClick={() =>
                            changeDraft((current) => ({
                              ...current,
                              orderBy: current.orderBy.filter(
                                (_, currentIndex) => currentIndex !== index,
                              ),
                            }))
                          }
                        >
                          <Trash2 className="size-4" />
                        </Button>
                      </div>
                    ))}
                  </div>
                  <label className="space-y-1 text-xs font-medium">
                    <span>Максимум строк</span>
                    <Input
                      type="number"
                      min={1}
                      max={10000}
                      step={1}
                      aria-label="Максимум строк"
                      value={draft.rowLimit}
                      onChange={(event) =>
                        changeDraft((current) => ({
                          ...current,
                          rowLimit: Number(event.target.value),
                        }))
                      }
                    />
                  </label>
                </div>
              </section>

              <QueryCompilerPanel
                issues={issues}
                result={result}
                error={error}
                compiling={compiling}
                onCompile={compile}
              />
            </>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
