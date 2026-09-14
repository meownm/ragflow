import {
  compileBusinessDocumentSqlQuery,
  planBusinessDocumentSqlQuery,
  resolveBusinessDocumentSqlExecutionBinding,
} from '@/services/business-document-service';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QuerySpecificationDialog } from './query-specification-dialog';
import {
  createSchemaWorkspaceStorageKey,
  saveSchemaWorkspace,
} from './schema-workspace-storage';

jest.mock('@/services/business-document-service', () => ({
  compileBusinessDocumentSqlQuery: jest.fn(),
  planBusinessDocumentSqlQuery: jest.fn(),
  resolveBusinessDocumentSqlExecutionBinding: jest.fn(),
}));

const mockedCompile = jest.mocked(compileBusinessDocumentSqlQuery);
const mockedPlan = jest.mocked(planBusinessDocumentSqlQuery);
const mockedResolveBinding = jest.mocked(
  resolveBusinessDocumentSqlExecutionBinding,
);
const scope = { userId: 'query-user', tenantId: 'query-tenant' };

function resolution(
  id: string,
  fqn: string,
  fields: string[],
  selectedFields = fields,
) {
  const technicalName = fqn.split('.').at(-1)!;
  return {
    term: technicalName,
    status: 'confirmed' as const,
    decision: 'user' as const,
    selectedEntityId: id,
    selectedColumnIds: selectedFields.map((field) => `${fqn}.${field}`),
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

function readyWorkspace() {
  return {
    input: 'Заказ\nКлиент',
    requirements: 'Вывести заказы с клиентами, начиная с указанного ID.',
    resolutions: [
      resolution('orders', 'dwh.order_fact', ['order_id', 'customer_id']),
      resolution('customers', 'dwh.customer_dim', ['customer_id', 'name']),
    ],
  };
}

describe('QuerySpecificationDialog', () => {
  beforeEach(() => {
    window.localStorage.clear();
    mockedCompile.mockReset();
    mockedPlan.mockReset();
    mockedResolveBinding.mockReset();
    mockedResolveBinding.mockResolvedValue({
      schema_version: '1',
      status: 'BOUND',
      reason: null,
      snapshot_fingerprint: 'sha256:snapshot',
      catalog_scopes: [
        {
          service: 'warehouse',
          database: 'analytics',
          schema: 'dwh',
          table_ids: ['orders', 'customers'],
        },
      ],
      unresolved_catalog_scopes: [],
      candidates: [],
      selection: {
        decision: 'automatic_exact',
        profile: {
          id: 'profile-1',
          name: 'Warehouse RO',
          dialect: 'postgres',
          statement_timeout_ms: 30000,
          max_rows: 1000,
          max_result_bytes: 5000000,
          version: 1,
          policy_fingerprint: 'sha256:policy',
          target_database: 'analytics',
        },
        bindings: [{ binding_id: 'binding-1', version: 1 }],
      },
    });
    mockedPlan.mockResolvedValue({
      schema_version: '1',
      status: 'PROPOSED',
      proposal: {
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
          {
            id: 'select-2',
            kind: 'column',
            column_id: 'dwh.customer_dim.name',
            alias: 'customer_name',
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
            parameter_name: 'minimum_order_id',
            parameter_type: 'integer',
            parameter_value: '100',
            description: 'ID заказа не меньше 100.',
            confirmed: true,
          },
        ],
        order_by: [{ select_item_id: 'select-1', direction: 'ASC' }],
        row_limit: 1000,
      },
      clarification_questions: [],
      warning: null,
      diagnostic: null,
      llm: {
        status: 'APPLIED',
        prompt: {
          name: 'sql_query_planner',
          version: '1',
          content_hash: 'sha256:planner',
        },
        warning: null,
      },
    });
    mockedCompile.mockResolvedValue({
      schema_version: '1',
      status: 'READY',
      snapshot_fingerprint: 'sha256:compiled',
      blocking_issues: [],
      sql: [
        'SELECT',
        '    t1.order_id AS order_id',
        'FROM dwh.order_fact AS t1',
        'JOIN dwh.customer_dim AS t2',
        '    ON t1.customer_id = t2.customer_id',
        'WHERE t1.order_id >= :value_1',
        'ORDER BY order_id ASC',
        'LIMIT :row_limit',
      ].join('\n'),
      parameters: { value_1: 100, row_limit: 1000 },
      guard: {
        status: 'PASS',
        dialect: 'postgres',
        statement_count: 1,
        read_only: true,
        tables: ['dwh.customer_dim', 'dwh.order_fact'],
        parameters: ['row_limit', 'value_1'],
      },
    });
  });

  it('blocks SQL until JOIN and WHERE decisions are explicit, then compiles', async () => {
    saveSchemaWorkspace(
      window.localStorage,
      createSchemaWorkspaceStorageKey(scope),
      scope,
      readyWorkspace(),
    );
    render(<QuerySpecificationDialog scope={scope} />);

    fireEvent.click(screen.getByTestId('open-query-specification'));
    expect(screen.getByTestId('query-compile-status')).toHaveTextContent(
      'NEEDS_CLARIFICATION',
    );
    expect(screen.getByTestId('query-compile')).toBeDisabled();
    expect(mockedCompile).not.toHaveBeenCalled();

    fireEvent.click(
      screen.getByRole('checkbox', { name: 'Подтвердить JOIN 1' }),
    );
    expect(screen.getByTestId('query-compile')).toBeEnabled();

    fireEvent.click(screen.getByRole('button', { name: 'Условие WHERE' }));
    expect(screen.getByTestId('query-compile')).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Описание WHERE 1'), {
      target: { value: 'ID заказа не меньше указанной границы' },
    });
    fireEvent.change(screen.getByLabelText('Тип параметра WHERE 1'), {
      target: { value: 'integer' },
    });
    fireEvent.change(screen.getByLabelText('Значение параметра WHERE 1'), {
      target: { value: '100' },
    });
    fireEvent.click(
      screen.getByRole('checkbox', { name: 'Подтвердить WHERE 1' }),
    );

    expect(screen.getByTestId('query-compile')).toBeEnabled();
    fireEvent.click(screen.getByTestId('query-compile'));

    await waitFor(() => expect(mockedCompile).toHaveBeenCalledTimes(1));
    const request = mockedCompile.mock.calls[0][0];
    expect(request.specification.joins[0]).toMatchObject({
      entity_id: 'customers',
      left_column_id: 'dwh.order_fact.customer_id',
      right_column_id: 'dwh.customer_dim.customer_id',
      decision: 'user',
      confirmed: true,
    });
    expect(request.specification.filters[0]).toMatchObject({
      column_id: 'dwh.order_fact.order_id',
      operator: 'eq',
      parameter: 'value_1',
      decision: 'user',
      confirmed: true,
    });
    expect(request.specification.parameters).toContainEqual({
      name: 'value_1',
      type: 'integer',
      value: 100,
    });
    expect(await screen.findByTestId('query-sql-output')).toHaveTextContent(
      'FROM dwh.order_fact AS t1',
    );
    expect(screen.getByTestId('query-compile-status')).toHaveTextContent(
      'READY',
    );
    expect(screen.getByText('SQLGuard')).toBeInTheDocument();
  });

  it('does not offer compilation without a READY schema snapshot', () => {
    render(<QuerySpecificationDialog scope={scope} />);

    fireEvent.click(screen.getByTestId('open-query-specification'));

    expect(
      screen.getByText('Сначала подготовьте снимок схемы'),
    ).toBeInTheDocument();
    expect(screen.queryByTestId('query-compile')).not.toBeInTheDocument();
    expect(mockedCompile).not.toHaveBeenCalled();
  });

  it('applies an LLM plan but still requires user approval for JOIN and WHERE', async () => {
    saveSchemaWorkspace(
      window.localStorage,
      createSchemaWorkspaceStorageKey(scope),
      scope,
      readyWorkspace(),
    );
    render(<QuerySpecificationDialog scope={scope} />);

    fireEvent.click(screen.getByTestId('open-query-specification'));
    fireEvent.click(screen.getByTestId('query-plan'));

    await waitFor(() => expect(mockedPlan).toHaveBeenCalledTimes(1));
    expect(mockedPlan.mock.calls[0][0]).toMatchObject({
      schema_version: '1',
      accepted_requirements:
        'Вывести заказы с клиентами, начиная с указанного ID.',
      locale: 'ru',
    });
    expect(screen.getByTestId('query-plan-status')).toHaveTextContent(
      'PROPOSED',
    );
    expect(screen.getByLabelText('Alias SELECT 1')).toHaveValue('order_id');
    expect(screen.getByLabelText('Значение параметра WHERE 1')).toHaveValue(
      '100',
    );
    expect(screen.getByTestId('query-compile')).toBeDisabled();
    expect(
      screen.getByRole('checkbox', { name: 'Подтвердить JOIN 1' }),
    ).not.toBeChecked();
    expect(
      screen.getByRole('checkbox', { name: 'Подтвердить WHERE 1' }),
    ).not.toBeChecked();

    fireEvent.click(
      screen.getByRole('checkbox', { name: 'Подтвердить JOIN 1' }),
    );
    fireEvent.click(
      screen.getByRole('checkbox', { name: 'Подтвердить WHERE 1' }),
    );
    expect(screen.getByTestId('query-compile')).toBeEnabled();
  });

  it('resolves the READY snapshot to an execution profile without running SQL', async () => {
    saveSchemaWorkspace(
      window.localStorage,
      createSchemaWorkspaceStorageKey(scope),
      scope,
      readyWorkspace(),
    );
    render(<QuerySpecificationDialog scope={scope} />);

    fireEvent.click(screen.getByTestId('open-query-specification'));
    expect(mockedResolveBinding).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId('query-execution-binding-resolve'));

    await waitFor(() => expect(mockedResolveBinding).toHaveBeenCalledTimes(1));
    expect(mockedResolveBinding.mock.calls[0][0]).toMatchObject({
      schema_version: '1',
      accepted_requirements:
        'Вывести заказы с клиентами, начиная с указанного ID.',
      selected_profile_id: null,
    });
    expect(mockedResolveBinding.mock.calls[0][0].schema_snapshot).toMatchObject(
      { status: 'READY' },
    );
    expect(
      screen.getByTestId('query-execution-binding-status'),
    ).toHaveTextContent('BOUND');
    expect(
      screen.getByTestId('query-execution-binding-selection'),
    ).toHaveTextContent('Warehouse RO');
    expect(mockedCompile).not.toHaveBeenCalled();
  });
});
