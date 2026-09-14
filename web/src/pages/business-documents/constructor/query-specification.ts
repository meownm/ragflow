import type {
  SqlQueryCompileRequest,
  SqlQueryPlanProposal,
  SqlQueryPlanRequest,
} from '@/pages/business-documents/types';
import {
  buildSchemaSnapshot,
  type SchemaColumn,
  type SchemaTableCandidate,
  type SchemaWorkspaceState,
} from './schema-workspace';

export type QuerySelectKind =
  | 'column'
  | 'sum'
  | 'avg'
  | 'min'
  | 'max'
  | 'count'
  | 'count_distinct'
  | 'date_bucket';
export type QueryDateGrain = 'day' | 'week' | 'month' | 'quarter' | 'year';
export type QueryFilterOperator =
  | 'eq'
  | 'ne'
  | 'gt'
  | 'gte'
  | 'lt'
  | 'lte'
  | 'like'
  | 'ilike'
  | 'in'
  | 'not_in'
  | 'is_null'
  | 'is_not_null';
export type QueryParameterType =
  | 'text'
  | 'integer'
  | 'decimal'
  | 'boolean'
  | 'date'
  | 'datetime'
  | 'text_list'
  | 'integer_list';

export interface QuerySelectDraft {
  id: string;
  columnId: string;
  kind: QuerySelectKind;
  alias: string;
  grain: QueryDateGrain | null;
}

export interface QueryJoinDraft {
  id: string;
  joinType: 'INNER' | 'LEFT';
  entityId: string;
  alias: string;
  leftColumnId: string;
  rightColumnId: string;
  description: string;
  confirmed: boolean;
}

export interface QueryFilterDraft {
  id: string;
  columnId: string;
  operator: QueryFilterOperator;
  parameterName: string;
  parameterType: QueryParameterType;
  parameterValue: string;
  description: string;
  confirmed: boolean;
}

export interface QuerySpecificationDraft {
  baseEntityId: string;
  aliases: Record<string, string>;
  select: QuerySelectDraft[];
  joins: QueryJoinDraft[];
  filters: QueryFilterDraft[];
  orderBy: Array<{ selectItemId: string; direction: 'ASC' | 'DESC' }>;
  rowLimit: number;
}

export interface QueryDraftIssue {
  code: string;
  path: string;
  message: string;
}

export interface SelectedSchemaTable {
  table: SchemaTableCandidate;
  selectedColumnIds: string[];
}

const SAFE_IDENTIFIER = /^[A-Za-z_][A-Za-z0-9_$]*$/;
const PARAMETERLESS_OPERATORS = new Set<QueryFilterOperator>([
  'is_null',
  'is_not_null',
]);
const LIST_OPERATORS = new Set<QueryFilterOperator>(['in', 'not_in']);
const INTEGER_VALUE = /^[+-]?\d+$/;
const DECIMAL_VALUE = /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/;
const ISO_DATE_VALUE = /^\d{4}-\d{2}-\d{2}$/;

function safeAlias(value: string, fallback: string) {
  const normalized = value
    .trim()
    .replace(/[^A-Za-z0-9_$]+/g, '_')
    .replace(/^[^A-Za-z_]+/, '')
    .slice(0, 128);
  return SAFE_IDENTIFIER.test(normalized) ? normalized : fallback;
}

function uniqueAlias(candidate: string, used: Set<string>) {
  let value = candidate;
  let suffix = 2;
  while (used.has(value.toLocaleLowerCase())) {
    value = `${candidate}_${suffix}`;
    suffix += 1;
  }
  used.add(value.toLocaleLowerCase());
  return value;
}

export function selectedSchemaTables(
  workspace: SchemaWorkspaceState,
): SelectedSchemaTable[] {
  const byId = new Map<string, SelectedSchemaTable>();
  workspace.resolutions.forEach((resolution) => {
    const table = resolution.candidates.find(
      (candidate) => candidate.id === resolution.selectedEntityId,
    );
    if (!table) return;
    const existing = byId.get(table.id);
    const selectedColumnIds = new Set(existing?.selectedColumnIds || []);
    resolution.selectedColumnIds.forEach((id) => selectedColumnIds.add(id));
    byId.set(table.id, { table, selectedColumnIds: [...selectedColumnIds] });
  });
  return [...byId.values()];
}

function matchingJoinColumns(
  earlier: SelectedSchemaTable[],
  target: SelectedSchemaTable,
) {
  const targetByName = new Map(
    target.table.columns.map((column) => [
      column.name.toLocaleLowerCase(),
      column,
    ]),
  );
  const matches: Array<[SchemaColumn, SchemaColumn]> = [];
  earlier.forEach(({ table }) =>
    table.columns.forEach((column) => {
      const other = targetByName.get(column.name.toLocaleLowerCase());
      if (other) matches.push([column, other]);
    }),
  );
  const idMatches = matches.filter(([left]) => left.name.endsWith('_id'));
  return (idMatches.length ? idMatches : matches)[0] || null;
}

function createJoinDrafts(
  tables: SelectedSchemaTable[],
  aliases: Record<string, string>,
) {
  return tables.slice(1).map((target, index): QueryJoinDraft => {
    const match = matchingJoinColumns(tables.slice(0, index + 1), target);
    return {
      id: `join-${index + 1}`,
      joinType: 'INNER',
      entityId: target.table.id,
      alias: aliases[target.table.id],
      leftColumnId: match?.[0].id || '',
      rightColumnId: match?.[1].id || '',
      description: match
        ? `Соединить ${match[0].fqn || match[0].name} и ${match[1].fqn || match[1].name}`
        : `Определить связь с ${target.table.fqn}`,
      confirmed: false,
    };
  });
}

export function createQuerySpecificationDraft(
  workspace: SchemaWorkspaceState,
): QuerySpecificationDraft {
  const tables = selectedSchemaTables(workspace);
  const aliases: Record<string, string> = {};
  tables.forEach(({ table }, index) => {
    aliases[table.id] = `t${index + 1}`;
  });

  const usedAliases = new Set<string>();
  const select: QuerySelectDraft[] = [];
  tables.forEach(({ table, selectedColumnIds }) => {
    selectedColumnIds.forEach((columnId) => {
      const column = table.columns.find((item) => item.id === columnId);
      if (!column) return;
      select.push({
        id: `select-${select.length + 1}`,
        columnId,
        kind: 'column',
        alias: uniqueAlias(
          safeAlias(column.name, `field_${select.length + 1}`),
          usedAliases,
        ),
        grain: null,
      });
    });
  });

  const joins = createJoinDrafts(tables, aliases);

  return {
    baseEntityId: tables[0]?.table.id || '',
    aliases,
    select,
    joins,
    filters: [],
    orderBy: select[0]
      ? [{ selectItemId: select[0].id, direction: 'ASC' }]
      : [],
    rowLimit: 1000,
  };
}

export function changeQueryBaseTable(
  workspace: SchemaWorkspaceState,
  draft: QuerySpecificationDraft,
  baseEntityId: string,
): QuerySpecificationDraft {
  const tables = selectedSchemaTables(workspace);
  if (!tables.some(({ table }) => table.id === baseEntityId)) return draft;
  const ordered = [
    ...tables.filter(({ table }) => table.id === baseEntityId),
    ...tables.filter(({ table }) => table.id !== baseEntityId),
  ];
  const aliases = Object.fromEntries(
    ordered.map(({ table }, index) => [
      table.id,
      draft.aliases[table.id] || `t${index + 1}`,
    ]),
  );
  return {
    ...draft,
    baseEntityId,
    aliases,
    joins: createJoinDrafts(ordered, aliases),
  };
}

export function createQueryFilter(index: number): QueryFilterDraft {
  return {
    id: `filter-${index}`,
    columnId: '',
    operator: 'eq',
    parameterName: `value_${index}`,
    parameterType: 'text',
    parameterValue: '',
    description: '',
    confirmed: false,
  };
}

export function validateQuerySpecificationDraft(
  workspace: SchemaWorkspaceState,
  draft: QuerySpecificationDraft,
): QueryDraftIssue[] {
  const issues: QueryDraftIssue[] = [];
  const snapshot = buildSchemaSnapshot(
    workspace.resolutions,
    workspace.requirements,
  );
  const tables = selectedSchemaTables(workspace);
  const columnIds = new Set(
    tables.flatMap(({ table }) => table.columns.map((column) => column.id)),
  );
  if (snapshot.status !== 'READY') {
    issues.push({
      code: 'SCHEMA_NOT_READY',
      path: 'schema_snapshot',
      message: 'Сначала завершите сопоставление таблиц и полей.',
    });
  }
  if (!tables.some(({ table }) => table.id === draft.baseEntityId)) {
    issues.push({
      code: 'BASE_TABLE_REQUIRED',
      path: 'from',
      message: 'Выберите базовую таблицу.',
    });
  }
  const draftAliases = Object.entries(draft.aliases);
  if (
    draftAliases.length !== tables.length ||
    draftAliases.some(
      ([entityId, alias]) =>
        !tables.some(({ table }) => table.id === entityId) ||
        !SAFE_IDENTIFIER.test(alias),
    ) ||
    new Set(draftAliases.map(([, alias]) => alias.toLocaleLowerCase())).size !==
      draftAliases.length
  ) {
    issues.push({
      code: 'INVALID_TABLE_ALIASES',
      path: 'aliases',
      message: 'Псевдонимы таблиц должны быть безопасными и уникальными.',
    });
  }
  if (!draft.select.length) {
    issues.push({
      code: 'SELECT_REQUIRED',
      path: 'select',
      message: 'Добавьте хотя бы одно поле вывода.',
    });
  }
  const selectIds = new Set<string>();
  const aliases = new Set<string>();
  draft.select.forEach((item, index) => {
    if (
      !columnIds.has(item.columnId) ||
      !SAFE_IDENTIFIER.test(item.alias) ||
      selectIds.has(item.id) ||
      aliases.has(item.alias.toLocaleLowerCase()) ||
      (item.kind === 'date_bucket' ? !item.grain : item.grain !== null)
    ) {
      issues.push({
        code: 'INVALID_SELECT_ITEM',
        path: `select[${index}]`,
        message: `Проверьте поле, преобразование и уникальный alias пункта SELECT ${index + 1}.`,
      });
    }
    selectIds.add(item.id);
    aliases.add(item.alias.toLocaleLowerCase());
  });
  const boundEntities = new Set([draft.baseEntityId]);
  const joinedEntities = new Set<string>();
  draft.joins.forEach((join, index) => {
    const left = tables.find(({ table }) =>
      table.columns.some((column) => column.id === join.leftColumnId),
    );
    const right = tables.find(({ table }) =>
      table.columns.some((column) => column.id === join.rightColumnId),
    );
    if (
      !columnIds.has(join.leftColumnId) ||
      !columnIds.has(join.rightColumnId) ||
      !tables.some(({ table }) => table.id === join.entityId) ||
      join.entityId === draft.baseEntityId ||
      joinedEntities.has(join.entityId) ||
      !boundEntities.has(left?.table.id || '') ||
      right?.table.id !== join.entityId ||
      draft.aliases[join.entityId] !== join.alias ||
      !join.description.trim() ||
      !join.confirmed
    ) {
      issues.push({
        code: 'JOIN_DECISION_REQUIRED',
        path: `joins[${index}]`,
        message: `Заполните и подтвердите соединение ${index + 1}.`,
      });
    }
    joinedEntities.add(join.entityId);
    boundEntities.add(join.entityId);
  });
  if (
    tables.some(
      ({ table }) =>
        table.id !== draft.baseEntityId && !joinedEntities.has(table.id),
    )
  ) {
    issues.push({
      code: 'TABLE_NOT_JOINED',
      path: 'joins',
      message: 'Каждая дополнительная таблица должна иметь одно соединение.',
    });
  }
  const parameterNames = new Set<string>();
  draft.filters.forEach((filter, index) => {
    const needsParameter = !PARAMETERLESS_OPERATORS.has(filter.operator);
    const valueIsValid =
      !needsParameter ||
      parameterValueIsValid(filter.parameterType, filter.parameterValue);
    if (
      !columnIds.has(filter.columnId) ||
      !filter.description.trim() ||
      !filter.confirmed ||
      (needsParameter &&
        (!SAFE_IDENTIFIER.test(filter.parameterName) ||
          !valueIsValid ||
          parameterNames.has(filter.parameterName))) ||
      LIST_OPERATORS.has(filter.operator) !==
        filter.parameterType.endsWith('_list')
    ) {
      issues.push({
        code: 'FILTER_DECISION_REQUIRED',
        path: `filters[${index}]`,
        message: `Заполните и подтвердите условие WHERE ${index + 1}.`,
      });
    }
    if (needsParameter) parameterNames.add(filter.parameterName);
  });
  if (
    !draft.orderBy.length ||
    draft.orderBy.some((item) => !selectIds.has(item.selectItemId)) ||
    new Set(draft.orderBy.map((item) => item.selectItemId)).size !==
      draft.orderBy.length
  ) {
    issues.push({
      code: 'ORDER_BY_REQUIRED',
      path: 'order_by',
      message: 'Выберите хотя бы одно поле сортировки.',
    });
  }
  if (
    !Number.isInteger(draft.rowLimit) ||
    draft.rowLimit < 1 ||
    draft.rowLimit > 10_000
  ) {
    issues.push({
      code: 'LIMIT_REQUIRED',
      path: 'row_limit',
      message: 'Лимит должен быть целым числом от 1 до 10000.',
    });
  }
  return issues;
}

function parameterValueIsValid(type: QueryParameterType, raw: string) {
  const value = raw.trim();
  if (!value || value.length > 5_000) return false;
  if (type === 'integer') {
    return INTEGER_VALUE.test(value) && Number.isSafeInteger(Number(value));
  }
  if (type === 'decimal') return DECIMAL_VALUE.test(value);
  if (type === 'boolean') return value === 'true' || value === 'false';
  if (type === 'date') {
    if (!ISO_DATE_VALUE.test(value)) return false;
    const parsed = new Date(`${value}T00:00:00Z`);
    return (
      !Number.isNaN(parsed.valueOf()) && parsed.toISOString().startsWith(value)
    );
  }
  if (type === 'datetime') return !Number.isNaN(Date.parse(value));
  if (type === 'text_list' || type === 'integer_list') {
    const items = value
      .split(/[\n,]+/)
      .map((item) => item.trim())
      .filter(Boolean);
    return (
      items.length > 0 &&
      items.length <= 100 &&
      (type === 'text_list' ||
        items.every(
          (item) =>
            INTEGER_VALUE.test(item) && Number.isSafeInteger(Number(item)),
        ))
    );
  }
  return true;
}

function parseParameterValue(filter: QueryFilterDraft): unknown {
  const value = filter.parameterValue.trim();
  if (filter.parameterType === 'integer') return Number(value);
  if (filter.parameterType === 'decimal') return value;
  if (filter.parameterType === 'boolean') return value === 'true';
  if (filter.parameterType === 'integer_list') {
    return value.split(/[\n,]+/).map((item) => Number(item.trim()));
  }
  if (filter.parameterType === 'text_list') {
    return value
      .split(/[\n,]+/)
      .map((item) => item.trim())
      .filter(Boolean);
  }
  return value;
}

export function buildQueryCompileRequest(
  workspace: SchemaWorkspaceState,
  draft: QuerySpecificationDraft,
): SqlQueryCompileRequest {
  const issues = validateQuerySpecificationDraft(workspace, draft);
  if (issues.length) throw new Error(issues[0].message);
  const tables = selectedSchemaTables(workspace);
  const snapshot = buildSchemaSnapshot(
    workspace.resolutions,
    workspace.requirements,
  );
  const parameters: SqlQueryCompileRequest['specification']['parameters'] =
    draft.filters
      .filter((filter) => !PARAMETERLESS_OPERATORS.has(filter.operator))
      .map((filter) => ({
        name: filter.parameterName,
        type: filter.parameterType,
        value: parseParameterValue(filter),
      }));
  parameters.push({
    name: 'row_limit',
    type: 'integer',
    value: draft.rowLimit,
  });
  return {
    schema_version: '1',
    schema_snapshot: snapshot as unknown as Record<string, unknown>,
    accepted_requirements: workspace.requirements.trim(),
    accepted_schema: tables.map(({ table }) => ({
      entity_id: table.id,
      version: table.version!,
      schema_fingerprint: table.schemaFingerprint!,
    })),
    specification: {
      dialect: 'postgres',
      from: {
        entity_id: draft.baseEntityId,
        alias: draft.aliases[draft.baseEntityId],
      },
      select: draft.select.map((item) => ({
        id: item.id,
        kind: item.kind,
        column_id: item.columnId,
        alias: item.alias,
        grain: item.kind === 'date_bucket' ? item.grain : null,
      })),
      joins: draft.joins.map((join) => ({
        id: join.id,
        join_type: join.joinType,
        entity_id: join.entityId,
        alias: join.alias,
        left_column_id: join.leftColumnId,
        right_column_id: join.rightColumnId,
        description: join.description.trim(),
        decision: join.confirmed ? 'user' : null,
        confirmed: join.confirmed,
      })),
      filters: draft.filters.map((filter) => ({
        id: filter.id,
        column_id: filter.columnId,
        operator: filter.operator,
        parameter: PARAMETERLESS_OPERATORS.has(filter.operator)
          ? null
          : filter.parameterName,
        description: filter.description.trim(),
        decision: filter.confirmed ? 'user' : null,
        confirmed: filter.confirmed,
      })),
      order_by: draft.orderBy.map((item) => ({
        select_item_id: item.selectItemId,
        direction: item.direction,
      })),
      parameters,
      limit_parameter: 'row_limit',
    },
  };
}

export function buildQueryPlanRequest(
  workspace: SchemaWorkspaceState,
  locale: 'ru' | 'en' = 'ru',
): SqlQueryPlanRequest {
  const snapshot = buildSchemaSnapshot(
    workspace.resolutions,
    workspace.requirements,
  );
  const tables = selectedSchemaTables(workspace);
  if (snapshot.status !== 'READY' || !tables.length) {
    throw new Error('Сначала завершите сопоставление таблиц и полей.');
  }
  return {
    schema_version: '1',
    schema_snapshot: snapshot as unknown as Record<string, unknown>,
    accepted_requirements: workspace.requirements.trim(),
    accepted_schema: tables.map(({ table }) => ({
      entity_id: table.id,
      version: table.version!,
      schema_fingerprint: table.schemaFingerprint!,
    })),
    locale,
  };
}

export function applyQueryPlanProposal(
  proposal: SqlQueryPlanProposal,
): QuerySpecificationDraft {
  return {
    baseEntityId: proposal.base_entity_id,
    aliases: { ...proposal.aliases },
    select: proposal.select.map((item) => ({
      id: item.id,
      columnId: item.column_id,
      kind: item.kind,
      alias: item.alias,
      grain: item.grain,
    })),
    joins: proposal.joins.map((item) => ({
      id: item.id,
      joinType: item.join_type,
      entityId: item.entity_id,
      alias: item.alias,
      leftColumnId: item.left_column_id,
      rightColumnId: item.right_column_id,
      description: item.description,
      confirmed: false,
    })),
    filters: proposal.filters.map((item) => ({
      id: item.id,
      columnId: item.column_id,
      operator: item.operator,
      parameterName: item.parameter_name,
      parameterType: item.parameter_type,
      parameterValue: item.parameter_value,
      description: item.description,
      confirmed: false,
    })),
    orderBy: proposal.order_by.map((item) => ({
      selectItemId: item.select_item_id,
      direction: item.direction,
    })),
    rowLimit: proposal.row_limit,
  };
}
