import {
  loadBusinessDocumentSqlSchemaEntities,
  resolveBusinessDocumentSqlSchema,
} from '@/services/business-document-service';
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { SchemaWorkspaceDialog } from './schema-workspace-dialog';
import { createSchemaWorkspaceStorageKey } from './schema-workspace-storage';

jest.mock('@/services/business-document-service', () => ({
  loadBusinessDocumentSqlSchemaEntities: jest.fn(),
  resolveBusinessDocumentSqlSchema: jest.fn(),
}));

const mockedResolveSqlSchema = jest.mocked(resolveBusinessDocumentSqlSchema);
const mockedLoadSqlSchemaEntities = jest.mocked(
  loadBusinessDocumentSqlSchemaEntities,
);
const scope = { userId: 'user-1', tenantId: 'tenant-1' };

function table(id: string, fqn: string, columns: Array<[string, string]>) {
  const technicalName = fqn.split('.').at(-1) || fqn;
  return {
    id,
    type: 'table',
    name: technicalName,
    display_name: null,
    technical_name: technicalName,
    fqn,
    description: `Описание ${fqn}`,
    version: 4,
    updated_at: '2026-09-14T08:00:00+00:00',
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
      constraint: '',
      glossary_terms: [],
    })),
    table_constraints: [
      {
        constraint_type: 'PRIMARY_KEY',
        columns: [columns[0][0]],
        referred_columns: [],
      },
    ],
    column_count: columns.length,
    described_column_count: columns.length,
    url: `https://metadata.example/table/${fqn}`,
    matched_by: ['catalog_projection', 'ragflow_dataset'],
  };
}

function catalogAnswer() {
  return {
    agent: 'catalog_copilot',
    intent: 'discovery',
    question: 'заказ',
    answer: 'Найдены две таблицы-кандидата.',
    needs_clarification: true,
    clarification: 'Выберите физическую таблицу.',
    freshness: {
      snapshot_at: '2026-09-14T08:00:00+00:00',
      checked_at: '2026-09-14T08:05:00+00:00',
      stale: false,
      threshold_hours: 168,
    },
    retrieval: 'omd_dataset_hybrid_rrf',
    sources: [
      { label: 'OpenMetadata', url: 'https://metadata.example' },
      { label: 'RAGFlow Dataset', dataset_id: 'dataset-1' },
    ],
    warnings: [],
    entities: [
      table('orders', 'dwh.order_fact', [
        ['order_id', 'BIGINT'],
        ['status_id', 'BIGINT'],
        ['paid_amount_rub', 'NUMERIC(18,2)'],
      ]),
      table('events', 'dwh.order_event_fact', [['event_id', 'BIGINT']]),
    ],
  };
}

function schemaResolutionResponse() {
  return {
    schema_version: '1' as const,
    status: 'NEEDS_CLARIFICATION' as const,
    resolutions: [
      {
        term: 'Заказ',
        lookup: {
          status: 'OK' as const,
          error_code: null,
          retryable: false,
          message: null,
        },
        catalog_answer: catalogAnswer(),
        interpretation: {
          term: 'Заказ',
          kind: 'entity' as const,
          normalized_term: 'заказ',
          recommended_entity_id: 'orders',
          recommended_column_ids: [],
          confidence: 0.82,
          reason: 'Таблица содержит факты заказов.',
          clarification_question: 'Выберите факты заказов или события заказов.',
        },
        needs_clarification: true,
      },
    ],
    llm: {
      status: 'APPLIED' as const,
      prompt: {
        name: 'sql_schema_interpreter',
        version: '1',
        content_hash: 'sha256:test-prompt',
      },
      warning: null,
    },
  };
}

function schemaEntityDetailsResponse(
  id = 'orders',
  fqn = 'dwh.order_fact',
  columns: Array<[string, string]> = [
    ['order_id', 'BIGINT'],
    ['status_id', 'BIGINT'],
    ['paid_amount_rub', 'NUMERIC(18,2)'],
  ],
) {
  const entity = table(id, fqn, columns);
  return {
    schema_version: '1' as const,
    status: 'READY' as const,
    entities: [
      {
        entity_id: id,
        lookup: {
          status: 'OK' as const,
          error_code: null,
          retryable: false,
          message: null,
        },
        entity: {
          ...entity,
          schema_loaded: true,
          schema_fingerprint: `sha256:${id}-v4`,
        },
        freshness: catalogAnswer().freshness,
        retrieval: 'omd_dataset_hybrid_rrf',
        sources: catalogAnswer().sources,
        warnings: [],
      },
    ],
  };
}

describe('SchemaWorkspaceDialog', () => {
  beforeEach(() => {
    window.localStorage.clear();
    mockedResolveSqlSchema.mockReset();
    mockedResolveSqlSchema.mockResolvedValue(schemaResolutionResponse());
    mockedLoadSqlSchemaEntities.mockReset();
    mockedLoadSqlSchemaEntities.mockResolvedValue(
      schemaEntityDetailsResponse(),
    );
  });

  it('requires an explicit table decision and persists selected fields', async () => {
    render(<SchemaWorkspaceDialog scope={scope} />);

    fireEvent.click(screen.getByTestId('open-schema-workspace'));
    fireEvent.change(
      await screen.findByRole('textbox', {
        name: 'Исходные требования к запросу',
      }),
      { target: { value: 'Вывести оплаченные заказы со статусом и суммой.' } },
    );
    fireEvent.change(
      await screen.findByRole('textbox', { name: 'Нужные бизнес-сущности' }),
      { target: { value: 'Заказ' } },
    );
    fireEvent.click(
      screen.getByRole('button', { name: 'Найти в базе знаний' }),
    );

    expect(await screen.findByText('Нужно уточнение')).toBeInTheDocument();
    expect(screen.getByText('Интерпретация LLM')).toBeInTheDocument();
    expect(
      screen.getByText('Таблица содержит факты заказов.'),
    ).toBeInTheDocument();
    const orders = screen.getByRole('radio', {
      name: 'Выбрать dwh.order_fact',
    });
    const events = screen.getByRole('radio', {
      name: 'Выбрать dwh.order_event_fact',
    });
    expect(orders).not.toBeChecked();
    expect(events).not.toBeChecked();

    fireEvent.click(orders);
    expect(screen.getByTestId('schema-snapshot-status')).toHaveTextContent(
      'Нужно уточнение',
    );
    expect(mockedLoadSqlSchemaEntities).toHaveBeenCalledWith({
      entity_ids: ['orders'],
      locale: 'ru',
    });
    expect(
      await screen.findByRole('checkbox', { name: 'Поле status_id' }),
    ).toBeInTheDocument();
    expect(screen.getByTestId('schema-snapshot-status')).toHaveTextContent(
      'Нужно уточнение',
    );
    fireEvent.click(screen.getByRole('checkbox', { name: 'Поле status_id' }));
    expect(screen.getByTestId('schema-snapshot-status')).toHaveTextContent(
      'Снимок готов',
    );
    expect(screen.getByText('Ключи и связи')).toBeInTheDocument();
    expect(screen.getByText('OpenMetadata', { selector: 'a' })).toHaveAttribute(
      'href',
      'https://metadata.example',
    );
    expect(screen.getByText('RAGFlow Dataset')).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole('checkbox', { name: 'Поле paid_amount_rub' }),
    );

    expect(mockedResolveSqlSchema).toHaveBeenCalledWith({
      terms: ['Заказ'],
      requirements: 'Вывести оплаченные заказы со статусом и суммой.',
      locale: 'ru',
    });
    const storageKey = createSchemaWorkspaceStorageKey(scope);
    await waitFor(() => {
      const saved = window.localStorage.getItem(storageKey);
      expect(saved).toContain('dwh.order_fact');
      expect(saved).toContain('dwh.order_fact.status_id');
      expect(saved).toContain('dwh.order_fact.paid_amount_rub');
    });

    fireEvent.change(
      screen.getByRole('textbox', {
        name: 'Исходные требования к запросу',
      }),
      { target: { value: 'Вывести только возвраты.' } },
    );
    expect(screen.queryByText('dwh.order_fact')).not.toBeInTheDocument();
    expect(screen.getByTestId('schema-snapshot-export')).toBeDisabled();
  });

  it('drops a pending catalog result when source input changes', async () => {
    let resolvePending!: (
      answer: ReturnType<typeof schemaResolutionResponse>,
    ) => void;
    const pending = new Promise<ReturnType<typeof schemaResolutionResponse>>(
      (resolve) => {
        resolvePending = resolve;
      },
    );
    mockedResolveSqlSchema.mockReturnValueOnce(pending);
    render(<SchemaWorkspaceDialog scope={scope} />);

    fireEvent.click(screen.getByTestId('open-schema-workspace'));
    fireEvent.change(
      await screen.findByRole('textbox', {
        name: 'Исходные требования к запросу',
      }),
      { target: { value: 'Вывести заказы.' } },
    );
    const entitiesInput = screen.getByRole('textbox', {
      name: 'Нужные бизнес-сущности',
    });
    fireEvent.change(entitiesInput, { target: { value: 'Заказ' } });
    fireEvent.click(
      screen.getByRole('button', { name: 'Найти в базе знаний' }),
    );
    await waitFor(() =>
      expect(mockedResolveSqlSchema).toHaveBeenCalledTimes(1),
    );

    fireEvent.change(entitiesInput, { target: { value: 'Возврат' } });
    await act(async () => {
      resolvePending(schemaResolutionResponse());
      await pending;
    });

    expect(entitiesInput).toHaveValue('Возврат');
    expect(screen.queryByText('dwh.order_fact')).not.toBeInTheDocument();
    expect(screen.getByTestId('schema-snapshot-export')).toBeDisabled();
  });

  it('drops a pending catalog result when the owner scope changes', async () => {
    let resolvePending!: (
      answer: ReturnType<typeof schemaResolutionResponse>,
    ) => void;
    const pending = new Promise<ReturnType<typeof schemaResolutionResponse>>(
      (resolve) => {
        resolvePending = resolve;
      },
    );
    mockedResolveSqlSchema.mockReturnValueOnce(pending);
    const { rerender } = render(<SchemaWorkspaceDialog scope={scope} />);

    fireEvent.click(screen.getByTestId('open-schema-workspace'));
    fireEvent.change(
      await screen.findByRole('textbox', {
        name: 'Исходные требования к запросу',
      }),
      { target: { value: 'Вывести заказы.' } },
    );
    fireEvent.change(
      await screen.findByRole('textbox', { name: 'Нужные бизнес-сущности' }),
      { target: { value: 'Заказ' } },
    );
    fireEvent.click(
      screen.getByRole('button', { name: 'Найти в базе знаний' }),
    );
    await waitFor(() =>
      expect(mockedResolveSqlSchema).toHaveBeenCalledTimes(1),
    );

    const nextScope = { ...scope, userId: 'user-2' };
    rerender(<SchemaWorkspaceDialog scope={nextScope} />);
    await waitFor(() =>
      expect(
        screen.getByRole('textbox', { name: 'Нужные бизнес-сущности' }),
      ).toHaveValue(''),
    );
    await act(async () => {
      resolvePending(schemaResolutionResponse());
      await pending;
    });

    expect(screen.queryByText('dwh.order_fact')).not.toBeInTheDocument();
    expect(
      window.localStorage.getItem(createSchemaWorkspaceStorageKey(nextScope)) ||
        '',
    ).not.toContain('dwh.order_fact');
  });

  it('keeps concurrent detail loads for different terms independent', async () => {
    const base = schemaResolutionResponse();
    const customerAnswer = {
      ...catalogAnswer(),
      question: 'клиент',
      entities: [
        table('customers', 'dwh.customer_dim', [['customer_id', 'BIGINT']]),
        table('contacts', 'dwh.customer_contact', [['contact_id', 'BIGINT']]),
      ],
    };
    mockedResolveSqlSchema.mockResolvedValueOnce({
      ...base,
      resolutions: [
        base.resolutions[0],
        {
          ...base.resolutions[0],
          term: 'Клиент',
          catalog_answer: customerAnswer,
          interpretation: {
            ...base.resolutions[0].interpretation,
            term: 'Клиент',
            normalized_term: 'клиент',
            recommended_entity_id: 'customers',
            reason: 'Таблица содержит клиентов.',
            clarification_question: 'Выберите таблицу клиентов.',
          },
        },
      ],
    });

    let resolveOrders!: (
      value: ReturnType<typeof schemaEntityDetailsResponse>,
    ) => void;
    let resolveCustomers!: (
      value: ReturnType<typeof schemaEntityDetailsResponse>,
    ) => void;
    mockedLoadSqlSchemaEntities.mockImplementation(({ entity_ids }) => {
      return new Promise((resolve) => {
        if (entity_ids[0] === 'orders') resolveOrders = resolve;
        if (entity_ids[0] === 'customers') resolveCustomers = resolve;
      });
    });

    render(<SchemaWorkspaceDialog scope={scope} />);
    fireEvent.click(screen.getByTestId('open-schema-workspace'));
    fireEvent.change(
      await screen.findByRole('textbox', {
        name: 'Исходные требования к запросу',
      }),
      { target: { value: 'Вывести заказы и клиентов.' } },
    );
    fireEvent.change(
      screen.getByRole('textbox', { name: 'Нужные бизнес-сущности' }),
      { target: { value: 'Заказ\nКлиент' } },
    );
    fireEvent.click(
      screen.getByRole('button', { name: 'Найти в базе знаний' }),
    );

    fireEvent.click(
      await screen.findByRole('radio', { name: 'Выбрать dwh.order_fact' }),
    );
    fireEvent.click(
      screen.getByRole('radio', { name: 'Выбрать dwh.customer_dim' }),
    );
    await waitFor(() =>
      expect(mockedLoadSqlSchemaEntities).toHaveBeenCalledTimes(2),
    );

    await act(async () => {
      resolveCustomers(
        schemaEntityDetailsResponse('customers', 'dwh.customer_dim', [
          ['customer_id', 'BIGINT'],
        ]),
      );
    });
    await act(async () => {
      resolveOrders(schemaEntityDetailsResponse());
    });

    expect(
      await screen.findByRole('checkbox', { name: 'Поле customer_id' }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('checkbox', { name: 'Поле order_id' }),
    ).toBeInTheDocument();
  });

  it('ignores an older detail failure after the same table is reselected', async () => {
    const requests: Array<{
      resolve: (value: ReturnType<typeof schemaEntityDetailsResponse>) => void;
      reject: (reason?: unknown) => void;
    }> = [];
    mockedLoadSqlSchemaEntities.mockImplementation(
      () =>
        new Promise((resolve, reject) => {
          requests.push({ resolve, reject });
        }),
    );
    render(<SchemaWorkspaceDialog scope={scope} />);

    fireEvent.click(screen.getByTestId('open-schema-workspace'));
    fireEvent.change(
      await screen.findByRole('textbox', {
        name: 'Исходные требования к запросу',
      }),
      { target: { value: 'Вывести заказы.' } },
    );
    fireEvent.change(
      screen.getByRole('textbox', { name: 'Нужные бизнес-сущности' }),
      { target: { value: 'Заказ' } },
    );
    fireEvent.click(
      screen.getByRole('button', { name: 'Найти в базе знаний' }),
    );

    const orders = await screen.findByRole('radio', {
      name: 'Выбрать dwh.order_fact',
    });
    const events = screen.getByRole('radio', {
      name: 'Выбрать dwh.order_event_fact',
    });
    fireEvent.click(orders);
    fireEvent.click(events);
    fireEvent.click(orders);
    await waitFor(() => expect(requests).toHaveLength(3));

    await act(async () => {
      requests[2].resolve(schemaEntityDetailsResponse());
    });
    expect(
      await screen.findByRole('checkbox', { name: 'Поле order_id' }),
    ).toBeInTheDocument();

    await act(async () => {
      requests[0].reject(new Error('stale failure'));
      requests[1].resolve(
        schemaEntityDetailsResponse('events', 'dwh.order_event_fact', [
          ['event_id', 'BIGINT'],
        ]),
      );
    });
    expect(
      screen.getByRole('checkbox', { name: 'Поле order_id' }),
    ).toBeInTheDocument();
    expect(screen.queryByText('stale failure')).not.toBeInTheDocument();
  });

  it('shows a catalog lookup failure as retryable error, not not-found', async () => {
    mockedResolveSqlSchema.mockResolvedValueOnce({
      schema_version: '1',
      status: 'DEGRADED',
      resolutions: [
        {
          term: 'Заказ',
          lookup: {
            status: 'ERROR',
            error_code: 'CATALOG_TIMEOUT',
            retryable: true,
            message: 'Истекло время ожидания каталога.',
          },
          catalog_answer: { ...catalogAnswer(), entities: [] },
          interpretation: {
            term: 'Заказ',
            kind: 'entity',
            normalized_term: 'заказ',
            recommended_entity_id: null,
            recommended_column_ids: [],
            confidence: null,
            reason: 'Каталог недоступен.',
            clarification_question: null,
          },
          needs_clarification: true,
        },
      ],
      llm: { status: 'SKIPPED', prompt: null, warning: null },
    });
    render(<SchemaWorkspaceDialog scope={scope} />);

    fireEvent.click(screen.getByTestId('open-schema-workspace'));
    fireEvent.change(
      await screen.findByRole('textbox', {
        name: 'Исходные требования к запросу',
      }),
      { target: { value: 'Вывести заказы.' } },
    );
    fireEvent.change(
      screen.getByRole('textbox', { name: 'Нужные бизнес-сущности' }),
      { target: { value: 'Заказ' } },
    );
    fireEvent.click(
      screen.getByRole('button', { name: 'Найти в базе знаний' }),
    );

    expect(
      await screen.findByText('Истекло время ожидания каталога.'),
    ).toBeInTheDocument();
    expect(screen.getByText('Ошибка каталога')).toBeInTheDocument();
    expect(screen.queryByText('Таблица не найдена')).not.toBeInTheDocument();
    expect(mockedLoadSqlSchemaEntities).not.toHaveBeenCalled();
  });
});
