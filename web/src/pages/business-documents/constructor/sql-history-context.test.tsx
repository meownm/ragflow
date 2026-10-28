import { fetchBusinessDocumentSqlHistoryContext } from '@/services/business-document-service';
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { SqlHistoryContext } from './sql-history-context';

jest.mock('@/services/business-document-service', () => ({
  fetchBusinessDocumentSqlHistoryContext: jest.fn(),
}));
const fetchContext = fetchBusinessDocumentSqlHistoryContext as jest.Mock;
const source = {
  compilation_id: 'compilation-old',
  query_artifact_id: 'query-old',
};
const identity = {
  id: 'requirements-old',
  revision: 1,
  accepted_by: 'old-author',
  created_at: 1792436401000,
  content_hash: 'sha256:old',
  source_proposal_id: 'proposal-old',
};
const packet = {
  schema_version: '1',
  project_id: 'project-1',
  source: {
    ...source,
    requirements_artifact_id: 'requirements-old',
    schema_artifact_id: 'schema-old',
  },
  catalog_changed: true,
  requirements: {
    ...identity,
    text: 'полное условие '.repeat(2000) + 'Последнее условие',
    requirements: [{ statement: 'Первое условие' }],
    questions: [{ text: 'Часовой пояс?', answer: 'Europe/Moscow' }],
  },
  schema: {
    ...identity,
    id: 'schema-old',
    accepted_schema: [
      { entity_id: 'orders', version: 1, schema_fingerprint: 'sha256:old' },
    ],
    snapshot: {
      status: 'READY',
      stale: false,
      fingerprint: 'sha256:old',
      original_requirements: 'Первое условие',
      tables: [
        {
          id: 'orders',
          fqn: 'old.public.orders',
          technical_name: 'orders',
          description: 'Исходная схема заказов',
          version: 1,
          columns: [
            {
              id: 'orders.last',
              name: 'last_field',
              data_type: 'NUMERIC',
              description: 'Последнее поле снимка',
              constraint: '',
              selected: true,
              glossary_terms: [],
            },
          ],
          table_constraints: [],
        },
      ],
    },
  },
};
const open = () =>
  fireEvent.click(
    screen.getByRole('button', {
      name: 'Требования и схема запроса',
    }),
  );

beforeEach(() => fetchContext.mockReset().mockResolvedValue(packet));

test('loads full selected context only on request, preserving final text, answers and all fields', async () => {
  render(<SqlHistoryContext projectId="project-1" source={source} />);
  expect(fetchContext).not.toHaveBeenCalled();
  open();
  expect(
    await screen.findByTestId('sql-history-requirements'),
  ).toHaveTextContent('Последнее условие');
  expect(screen.getByTestId('sql-history-requirements').textContent).toBe(
    packet.requirements.text,
  );
  expect(fetchContext).toHaveBeenCalledWith(
    'project-1',
    source,
    expect.any(AbortSignal),
  );
  fireEvent.click(screen.getByText('Уточнения, ответы и принятые требования'));
  expect(screen.getByText(/Europe\/Moscow/)).toBeVisible();
  fireEvent.click(
    within(screen.getByTestId('sql-history-schema')).getByText(
      /old.public.orders/,
    ),
  );
  expect(screen.getByText('last_field')).toBeVisible();
  expect(screen.getByText('Последнее поле снимка')).toBeVisible();
  expect(screen.getByText(/Каталог изменился/)).toBeVisible();
  expect(screen.getAllByText(/old-author/)).toHaveLength(2);
});

test('close discards data and reopening performs a fresh permission check', async () => {
  render(<SqlHistoryContext projectId="project-1" source={source} />);
  open();
  await screen.findByTestId('sql-history-requirements');
  fireEvent.click(screen.getByRole('button', { name: 'Закрыть контекст' }));
  expect(
    screen.queryByTestId('sql-history-requirements'),
  ).not.toBeInTheDocument();
  fetchContext.mockRejectedValueOnce(new Error('Доступ к таблице отозван'));
  open();
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'Доступ к таблице отозван',
  );
  expect(fetchContext).toHaveBeenCalledTimes(2);
  expect(screen.queryByTestId('sql-history-schema')).not.toBeInTheDocument();
});

test('keeps technical identities available in collapsed details without hiding source content', async () => {
  render(<SqlHistoryContext projectId="project-1" source={source} />);
  open();
  await screen.findByTestId('sql-history-requirements');
  expect(screen.getByText('Проверка: compilation-old')).not.toBeVisible();
  expect(screen.getByText('Версия: requirements-old')).not.toBeVisible();
  for (const value of screen.getAllByText('Основание: proposal-old'))
    expect(value).not.toBeVisible();
  fireEvent.click(screen.getByText('Технические сведения о запросе'));
  expect(screen.getByText('Проверка: compilation-old')).toBeVisible();
  fireEvent.click(screen.getAllByText('Технические сведения об источнике')[0]);
  expect(screen.getByText('Версия: requirements-old')).toBeVisible();
  expect(screen.getByTestId('sql-history-requirements')).toHaveTextContent(
    'Последнее условие',
  );
});

test.each(['close', 'pagehide', 'unmount', 'source-change'])(
  'ignores a delayed context after %s and aborts its request',
  async (action) => {
    let resolve!: (value: typeof packet) => void;
    fetchContext.mockReturnValueOnce(
      new Promise<typeof packet>((done) => {
        resolve = done;
      }),
    );
    const view = render(
      <SqlHistoryContext projectId="project-1" source={source} />,
    );
    open();
    const signal = fetchContext.mock.calls[0][2] as AbortSignal;
    if (action === 'close')
      fireEvent.click(screen.getByRole('button', { name: 'Закрыть контекст' }));
    if (action === 'pagehide') fireEvent(window, new Event('pagehide'));
    if (action === 'unmount') view.unmount();
    if (action === 'source-change')
      view.rerender(
        <SqlHistoryContext
          projectId="project-2"
          source={{ compilation_id: 'new', query_artifact_id: 'new-query' }}
        />,
      );
    expect(signal.aborted).toBe(true);
    await act(async () => resolve(packet));
    expect(
      screen.queryByTestId('sql-history-requirements'),
    ).not.toBeInTheDocument();
  },
);

test('pagehide removes already rendered metadata and pageshow requires explicit reload', async () => {
  render(<SqlHistoryContext projectId="project-1" source={source} />);
  open();
  await screen.findByTestId('sql-history-schema');
  fireEvent(window, new Event('pagehide'));
  fireEvent(window, new Event('pageshow'));
  expect(screen.queryByTestId('sql-history-schema')).not.toBeInTheDocument();
  expect(fetchContext).toHaveBeenCalledTimes(1);
  open();
  await screen.findByTestId('sql-history-schema');
  expect(fetchContext).toHaveBeenCalledTimes(2);
});

test('refresh hides cached schema before checking permission again and permits explicit retry', async () => {
  render(<SqlHistoryContext projectId="project-1" source={source} />);
  open();
  await screen.findByTestId('sql-history-schema');
  fetchContext.mockRejectedValueOnce(new Error('Каталог недоступен'));
  fireEvent.click(
    screen.getByRole('button', { name: 'Обновить контекст запроса' }),
  );
  expect(screen.queryByTestId('sql-history-schema')).not.toBeInTheDocument();
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'Каталог недоступен',
  );
  fireEvent.click(
    screen.getByRole('button', { name: 'Обновить контекст запроса' }),
  );
  await screen.findByTestId('sql-history-schema');
});

test('rejects a packet for another project or query instead of showing its contents', async () => {
  fetchContext.mockResolvedValueOnce({
    ...packet,
    project_id: 'other-project',
  });
  render(<SqlHistoryContext projectId="project-1" source={source} />);
  open();
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'не относится к выбранному запросу',
  );
  await waitFor(() =>
    expect(screen.queryByTestId('sql-history-schema')).not.toBeInTheDocument(),
  );
});

test.each(['requirements', 'schema'])(
  'opens an accepted %s artifact without a query or compilation',
  async (kind) => {
    const artifact =
      kind === 'requirements' ? packet.requirements : packet.schema;
    fetchContext.mockResolvedValueOnce({
      ...packet,
      source: { artifact_id: artifact.id },
      requirements: kind === 'requirements' ? artifact : null,
      schema: kind === 'schema' ? artifact : null,
    });
    render(
      <SqlHistoryContext
        projectId="project-1"
        source={{ artifact_id: artifact.id }}
        label="Открыть сохранённую версию"
      />,
    );
    expect(fetchContext).not.toHaveBeenCalled();
    fireEvent.click(
      screen.getByRole('button', { name: 'Открыть сохранённую версию' }),
    );
    expect(
      await screen.findByTestId(
        kind === 'requirements'
          ? 'sql-history-requirements'
          : 'sql-history-schema',
      ),
    ).toBeVisible();
    expect(fetchContext).toHaveBeenCalledWith(
      'project-1',
      { artifact_id: artifact.id },
      expect.any(AbortSignal),
    );
  },
);
