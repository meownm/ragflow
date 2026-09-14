import {
  applyQueryPlanProposal,
  buildQueryCompileRequest,
  buildQueryPlanRequest,
  createQueryFilter,
  createQuerySpecificationDraft,
  selectedSchemaTables,
  validateQuerySpecificationDraft,
} from './query-specification';
import type { SchemaWorkspaceState } from './schema-workspace';

function table(
  id: string,
  fqn: string,
  fields: string[],
  selectedColumnIds = fields.map((field) => `${fqn}.${field}`),
) {
  const technicalName = fqn.split('.').at(-1)!;
  return {
    term: technicalName,
    status: 'confirmed' as const,
    decision: 'user' as const,
    selectedEntityId: id,
    selectedColumnIds,
    freshness: {
      snapshot_at: '2026-09-14T08:00:00+00:00',
      checked_at: '2026-09-14T08:05:00+00:00',
      stale: false,
      threshold_hours: 168,
    },
    retrieval: 'fixture',
    sources: [{ label: 'OpenMetadata' }],
    warnings: [],
    interpretation: null,
    candidates: [
      {
        id,
        name: technicalName,
        displayName: null,
        technicalName,
        fqn,
        description: fqn,
        version: 3,
        updatedAt: '2026-09-14T08:00:00+00:00',
        service: 'warehouse',
        schema: fqn.split('.')[0],
        database: 'analytics',
        owners: ['DWH'],
        domains: [],
        tags: [],
        glossaryTerms: [],
        url: '',
        matchedBy: ['fixture'],
        columns: fields.map((name) => ({
          id: `${fqn}.${name}`,
          name,
          fqn: `${fqn}.${name}`,
          dataType: 'TEXT',
          description: name,
          constraint: '',
          glossaryTerms: [],
        })),
        columnCount: fields.length,
        columnsTruncated: false,
        tableConstraints: [],
        schemaStatus: 'loaded' as const,
        schemaFingerprint: `sha256:${id}-v3`,
        schemaError: null,
      },
    ],
  };
}

function workspace(twoTables = false): SchemaWorkspaceState {
  return {
    input: twoTables ? 'Заказ\nКлиент' : 'Заказ',
    requirements: 'Вывести заказы.',
    resolutions: [
      table('orders', 'dwh.order_fact', ['order_id', 'customer_id']),
      ...(twoTables
        ? [
            table(
              'customers',
              'dwh.customer_dim',
              ['customer_id'],
              ['dwh.customer_dim.customer_id'],
            ),
          ]
        : []),
    ],
  };
}

describe('query specification model', () => {
  it('creates selected fields and blocks an unconfirmed join suggestion', () => {
    const source = workspace(true);
    const draft = createQuerySpecificationDraft(source);

    expect(selectedSchemaTables(source).map(({ table }) => table.id)).toEqual([
      'orders',
      'customers',
    ]);
    expect(draft.select.map((item) => item.columnId)).toEqual([
      'dwh.order_fact.order_id',
      'dwh.order_fact.customer_id',
      'dwh.customer_dim.customer_id',
    ]);
    expect(draft.joins[0]).toMatchObject({
      leftColumnId: 'dwh.order_fact.customer_id',
      rightColumnId: 'dwh.customer_dim.customer_id',
      confirmed: false,
    });
    expect(validateQuerySpecificationDraft(source, draft)).toContainEqual(
      expect.objectContaining({ code: 'JOIN_DECISION_REQUIRED' }),
    );
  });

  it('builds a closed request with separate filter and limit parameters', () => {
    const source = workspace();
    const draft = createQuerySpecificationDraft(source);
    const filter = createQueryFilter(1);
    Object.assign(filter, {
      columnId: 'dwh.order_fact.order_id',
      operator: 'gte',
      parameterName: 'order_from',
      parameterType: 'integer',
      parameterValue: '100',
      description: 'ID заказа не меньше границы',
      confirmed: true,
    });
    draft.filters.push(filter);

    const request = buildQueryCompileRequest(source, draft);

    expect(request.accepted_schema).toEqual([
      {
        entity_id: 'orders',
        version: 3,
        schema_fingerprint: 'sha256:orders-v3',
      },
    ]);
    expect(request.specification.filters[0]).toMatchObject({
      parameter: 'order_from',
      decision: 'user',
      confirmed: true,
    });
    expect(request.specification.parameters).toEqual([
      { name: 'order_from', type: 'integer', value: 100 },
      { name: 'row_limit', type: 'integer', value: 1000 },
    ]);
  });

  it('fails closed when the schema snapshot is stale', () => {
    const source = workspace();
    source.resolutions[0].freshness!.stale = true;
    const draft = createQuerySpecificationDraft(source);

    expect(validateQuerySpecificationDraft(source, draft)).toContainEqual(
      expect.objectContaining({ code: 'SCHEMA_NOT_READY' }),
    );
    expect(() => buildQueryCompileRequest(source, draft)).toThrow(
      'Сначала завершите сопоставление',
    );
  });

  it('rejects malformed typed values before building a wire request', () => {
    const source = workspace();
    const draft = createQuerySpecificationDraft(source);
    const filter = createQueryFilter(1);
    Object.assign(filter, {
      columnId: 'dwh.order_fact.order_id',
      parameterType: 'integer',
      parameterValue: '12abc',
      description: 'Некорректная целочисленная граница',
      confirmed: true,
    });
    draft.filters.push(filter);

    expect(validateQuerySpecificationDraft(source, draft)).toContainEqual(
      expect.objectContaining({ code: 'FILTER_DECISION_REQUIRED' }),
    );
    expect(() => buildQueryCompileRequest(source, draft)).toThrow(
      'Заполните и подтвердите условие WHERE 1',
    );
  });

  it('binds an LLM proposal to the accepted snapshot and resets all approvals', () => {
    const source = workspace(true);
    const request = buildQueryPlanRequest(source);
    const draft = applyQueryPlanProposal({
      base_entity_id: 'orders',
      aliases: { orders: 't1', customers: 't2' },
      select: [
        {
          id: 'select-1',
          kind: 'column',
          column_id: 'dwh.order_fact.order_id',
          alias: 'order_id',
          grain: null,
        },
      ],
      joins: [
        {
          id: 'join-1',
          join_type: 'INNER',
          entity_id: 'customers',
          alias: 't2',
          left_column_id: 'dwh.order_fact.customer_id',
          right_column_id: 'dwh.customer_dim.customer_id',
          description: 'Заказ принадлежит клиенту.',
          confirmed: true,
        },
      ],
      filters: [
        {
          id: 'filter-1',
          column_id: 'dwh.order_fact.order_id',
          operator: 'gte',
          parameter_name: 'minimum_id',
          parameter_type: 'integer',
          parameter_value: '100',
          description: 'ID заказа не меньше 100.',
          confirmed: true,
        },
      ],
      order_by: [{ select_item_id: 'select-1', direction: 'ASC' }],
      row_limit: 1000,
    });

    expect(request.accepted_schema).toHaveLength(2);
    expect(request.locale).toBe('ru');
    expect(draft.joins[0].confirmed).toBe(false);
    expect(draft.filters[0].confirmed).toBe(false);
    expect(validateQuerySpecificationDraft(source, draft)).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ code: 'JOIN_DECISION_REQUIRED' }),
        expect.objectContaining({ code: 'FILTER_DECISION_REQUIRED' }),
      ]),
    );
  });
});
