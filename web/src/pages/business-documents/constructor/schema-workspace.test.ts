import type {
  CatalogAnswer,
  CatalogEntity,
  Freshness,
} from '@/pages/openmetadata/types';
import {
  applySchemaCandidateDetails,
  buildSchemaSnapshot,
  chooseSchemaCandidate,
  createSchemaResolution,
  MAX_SCHEMA_COLUMNS_PER_TABLE,
  parseSchemaTerms,
  toggleSchemaColumn,
} from './schema-workspace';

const freshness: Freshness = {
  snapshot_at: '2026-09-14T08:00:00+00:00',
  checked_at: '2026-09-14T08:05:00+00:00',
  stale: false,
  threshold_hours: 168,
};

function table(
  id: string,
  fqn: string,
  columns: Array<[string, string]> = [['id', 'BIGINT']],
  schemaLoaded = false,
): CatalogEntity {
  const technicalName = fqn.split('.').at(-1) || fqn;
  return {
    id,
    type: 'table',
    name: technicalName,
    display_name: null,
    technical_name: technicalName,
    fqn,
    description: `Описание ${fqn}`,
    version: 3,
    updated_at: freshness.snapshot_at,
    service: 'warehouse',
    schema: 'dwh',
    database: 'analytics',
    owners: ['DWH'],
    domains: ['Sales'],
    tags: ['gold'],
    glossary_terms: ['Заказ'],
    columns: columns.map(([name]) => name),
    column_details: columns.map(([name, dataType]) => ({
      name,
      fqn: `${fqn}.${name}`,
      data_type: dataType,
      description: `Поле ${name}`,
      constraint: name === 'id' ? 'PRIMARY_KEY' : '',
      glossary_terms: [],
    })),
    table_constraints: [
      {
        constraint_type: 'PRIMARY_KEY',
        columns: ['id'],
        referred_columns: [],
      },
    ],
    column_count: columns.length,
    described_column_count: columns.length,
    url: `https://metadata.example/table/${fqn}`,
    matched_by: ['catalog_projection', 'ragflow_dataset'],
    schema_loaded: schemaLoaded,
    schema_fingerprint: schemaLoaded ? `sha256:${id}` : null,
  };
}

function answer(entities: CatalogEntity[], currentFreshness = freshness) {
  return {
    agent: 'catalog_copilot',
    intent: 'discovery',
    question: 'заказ',
    answer: 'Найдены таблицы',
    freshness: currentFreshness,
    warnings: [],
    entities,
    retrieval: 'omd_dataset_hybrid_rrf',
    sources: [
      { label: 'OpenMetadata', url: 'https://metadata.example' },
      { label: 'RAGFlow Dataset', dataset_id: 'dataset-1' },
    ],
  } satisfies CatalogAnswer;
}

describe('schema workspace', () => {
  it('normalizes a bounded list without silently truncating it', () => {
    expect(parseSchemaTerms(' - Заказ\nКлиент\nзаказ ')).toEqual({
      terms: ['Заказ', 'Клиент'],
      error: null,
    });
    expect(parseSchemaTerms('')).toEqual({
      terms: [],
      error: 'Укажите хотя бы одну бизнес-сущность.',
    });
    expect(
      parseSchemaTerms(Array.from({ length: 9 }, (_, i) => `e${i}`).join('\n'))
        .error,
    ).toContain('не более 8');
  });

  it('never accepts the top fuzzy candidate when several tables match', () => {
    const resolution = createSchemaResolution(
      'заказ',
      answer([
        table('orders', 'dwh.order_fact'),
        table('events', 'dwh.order_event_fact'),
      ]),
    );

    expect(resolution.status).toBe('needs_clarification');
    expect(resolution.selectedEntityId).toBeNull();
    expect(resolution.decision).toBeNull();
    expect(buildSchemaSnapshot([resolution]).status).toBe(
      'NEEDS_CLARIFICATION',
    );
  });

  it('accepts one exact technical name but still records the evidence', () => {
    const resolution = createSchemaResolution(
      'order_fact',
      answer([
        table('orders', 'dwh.order_fact'),
        table('events', 'dwh.order_event_fact'),
      ]),
    );

    expect(resolution.status).toBe('confirmed');
    expect(resolution.selectedEntityId).toBe('orders');
    expect(resolution.decision).toBe('automatic_exact');
    expect(resolution.sources).toEqual([
      { label: 'OpenMetadata', url: 'https://metadata.example' },
      { label: 'RAGFlow Dataset', datasetId: 'dataset-1' },
    ]);
  });

  it('retains prior field choices until the refreshed full schema verifies them', () => {
    const initial = createSchemaResolution(
      'order_fact',
      answer([
        table('orders', 'dwh.order_fact', [
          ['order_id', 'BIGINT'],
          ['paid_amount_rub', 'NUMERIC(18,2)'],
        ]),
      ]),
    );
    const detailed = applySchemaCandidateDetails(
      initial,
      table(
        'orders',
        'dwh.order_fact',
        [
          ['order_id', 'BIGINT'],
          ['paid_amount_rub', 'NUMERIC(18,2)'],
        ],
        true,
      ),
      {
        freshness,
        retrieval: 'catalog_projection',
        sources: [],
        warnings: [],
      },
    );
    const selected = toggleSchemaColumn(
      detailed,
      'dwh.order_fact.paid_amount_rub',
    );

    const refreshedSummary = createSchemaResolution(
      'order_fact',
      answer([table('orders', 'dwh.order_fact', [['order_id', 'BIGINT']])]),
      selected,
    );
    expect(refreshedSummary.selectedColumnIds).toEqual([
      'dwh.order_fact.paid_amount_rub',
    ]);

    const refreshedDetails = applySchemaCandidateDetails(
      refreshedSummary,
      table(
        'orders',
        'dwh.order_fact',
        [
          ['order_id', 'BIGINT'],
          ['paid_amount_rub', 'NUMERIC(18,2)'],
        ],
        true,
      ),
      {
        freshness,
        retrieval: 'catalog_projection',
        sources: [],
        warnings: [],
      },
    );
    expect(refreshedDetails.selectedColumnIds).toEqual([
      'dwh.order_fact.paid_amount_rub',
    ]);
  });

  it('exports the explicit table and field decision as a closed snapshot', () => {
    const ambiguous = createSchemaResolution(
      'заказ',
      answer([
        table('orders', 'dwh.order_fact', [
          ['order_id', 'BIGINT'],
          ['status_id', 'BIGINT'],
          ['paid_amount_rub', 'NUMERIC(18,2)'],
        ]),
        table('events', 'dwh.order_event_fact'),
      ]),
    );
    const chosen = chooseSchemaCandidate(ambiguous, 'orders');
    expect(buildSchemaSnapshot([chosen]).status).toBe('NEEDS_CLARIFICATION');
    const detailed = applySchemaCandidateDetails(
      chosen,
      table(
        'orders',
        'dwh.order_fact',
        [
          ['order_id', 'BIGINT'],
          ['status_id', 'BIGINT'],
          ['paid_amount_rub', 'NUMERIC(18,2)'],
        ],
        true,
      ),
      {
        freshness,
        retrieval: 'omd_dataset_hybrid_rrf',
        sources: [
          { label: 'OpenMetadata', url: 'https://metadata.example' },
          { label: 'RAGFlow Dataset', dataset_id: 'dataset-1' },
        ],
        warnings: [],
      },
    );
    expect(buildSchemaSnapshot([detailed]).status).toBe('NEEDS_CLARIFICATION');
    const withField = toggleSchemaColumn(
      detailed,
      'dwh.order_fact.paid_amount_rub',
    );
    expect(buildSchemaSnapshot([withField]).status).toBe('NEEDS_CLARIFICATION');
    const snapshot = buildSchemaSnapshot(
      [withField],
      'Вывести оплаченные заказы.',
    );

    expect(snapshot.status).toBe('READY');
    expect(snapshot.original_requirements).toBe('Вывести оплаченные заказы.');
    expect(snapshot.source).toEqual({
      type: 'openmetadata',
      catalog_snapshot_at: freshness.snapshot_at,
      checked_at: freshness.checked_at,
      stale: false,
      retrieval: ['omd_dataset_hybrid_rrf'],
      references: [
        { label: 'OpenMetadata', url: 'https://metadata.example' },
        { label: 'RAGFlow Dataset', dataset_id: 'dataset-1' },
      ],
    });
    expect(snapshot.requirements[0].state).toBe('CONFIRMED');
    expect(snapshot.requirements[0].decision).toBe('USER');
    expect(
      snapshot.requirements[0].selected_table?.columns.find(
        (column) => column.name === 'paid_amount_rub',
      )?.selected,
    ).toBe(true);
  });

  it('marks an otherwise complete snapshot stale instead of ready', () => {
    const summary = createSchemaResolution(
      'order_fact',
      answer([table('orders', 'dwh.order_fact')], {
        ...freshness,
        stale: true,
      }),
    );
    const detailed = applySchemaCandidateDetails(
      summary,
      table('orders', 'dwh.order_fact', [['id', 'BIGINT']], true),
      {
        freshness: { ...freshness, stale: true },
        retrieval: 'omd_dataset_hybrid_rrf',
        sources: [],
        warnings: [],
      },
    );
    const resolution = toggleSchemaColumn(detailed, 'dwh.order_fact.id');

    expect(buildSchemaSnapshot([resolution], 'Вывести заказы.').status).toBe(
      'STALE',
    );
  });

  it('fails closed when freshness dates or schema version are unknown', () => {
    const summary = createSchemaResolution(
      'order_fact',
      answer([table('orders', 'dwh.order_fact')]),
    );
    const missingFreshness = applySchemaCandidateDetails(
      summary,
      table('orders', 'dwh.order_fact', [['id', 'BIGINT']], true),
      {
        freshness: { stale: false, threshold_hours: 168 },
        retrieval: 'catalog_projection',
        sources: [],
        warnings: [],
      },
    );
    expect(
      buildSchemaSnapshot(
        [toggleSchemaColumn(missingFreshness, 'dwh.order_fact.id')],
        'Вывести заказы.',
      ).status,
    ).toBe('STALE');

    const entityWithoutVersion = {
      ...table('orders', 'dwh.order_fact', [['id', 'BIGINT']], true),
      version: undefined,
    };
    const missingVersion = applySchemaCandidateDetails(
      summary,
      entityWithoutVersion,
      {
        freshness,
        retrieval: 'catalog_projection',
        sources: [],
        warnings: [],
      },
    );
    expect(
      buildSchemaSnapshot(
        [toggleSchemaColumn(missingVersion, 'dwh.order_fact.id')],
        'Вывести заказы.',
      ).status,
    ).toBe('NEEDS_CLARIFICATION');
  });

  it('fails closed when a wide table schema is truncated', () => {
    const columns = Array.from(
      { length: MAX_SCHEMA_COLUMNS_PER_TABLE + 1 },
      (_, index) => [`field_${index}`, 'TEXT'] as [string, string],
    );
    const summary = createSchemaResolution(
      'wide_fact',
      answer([table('wide', 'dwh.wide_fact', columns)]),
    );
    const resolution = applySchemaCandidateDetails(
      summary,
      table('wide', 'dwh.wide_fact', columns, true),
      {
        freshness,
        retrieval: 'omd_dataset_hybrid_rrf',
        sources: [],
        warnings: [],
      },
    );
    const selected = resolution.candidates[0];

    expect(resolution.status).toBe('confirmed');
    expect(selected.columns).toHaveLength(MAX_SCHEMA_COLUMNS_PER_TABLE);
    expect(selected.columnCount).toBe(MAX_SCHEMA_COLUMNS_PER_TABLE + 1);
    expect(selected.columnsTruncated).toBe(true);
    expect(buildSchemaSnapshot([resolution]).status).toBe(
      'NEEDS_CLARIFICATION',
    );
  });
});
