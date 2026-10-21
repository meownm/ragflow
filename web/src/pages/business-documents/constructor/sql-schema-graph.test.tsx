import '@/locales/config';
import {
  fetchBusinessDocumentSqlGraph,
  fetchBusinessDocumentSqlProjectGraph,
  fetchBusinessDocumentSqlView,
  saveBusinessDocumentSqlView,
} from '@/services/business-document-service';
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { SqlSchemaGraph } from './sql-schema-graph';
type SqlAgentProject =
  import('@/pages/business-documents/types').SqlAgentProject;
type SqlQueryGraph = import('@/pages/business-documents/types').SqlQueryGraph;

jest.mock('@/services/business-document-service', () => ({
  fetchBusinessDocumentSqlGraph: jest.fn(),
  fetchBusinessDocumentSqlProjectGraph: jest.fn(),
  fetchBusinessDocumentSqlView: jest.fn(),
  saveBusinessDocumentSqlView: jest.fn(),
}));

jest.mock('@xyflow/react', () => ({
  ReactFlow: ({ nodes, nodeTypes, onNodeClick, children }: any) => (
    <div>
      {nodes.map((node: any) => {
        const Component = nodeTypes[node.type];
        return (
          <div key={node.id} onClick={() => onNodeClick?.({}, node)}>
            <Component data={node.data} selected={false} />
          </div>
        );
      })}
      {children}
    </div>
  ),
  Background: () => null,
  Controls: () => null,
  Handle: () => null,
  Position: { Left: 'left', Right: 'right' },
  MarkerType: { ArrowClosed: 'arrowclosed' },
  applyNodeChanges: (_: unknown, nodes: unknown) => nodes,
}));

const project = {
  id: 'project-1',
  artifact_ids: { query: 'query-1' },
  operation_state: 'IDLE',
} as SqlAgentProject;
const schema = { accepted_schema: [{ entity_id: 'orders' }] };
const sql = 'SELECT o.id FROM public.orders o';
const graph: SqlQueryGraph = {
  schema_version: '1',
  query_hash: 'sha256:query',
  depth: 1,
  truncated: false,
  freshness: { stale: false, threshold_hours: 24 },
  warnings: [],
  tables: [],
  paths: {
    customers: [
      {
        entity_ids: ['orders', 'customers'],
        relationship_ids: ['fk:customer'],
      },
    ],
  },
  paths_truncated: false,
  relationships: [
    {
      id: 'fk:customer',
      from_entity_id: 'orders',
      to_entity_id: 'customers',
      column_pairs: [{ from: 'customer_id', to: 'id' }],
    },
  ],
  nodes: [
    {
      id: 'query:0:o',
      scope: 'root',
      entity_id: 'orders',
      alias: 'o',
      kind: 'TABLE',
      membership: 'QUERY',
      label: 'Заказы',
      technical_name: 'orders',
      schema: 'public',
      columns: [
        {
          id: 'orders.id',
          name: 'id',
          data_type: 'BIGINT',
          constraint: 'PRIMARY_KEY',
          roles: ['SELECT'],
        },
      ],
    },
    {
      id: 'catalog:customers',
      scope: null,
      entity_id: 'customers',
      alias: '',
      kind: 'TABLE',
      membership: 'RELATED',
      label: 'Клиенты',
      technical_name: 'customers',
      schema: 'public',
      columns: [
        {
          id: 'customers.id',
          name: 'id',
          data_type: 'BIGINT',
          constraint: 'PRIMARY_KEY',
          roles: [],
        },
      ],
    },
  ],
  edges: [
    {
      id: 'fk:customer',
      from: 'query:0:o',
      to: 'catalog:customers',
      kind: 'FOREIGN_KEY',
      join_type: '',
      condition: '',
      cardinality: 'UNKNOWN',
      source: 'OpenMetadata',
      column_pairs: [{ from: 'customer_id', to: 'id' }],
    },
  ],
};
const loadGraph = fetchBusinessDocumentSqlGraph as jest.Mock;
const loadHistoricalGraph = fetchBusinessDocumentSqlProjectGraph as jest.Mock;
const loadView = fetchBusinessDocumentSqlView as jest.Mock;
const saveView = saveBusinessDocumentSqlView as jest.Mock;

beforeEach(() => {
  jest.clearAllMocks();
  loadGraph.mockResolvedValue(graph);
  loadHistoricalGraph.mockResolvedValue(graph);
  loadView.mockResolvedValue({ version: 0, view: null });
  saveView.mockImplementation(async (_, version, view) => ({
    version: version + 1,
    view,
  }));
});

function open(onRequestChange = jest.fn()) {
  const rendered = render(
    <SqlSchemaGraph
      project={project}
      sql={sql}
      schema={schema}
      onRequestChange={onRequestChange}
    />,
  );
  return { ...rendered, onRequestChange };
}

it('loads a historical graph by its saved source even when the current schema is absent', async () => {
  render(
    <SqlSchemaGraph
      project={project}
      sql={sql}
      schema={null}
      readOnlyView
      exportSource={{
        compilation_id: 'old-compilation',
        query_artifact_id: 'old-query',
      }}
      onRequestChange={jest.fn()}
    />,
  );
  expect(await screen.findAllByTestId('sql-er-table')).toHaveLength(2);
  expect(loadHistoricalGraph).toHaveBeenCalledWith(
    'project-1',
    { compilation_id: 'old-compilation', query_artifact_id: 'old-query' },
    1,
    expect.any(AbortSignal),
  );
  expect(loadGraph).not.toHaveBeenCalled();
});

it('shows a revoked historical source error without substituting the current graph', async () => {
  loadHistoricalGraph.mockRejectedValue(
    new Error('Доступ к старой таблице отозван'),
  );
  render(
    <SqlSchemaGraph
      project={project}
      sql={sql}
      schema={schema}
      readOnlyView
      exportSource={{
        compilation_id: 'old-compilation',
        query_artifact_id: 'old-query',
      }}
      onRequestChange={jest.fn()}
    />,
  );
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'Доступ к старой таблице отозван',
  );
  expect(screen.queryByTestId('sql-er-table')).not.toBeInTheDocument();
  expect(loadGraph).not.toHaveBeenCalled();
});

it('distinguishes query membership from visible neighboring tables', async () => {
  const { onRequestChange } = open();
  const nodes = await screen.findAllByTestId('sql-er-table');
  expect(nodes.map((node) => node.dataset.membership)).toEqual([
    'QUERY',
    'RELATED',
  ]);
  expect(nodes[0]).toHaveClass('border-accent-primary');
  expect(nodes[1]).toHaveClass('text-text-secondary');
  expect(screen.getByLabelText('Легенда диаграммы')).toBeInTheDocument();
  expect(onRequestChange).not.toHaveBeenCalled();
});

it('selecting and inspecting an unused table does not add it to SQL', async () => {
  const { onRequestChange } = open();
  fireEvent.click(await screen.findByLabelText('Клиенты · Связанная таблица'));
  expect(
    screen.getByText('Доступна по связи. Пока не входит в запрос.'),
  ).toBeInTheDocument();
  expect(onRequestChange).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Добавить в запрос' }));
  expect(onRequestChange).toHaveBeenCalledWith(
    expect.objectContaining({
      entityIds: ['customers'],
      text: expect.stringContaining('public.customers'),
    }),
  );
  expect(screen.getByLabelText('Клиенты · Связанная таблица')).toHaveAttribute(
    'data-membership',
    'RELATED',
  );
});

it('query-only mode and search change visibility without changing membership', async () => {
  const { onRequestChange } = open();
  await screen.findAllByTestId('sql-er-table');
  fireEvent.click(screen.getByLabelText('Только запрос'));
  expect(screen.getAllByTestId('sql-er-table')).toHaveLength(1);
  fireEvent.click(screen.getByLabelText('Только запрос'));
  fireEvent.change(screen.getByLabelText('Поиск таблицы на диаграмме'), {
    target: { value: 'customers' },
  });
  expect(screen.getAllByTestId('sql-er-table')).toHaveLength(1);
  expect(screen.getByTestId('sql-er-table')).toHaveAttribute(
    'data-membership',
    'RELATED',
  );
  expect(onRequestChange).not.toHaveBeenCalled();
});

it('loads a deeper neighborhood as a read-only graph request', async () => {
  const { onRequestChange } = open();
  await screen.findAllByTestId('sql-er-table');
  fireEvent.change(screen.getByLabelText('Глубина связанных таблиц'), {
    target: { value: '2' },
  });
  await waitFor(() =>
    expect(loadGraph).toHaveBeenLastCalledWith(
      { schema_version: '1', entity_ids: ['orders'], sql, depth: 2 },
      expect.any(AbortSignal),
    ),
  );
  expect(onRequestChange).not.toHaveBeenCalled();
});

it('provides a tabular keyboard-accessible equivalent of diagram membership', async () => {
  open();
  await screen.findAllByTestId('sql-er-table');
  fireEvent.click(screen.getByRole('button', { name: 'Список' }));
  expect(screen.getByRole('table')).toHaveTextContent('Не входит в запрос');
  fireEvent.click(
    screen.getAllByRole('button', { name: 'Посмотреть поля' })[1],
  );
  expect(
    screen.getByText('Доступна по связи. Пока не входит в запрос.'),
  ).toBeInTheDocument();
});

it('reports catalog failures without fabricating an empty successful graph', async () => {
  loadGraph.mockRejectedValue(new Error('Каталог недоступен'));
  open();
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'Каталог недоступен',
  );
  expect(screen.queryByTestId('sql-er-table')).not.toBeInTheDocument();
});

it('restores presentation independently from the accepted SQL revision', async () => {
  loadView.mockResolvedValue({
    version: 7,
    view: {
      positions: {},
      viewport: { x: 0, y: 0, zoom: 1 },
      mode: 'query',
      depth: 2,
      list_view: true,
    },
  });
  const { onRequestChange } = open();
  await screen.findByRole('table');
  expect(screen.getByLabelText('Только запрос')).toBeChecked();
  expect(screen.getByLabelText('Глубина связанных таблиц')).toHaveValue('2');
  expect(onRequestChange).not.toHaveBeenCalled();
});

it('persists only cosmetic state with its independent version', async () => {
  jest.useFakeTimers();
  try {
    const { onRequestChange } = open();
    await act(async () => {});
    fireEvent.click(screen.getByLabelText('Только запрос'));
    await act(async () => {
      jest.advanceTimersByTime(900);
    });
    expect(saveView).toHaveBeenCalledWith(
      'project-1',
      0,
      expect.objectContaining({ mode: 'query', positions: expect.any(Object) }),
      expect.any(AbortSignal),
    );
    expect(onRequestChange).not.toHaveBeenCalled();
  } finally {
    jest.useRealTimers();
  }
});

it('shows proposed additions separately from current query participation without duplicate catalog nodes', async () => {
  const added = {
    ...graph.nodes[1],
    id: 'query:root:c',
    scope: 'root',
    alias: 'c',
    membership: 'QUERY' as const,
  };
  loadGraph.mockResolvedValueOnce(graph).mockResolvedValueOnce({
    ...graph,
    nodes: [graph.nodes[0], added],
    edges: [],
  });
  render(
    <SqlSchemaGraph
      project={project}
      sql={sql}
      schema={schema}
      candidateSql={`${sql} LEFT JOIN public.customers c ON c.id=o.customer_id`}
      onRequestChange={jest.fn()}
    />,
  );
  await waitFor(() =>
    expect(
      screen.getByLabelText('Клиенты · c · Связанная таблица'),
    ).toHaveAttribute('data-change', 'ADDING'),
  );
  expect(screen.getAllByTestId('sql-er-table')).toHaveLength(2);
  expect(
    screen.getByLabelText('Клиенты · c · Связанная таблица'),
  ).toHaveAttribute('data-membership', 'RELATED');
  fireEvent.click(screen.getByLabelText('Клиенты · c · Связанная таблица'));
  expect(
    screen.getByText('Предлагается добавить. Пока не входит в текущий запрос.'),
  ).toBeInTheDocument();
});

it('marks removed instances in their own SQL scopes while keeping their current membership', async () => {
  const nested = {
    ...graph.nodes[0],
    id: 'query:root/subquery:0:o',
    scope: 'root/subquery:0',
  };
  loadGraph
    .mockResolvedValueOnce({
      ...graph,
      nodes: [graph.nodes[0], nested, graph.nodes[1]],
    })
    .mockResolvedValueOnce(graph);
  render(
    <SqlSchemaGraph
      project={project}
      sql={`${sql} WHERE EXISTS(SELECT o.id FROM public.orders o)`}
      schema={schema}
      candidateSql={sql}
      onRequestChange={jest.fn()}
    />,
  );
  await waitFor(() =>
    expect(
      screen
        .getAllByTestId('sql-er-table')
        .filter((node) => node.dataset.change === 'REMOVING'),
    ).toHaveLength(1),
  );
  expect(
    screen
      .getAllByTestId('sql-er-table')
      .filter((node) => node.dataset.change === 'REMOVING')[0],
  ).toHaveAttribute('data-membership', 'QUERY');
});

it('renders a graph even when personal view loading fails and avoids saving defaults over it', async () => {
  loadView.mockRejectedValue(new Error('View unavailable'));
  open();
  await screen.findAllByTestId('sql-er-table');
  expect(
    screen.getByText(/Не удалось загрузить расположение/),
  ).toBeInTheDocument();
  expect(saveView).not.toHaveBeenCalled();
});

it('cancels requests on page exit and renews them when a cached page returns', async () => {
  const rendered = open();
  await screen.findAllByTestId('sql-er-table');
  const first = loadView.mock.calls[0][1] as AbortSignal;
  expect(first.aborted).toBe(false);
  expect(loadGraph.mock.calls[0][1]).toBe(first);
  fireEvent(window, new Event('pagehide'));
  expect(first.aborted).toBe(true);
  const restored = new Event('pageshow');
  Object.defineProperty(restored, 'persisted', { value: true });
  fireEvent(window, restored);
  await waitFor(() => expect(loadView).toHaveBeenCalledTimes(2));
  const next = loadView.mock.calls[1][1] as AbortSignal;
  expect(next).not.toBe(first);
  expect(next.aborted).toBe(false);
  rendered.unmount();
  expect(next.aborted).toBe(true);
});

it('refreshing metadata does not reload and overwrite the personal view', async () => {
  open();
  await screen.findAllByTestId('sql-er-table');
  fireEvent.click(screen.getByRole('button', { name: 'Обновить схему' }));
  await waitFor(() => expect(loadGraph).toHaveBeenCalledTimes(2));
  expect(loadView).toHaveBeenCalledTimes(1);
});

it('can explicitly recover personal view loading after a service failure', async () => {
  loadView.mockRejectedValueOnce(new Error('View unavailable'));
  loadView.mockResolvedValueOnce({ version: 8, view: null });
  open();
  fireEvent.click(
    await screen.findByRole('button', { name: 'Загрузить сохранённый вид' }),
  );
  await waitFor(() => expect(loadView).toHaveBeenCalledTimes(2));
  await waitFor(() =>
    expect(
      screen.queryByText(/Не удалось загрузить расположение/),
    ).not.toBeInTheDocument(),
  );
});

it('a late save from the previous project cannot replace the new view version', async () => {
  jest.useFakeTimers();
  try {
    let finishOldSave!: (value: unknown) => void;
    saveView.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finishOldSave = resolve;
        }),
    );
    loadView.mockResolvedValueOnce({ version: 4, view: null });
    loadView.mockResolvedValueOnce({ version: 20, view: null });
    const rendered = open();
    await act(async () => {});
    await act(async () => {
      jest.advanceTimersByTime(900);
    });
    expect(saveView).toHaveBeenCalledTimes(1);
    const previousSignal = saveView.mock.calls[0][3] as AbortSignal;
    rendered.rerender(
      <SqlSchemaGraph
        project={{ ...project, id: 'project-2' }}
        sql={sql}
        schema={schema}
        onRequestChange={jest.fn()}
      />,
    );
    await act(async () => {});
    expect(previousSignal.aborted).toBe(true);
    await act(async () => {
      finishOldSave({ version: 999, view: {} });
      await Promise.resolve();
    });
    await act(async () => {
      jest.advanceTimersByTime(900);
    });
    expect(saveView).toHaveBeenLastCalledWith(
      'project-2',
      20,
      expect.any(Object),
      expect.any(AbortSignal),
    );
  } finally {
    jest.useRealTimers();
  }
});
