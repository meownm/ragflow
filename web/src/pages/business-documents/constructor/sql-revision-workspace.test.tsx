import {
  createBusinessDocumentSqlRevisionThread,
  decideBusinessDocumentSqlRevision,
  fetchBusinessDocumentSqlRevisionWorkspace,
  reopenBusinessDocumentSqlSchema,
  requestBusinessDocumentSqlRevision,
  restoreBusinessDocumentSqlQuery,
} from '@/services/business-document-service';
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { SqlRevisionWorkspacePanel } from './sql-revision-workspace';

jest.mock('@/services/business-document-service', () => ({
  createBusinessDocumentSqlRevisionThread: jest.fn(),
  decideBusinessDocumentSqlRevision: jest.fn(),
  fetchBusinessDocumentSqlRevisionWorkspace: jest.fn(),
  reopenBusinessDocumentSqlSchema: jest.fn(),
  replyBusinessDocumentSqlRevisionThread: jest.fn(),
  requestBusinessDocumentSqlRevision: jest.fn(),
  restoreBusinessDocumentSqlQuery: jest.fn(),
}));

const fetchWorkspace = fetchBusinessDocumentSqlRevisionWorkspace as jest.Mock;
const createThread = createBusinessDocumentSqlRevisionThread as jest.Mock;
const requestRevision = requestBusinessDocumentSqlRevision as jest.Mock;
const decideRevision = decideBusinessDocumentSqlRevision as jest.Mock;
const reopenSchema = reopenBusinessDocumentSqlSchema as jest.Mock;
const restoreQuery = restoreBusinessDocumentSqlQuery as jest.Mock;
const sql = 'SELECT o.id AS id FROM dwh.orders AS o LIMIT :row_limit';
const block = {
  id: 'join:1:hash',
  kind: 'JOIN',
  sql: 'JOIN dwh.customer AS c ON c.id = o.id',
  fingerprint: 'hash',
};
const thread = {
  id: 'thread-1',
  target: 'QUERY',
  base_artifact_id: 'query-1',
  anchor: { kind: 'BLOCK', block_id: block.id },
  anchor_status: 'CURRENT',
  messages: [
    {
      id: 'message-1',
      author_id: 'author',
      text: 'Уточни соединение',
      created_at: 1,
    },
  ],
  status: 'OPEN',
  version: 1,
};
const workspace = {
  project_id: 'project-1',
  state_version: 7,
  query_artifact_id: 'query-1',
  document_artifact_id: 'document-1',
  sql,
  blocks: [block],
  query_revisions: [{ id: 'query-1', revision: 2, sql, blocks: [block] }],
  draft: null,
  threads: [],
};
const project = {
  id: 'project-1',
  state_version: 7,
  operation_state: 'IDLE',
  pending_proposal: null,
  artifact_ids: {
    requirements: 'requirements-1',
    schema: 'schema-1',
    query: 'query-1',
  },
  document: {
    id: 'document-1',
    revision: 1,
    payload: { confirmed_conclusion: { text: 'Получено 10 строк' } },
  },
  documents: [{ id: 'document-1', revision: 1, payload: {} }],
};

describe('SqlRevisionWorkspacePanel', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    fetchWorkspace.mockResolvedValue({ ...workspace, threads: [] });
    createThread.mockResolvedValue(thread);
    requestRevision.mockResolvedValue({});
    decideRevision.mockResolvedValue({});
    reopenSchema.mockResolvedValue({});
    restoreQuery.mockResolvedValue({});
  });

  const historicalWorkspace = {
    ...workspace,
    query_revisions: [
      {
        id: 'query-1',
        compilation_id: 'compilation-2',
        revision: 2,
        sql,
        blocks: [block],
        parameters: [
          { name: 'row_limit', type: 'integer', value: 25, value_text: '25' },
        ],
        accepted_by: 'current-author',
        created_at: 1792530000000,
      },
      {
        id: 'query-0',
        compilation_id: 'compilation-1',
        revision: 1,
        sql,
        blocks: [block],
        parameters: [
          { name: 'row_limit', type: 'integer', value: 100, value_text: '100' },
        ],
        accepted_by: 'original-author',
        created_at: 1792440000000,
        source_artifact_ids: ['old-requirements', 'old-schema'],
        matches_current_context: false,
      },
    ],
  };

  it('shows author/time/source and parameter changes when historical SQL text is identical', async () => {
    fetchWorkspace.mockResolvedValue(historicalWorkspace);
    render(
      <SqlRevisionWorkspacePanel
        project={project as never}
        onRefresh={jest.fn()}
      />,
    );
    fireEvent.click(
      await screen.findByRole('button', { name: 'Запрос · версия 1' }),
    );
    expect(screen.getByText(/Автор: original-author/)).toBeInTheDocument();
    expect(
      screen.getByText(/old-requirements · old-schema/),
    ).toBeInTheDocument();
    expect(screen.getByText(/Текст SQL совпадает/)).toBeInTheDocument();
    const comparison = screen.getByRole('table', {
      name: 'Различия параметров',
    });
    expect(
      comparison.querySelector('[data-change="CHANGED"]'),
    ).toHaveTextContent('integer · 25integer · 100');
    expect(
      screen.getByRole('button', { name: 'Добавить комментарий' }),
    ).toBeDisabled();
    expect(
      screen.getByRole('button', { name: 'Восстановить как новую версию' }),
    ).toBeEnabled();
  });

  it('restores the selected compilation without sending browser SQL or starting a run', async () => {
    fetchWorkspace.mockResolvedValue(historicalWorkspace);
    const onRefresh = jest.fn().mockResolvedValue(undefined);
    render(
      <SqlRevisionWorkspacePanel
        project={project as never}
        onRefresh={onRefresh}
      />,
    );
    fireEvent.click(
      await screen.findByRole('button', { name: 'Запрос · версия 1' }),
    );
    fireEvent.click(
      screen.getByRole('button', { name: 'Восстановить как новую версию' }),
    );
    await waitFor(() =>
      expect(restoreQuery).toHaveBeenCalledWith(
        'project-1',
        'compilation-1',
        7,
        expect.any(String),
      ),
    );
    await waitFor(() => expect(onRefresh).toHaveBeenCalledTimes(1));
    expect(screen.getByLabelText('Проверенный SQL')).toHaveValue(sql);
  });

  it('reuses the restore idempotency key after a network failure', async () => {
    fetchWorkspace.mockResolvedValue(historicalWorkspace);
    restoreQuery
      .mockRejectedValueOnce(new Error('Ответ потерян'))
      .mockResolvedValueOnce({});
    render(
      <SqlRevisionWorkspacePanel
        project={project as never}
        onRefresh={jest.fn().mockResolvedValue(undefined)}
      />,
    );
    fireEvent.click(
      await screen.findByRole('button', { name: 'Запрос · версия 1' }),
    );
    fireEvent.click(
      screen.getByRole('button', { name: 'Восстановить как новую версию' }),
    );
    expect(await screen.findByRole('alert')).toHaveTextContent('Ответ потерян');
    fireEvent.click(
      screen.getByRole('button', { name: 'Восстановить как новую версию' }),
    );
    await waitFor(() => expect(restoreQuery).toHaveBeenCalledTimes(2));
    expect(restoreQuery.mock.calls[1][3]).toBe(restoreQuery.mock.calls[0][3]);
  });

  it('keeps the original restore command when project refresh observes an ambiguously committed request', async () => {
    fetchWorkspace.mockResolvedValue(historicalWorkspace);
    restoreQuery
      .mockRejectedValueOnce(new Error('Ответ потерян'))
      .mockResolvedValueOnce({});
    const view = render(
      <SqlRevisionWorkspacePanel
        project={project as never}
        onRefresh={jest.fn().mockResolvedValue(undefined)}
      />,
    );
    fireEvent.click(
      await screen.findByRole('button', { name: 'Запрос · версия 1' }),
    );
    fireEvent.click(
      screen.getByRole('button', { name: 'Восстановить как новую версию' }),
    );
    await screen.findByRole('alert');
    view.rerender(
      <SqlRevisionWorkspacePanel
        project={{ ...project, state_version: 8 } as never}
        onRefresh={jest.fn().mockResolvedValue(undefined)}
      />,
    );
    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: 'Восстановить как новую версию' }),
      ).toBeEnabled(),
    );
    fireEvent.click(
      screen.getByRole('button', { name: 'Восстановить как новую версию' }),
    );
    await waitFor(() => expect(restoreQuery).toHaveBeenCalledTimes(2));
    expect(restoreQuery.mock.calls[1]).toEqual(restoreQuery.mock.calls[0]);
  });

  it('does not refresh a different workspace after a late restore response', async () => {
    fetchWorkspace.mockResolvedValue(historicalWorkspace);
    let finish!: (value: unknown) => void;
    restoreQuery.mockReturnValue(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );
    const onRefresh = jest.fn();
    const view = render(
      <SqlRevisionWorkspacePanel
        project={project as never}
        onRefresh={onRefresh}
      />,
    );
    fireEvent.click(
      await screen.findByRole('button', { name: 'Запрос · версия 1' }),
    );
    fireEvent.click(
      screen.getByRole('button', { name: 'Восстановить как новую версию' }),
    );
    const reads = fetchWorkspace.mock.calls.length;
    view.unmount();
    await act(async () => finish({}));
    expect(onRefresh).not.toHaveBeenCalled();
    expect(fetchWorkspace).toHaveBeenCalledTimes(reads);
  });

  it('keeps historical reader view while hiding restore', async () => {
    fetchWorkspace.mockResolvedValue(historicalWorkspace);
    render(
      <SqlRevisionWorkspacePanel
        project={{ ...project, capabilities: { edit: false } } as never}
        onRefresh={jest.fn()}
      />,
    );
    fireEvent.click(
      await screen.findByRole('button', { name: 'Запрос · версия 1' }),
    );
    expect(
      screen.queryByRole('button', { name: 'Восстановить как новую версию' }),
    ).not.toBeInTheDocument();
    expect(restoreQuery).not.toHaveBeenCalled();
  });

  it.each(['unmount', 'pagehide', 'state'])(
    'aborts an outstanding workspace request on %s',
    async (change) => {
      let finish!: (value: unknown) => void;
      fetchWorkspace.mockReturnValueOnce(
        new Promise((resolve) => {
          finish = resolve;
        }),
      );
      const view = render(
        <SqlRevisionWorkspacePanel
          project={project as never}
          onRefresh={jest.fn()}
        />,
      );
      const signal = fetchWorkspace.mock.calls[0][1] as AbortSignal;
      expect(signal.aborted).toBe(false);
      if (change === 'unmount') view.unmount();
      else if (change === 'pagehide') fireEvent(window, new Event('pagehide'));
      else
        view.rerender(
          <SqlRevisionWorkspacePanel
            project={
              { ...project, state_version: project.state_version + 1 } as never
            }
            onRefresh={jest.fn()}
          />,
        );
      expect(signal.aborted).toBe(true);
      await act(async () => finish({ ...workspace, sql: 'STALE SQL' }));
      expect(screen.queryByText('STALE SQL')).not.toBeInTheDocument();
    },
  );

  it('reloads workspace with a new request after a BFcache restore', async () => {
    render(
      <SqlRevisionWorkspacePanel
        project={project as never}
        onRefresh={jest.fn()}
      />,
    );
    await screen.findByTestId('sql-revision-workspace');
    const signal = fetchWorkspace.mock.calls[0][1] as AbortSignal;
    fireEvent(window, new Event('pagehide'));
    const restored = new Event('pageshow');
    Object.defineProperty(restored, 'persisted', { value: true });
    fireEvent(window, restored);
    await waitFor(() => expect(fetchWorkspace).toHaveBeenCalledTimes(2));
    expect(signal.aborted).toBe(true);
    expect((fetchWorkspace.mock.calls[1][1] as AbortSignal).aborted).toBe(
      false,
    );
  });

  it('comments on a semantic SQL block and requests an AI proposal', async () => {
    fetchWorkspace
      .mockResolvedValueOnce({ ...workspace, threads: [] })
      .mockResolvedValue({ ...workspace, threads: [thread] });
    const onRefresh = jest.fn().mockResolvedValue(undefined);
    render(
      <SqlRevisionWorkspacePanel
        project={project as never}
        onRefresh={onRefresh}
      />,
    );
    await screen.findByTestId('sql-revision-workspace');
    fireEvent.click(screen.getByRole('button', { name: /Соединение/ }));
    fireEvent.change(screen.getByLabelText('Что изменить?'), {
      target: { value: 'Уточни соединение' },
    });
    fireEvent.click(
      screen.getByRole('button', { name: 'Добавить комментарий' }),
    );
    await waitFor(() =>
      expect(createThread).toHaveBeenCalledWith(
        'project-1',
        expect.objectContaining({
          target: 'QUERY',
          anchor: { kind: 'BLOCK', block_id: block.id },
          text: 'Уточни соединение',
        }),
      ),
    );
    fireEvent.click(
      await screen.findByRole('button', {
        name: 'Предложить изменение через ИИ',
      }),
    );
    await waitFor(() =>
      expect(requestRevision).toHaveBeenCalledWith(
        'project-1',
        'thread-1',
        7,
        expect.any(String),
      ),
    );
    expect(onRefresh).toHaveBeenCalled();
  });

  it('refreshes an assistant answer after generation without a project version change', async () => {
    fetchWorkspace.mockResolvedValueOnce({ ...workspace, threads: [thread] });
    const view = render(
      <SqlRevisionWorkspacePanel
        project={{ ...project, operation_state: 'RUNNING' } as never}
        onRefresh={jest.fn()}
      />,
    );
    await screen.findByText('Уточни соединение');
    fetchWorkspace.mockResolvedValue({
      ...workspace,
      threads: [
        {
          ...thread,
          version: 2,
          messages: [
            ...thread.messages,
            {
              id: 'assistant-1',
              role: 'assistant',
              response_kind: 'EXPLANATION',
              text: 'LIMIT ограничивает число строк.',
              created_at: 2,
            },
          ],
        },
      ],
    });
    view.rerender(
      <SqlRevisionWorkspacePanel
        project={project as never}
        onRefresh={jest.fn()}
      />,
    );
    expect(
      await screen.findByText('LIMIT ограничивает число строк.'),
    ).toBeInTheDocument();
    expect(
      screen.queryByTestId('sql-revision-proposal'),
    ).not.toBeInTheDocument();
    expect(decideRevision).not.toHaveBeenCalled();
  });

  it('a clarification choice fills a reply without sending or applying it', async () => {
    fetchWorkspace.mockResolvedValue({
      ...workspace,
      threads: [
        {
          ...thread,
          messages: [
            ...thread.messages,
            {
              id: 'assistant-1',
              role: 'assistant',
              response_kind: 'CLARIFICATION',
              text: 'Укажите год.',
              questions: [
                { id: 'year', text: 'Какой год?', options: ['2025', '2026'] },
              ],
              created_at: 2,
            },
          ],
        },
      ],
    });
    render(
      <SqlRevisionWorkspacePanel
        project={project as never}
        onRefresh={jest.fn()}
      />,
    );
    fireEvent.click(await screen.findByRole('button', { name: /^2026$/ }));
    expect(screen.getByDisplayValue('Какой год?: 2026')).toBeInTheDocument();
    expect(requestRevision).not.toHaveBeenCalled();
    expect(decideRevision).not.toHaveBeenCalled();
    expect(createThread).not.toHaveBeenCalled();
  });

  it('keeps history readable while preventing reader mutations', async () => {
    fetchWorkspace.mockResolvedValue({
      ...workspace,
      threads: [thread],
      query_revisions: [
        ...workspace.query_revisions,
        {
          id: 'older',
          revision: 1,
          sql: 'SELECT o.id AS id FROM dwh.orders o LIMIT :row_limit',
          blocks: [],
        },
      ],
    });
    render(
      <SqlRevisionWorkspacePanel
        project={
          { ...project, capabilities: { edit: false, execute: false } } as never
        }
        onRefresh={jest.fn()}
      />,
    );
    fireEvent.click(
      await screen.findByRole('button', { name: 'Запрос · версия 1' }),
    );
    expect(screen.getByLabelText('Проверенный SQL')).toHaveValue(
      'SELECT o.id AS id FROM dwh.orders o LIMIT :row_limit',
    );
    expect(screen.getByLabelText('Что изменить?')).toHaveAttribute('readonly');
    expect(
      screen.getByRole('button', { name: 'Предложить изменение через ИИ' }),
    ).toBeDisabled();
    expect(requestRevision).not.toHaveBeenCalled();
  });

  it('reports the viewed historical SQL and clears it when returning to current', async () => {
    const historical = {
      id: 'older',
      revision: 1,
      sql: 'SELECT o.id AS id FROM dwh.orders o LIMIT :row_limit',
      blocks: [],
    };
    fetchWorkspace.mockResolvedValue({
      ...workspace,
      query_revisions: [...workspace.query_revisions, historical],
    });
    const onQueryViewChange = jest.fn();
    render(
      <SqlRevisionWorkspacePanel
        project={project as never}
        onRefresh={jest.fn()}
        onQueryViewChange={onQueryViewChange}
      />,
    );
    fireEvent.click(
      await screen.findByRole('button', { name: 'Запрос · версия 1' }),
    );
    expect(onQueryViewChange).toHaveBeenLastCalledWith({
      id: historical.id,
      revision: historical.revision,
      sql: historical.sql,
    });
    fireEvent.click(screen.getByRole('button', { name: 'Запрос · версия 2' }));
    expect(onQueryViewChange).toHaveBeenLastCalledWith(null);
  });

  it('ignores a previous project response after switching projects', async () => {
    let finishFirst: (value: typeof workspace) => void = () => undefined;
    fetchWorkspace
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            finishFirst = resolve;
          }),
      )
      .mockResolvedValue({
        ...workspace,
        project_id: 'project-2',
        sql: 'SELECT 2 AS second',
      });
    const view = render(
      <SqlRevisionWorkspacePanel
        project={project as never}
        onRefresh={jest.fn()}
      />,
    );
    view.rerender(
      <SqlRevisionWorkspacePanel
        project={{ ...project, id: 'project-2' } as never}
        onRefresh={jest.fn()}
      />,
    );
    await screen.findByTestId('sql-revision-workspace');
    await act(async () => finishFirst(workspace));
    expect(screen.getByLabelText('Проверенный SQL')).toHaveValue(
      'SELECT 2 AS second',
    );
    fireEvent.change(screen.getByLabelText('Что изменить?'), {
      target: { value: 'Second project only' },
    });
    fireEvent.click(
      screen.getByRole('button', { name: 'Добавить комментарий' }),
    );
    await waitFor(() =>
      expect(createThread).toHaveBeenCalledWith(
        'project-2',
        expect.objectContaining({ text: 'Second project only' }),
      ),
    );
  });

  it('can comment on a compatible historical query revision', async () => {
    const originalSql = sql.replace('AS id', 'AS original_id');
    fetchWorkspace.mockResolvedValue({
      ...workspace,
      query_revisions: [
        ...workspace.query_revisions,
        { id: 'query-0', revision: 1, sql: originalSql, blocks: [block] },
      ],
    });
    render(
      <SqlRevisionWorkspacePanel
        project={project as never}
        onRefresh={jest.fn()}
      />,
    );
    await screen.findByTestId('sql-revision-workspace');
    fireEvent.click(screen.getByRole('button', { name: 'Запрос · версия 1' }));
    expect(screen.getByLabelText('Проверенный SQL')).toHaveValue(originalSql);
    fireEvent.change(screen.getByLabelText('Что изменить?'), {
      target: { value: 'Верни прежнее поле' },
    });
    fireEvent.click(
      screen.getByRole('button', { name: 'Добавить комментарий' }),
    );
    await waitFor(() =>
      expect(createThread).toHaveBeenCalledWith(
        'project-1',
        expect.objectContaining({
          target: 'QUERY',
          base_artifact_id: 'query-0',
          text: 'Верни прежнее поле',
        }),
      ),
    );
  });

  it('shows parameter-only changes and blocks a proposal after a new reply', async () => {
    fetchWorkspace.mockResolvedValue({
      ...workspace,
      threads: [{ ...thread, version: 3 }],
    });
    const proposed = {
      ...project,
      operation_state: 'REVIEW',
      pending_proposal: {
        id: 'proposal-parameters',
        kind: 'REVISION',
        payload: {
          agent_result: {
            proposal: {
              sql,
              parameters: [{ name: 'row_limit', type: 'integer', value: 20 }],
              parameter_diff: '- "value": 10\n+ "value": 20',
              summary: 'Изменить лимит',
              checks: { status: 'PASS' },
            },
            source: {
              target: 'QUERY',
              thread_id: 'thread-1',
              thread_version: 2,
              base_artifact_id: 'query-1',
            },
          },
        },
      },
    };
    render(
      <SqlRevisionWorkspacePanel
        project={proposed as never}
        onRefresh={jest.fn()}
      />,
    );
    await screen.findByTestId('sql-revision-proposal');
    expect(screen.getByText('Изменения параметров')).toBeInTheDocument();
    expect(
      screen.getByText(/Отклоните предложение и запросите новое/),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'Принять версию' }),
    ).toBeDisabled();
  });

  it('shows a saved document and requires explicit acceptance of a checked proposal', async () => {
    const proposed = {
      ...project,
      operation_state: 'REVIEW',
      pending_proposal: {
        id: 'proposal-1',
        kind: 'REVISION',
        payload: {
          agent_result: {
            proposal: {
              sql: sql.replace('AS id', 'AS order_id'),
              parameters: [],
              summary: 'Переименовать поле',
              checks: { status: 'PASS' },
            },
            source: { target: 'QUERY', thread_id: 'thread-1' },
          },
        },
      },
    };
    render(
      <SqlRevisionWorkspacePanel
        project={proposed as never}
        onRefresh={jest.fn().mockResolvedValue(undefined)}
      />,
    );
    await screen.findByTestId('sql-revision-proposal');
    expect(screen.getByText('Переименовать поле')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Итоговый документ' }));
    expect(screen.getByText('Получено 10 строк')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Принять версию' }));
    await waitFor(() =>
      expect(decideRevision).toHaveBeenCalledWith(
        'project-1',
        'proposal-1',
        7,
        expect.any(String),
        'ACCEPT',
        false,
      ),
    );
  });

  it('keeps prior documents readable and blocks accepting an unchecked SQL suggestion', async () => {
    const proposed = {
      ...project,
      documents: [
        {
          id: 'document-1',
          revision: 2,
          payload: { confirmed_conclusion: { text: 'Получено 10 строк' } },
        },
        {
          id: 'document-0',
          revision: 1,
          payload: { confirmed_conclusion: { text: 'Первый результат' } },
        },
      ],
      operation_state: 'REVIEW',
      pending_proposal: {
        id: 'blocked',
        kind: 'REVISION',
        payload: {
          agent_result: {
            proposal: {
              sql: 'SELECT * FROM unknown',
              summary: 'Новый источник',
              requested_sources: ['Платежи'],
              checks: {
                status: 'BLOCKED',
                reason: 'Таблица вне подтверждённой схемы',
              },
            },
            source: { target: 'QUERY' },
          },
        },
      },
    };
    const onSchemaRequested = jest.fn();
    render(
      <SqlRevisionWorkspacePanel
        project={proposed as never}
        onRefresh={jest.fn()}
        onSchemaRequested={onSchemaRequested}
      />,
    );
    await screen.findByTestId('sql-revision-proposal');
    expect(
      screen.getByRole('button', { name: 'Принять версию' }),
    ).toBeDisabled();
    expect(
      screen.getByText('Таблица вне подтверждённой схемы'),
    ).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole('button', {
        name: 'Пересмотреть данные и подтвердить источник',
      }),
    );
    await waitFor(() =>
      expect(reopenSchema).toHaveBeenCalledWith(
        'project-1',
        7,
        expect.any(String),
      ),
    );
    expect(onSchemaRequested).toHaveBeenCalledWith(['Платежи']);
    fireEvent.click(screen.getByRole('button', { name: 'Итоговый документ' }));
    fireEvent.click(
      screen.getByRole('button', { name: 'Документ · версия 1' }),
    );
    expect(screen.getByText('Первый результат')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Что изменить?'), {
      target: { value: 'Уточни первую редакцию' },
    });
    fireEvent.click(
      screen.getByRole('button', { name: 'Добавить комментарий' }),
    );
    await waitFor(() =>
      expect(createThread).toHaveBeenCalledWith(
        'project-1',
        expect.objectContaining({
          target: 'DOCUMENT',
          base_artifact_id: 'document-0',
          text: 'Уточни первую редакцию',
        }),
      ),
    );
  });

  it('requires a factual review before accepting a wording-only document revision', async () => {
    const proposed = {
      ...project,
      operation_state: 'REVIEW',
      pending_proposal: {
        id: 'wording',
        kind: 'REVISION',
        payload: {
          agent_result: {
            proposal: {
              document_text: 'Всего получено 10 строк',
              summary: 'Уточнить формулировку',
              checks: { status: 'PASS' },
            },
            source: { target: 'DOCUMENT' },
          },
        },
      },
    };
    render(
      <SqlRevisionWorkspacePanel
        project={proposed as never}
        onRefresh={jest.fn().mockResolvedValue(undefined)}
      />,
    );
    await screen.findByTestId('sql-revision-proposal');
    const accept = screen.getByRole('button', { name: 'Принять версию' });
    expect(accept).toBeDisabled();
    fireEvent.click(screen.getByRole('checkbox', { name: /Я сравнил текст/ }));
    fireEvent.click(accept);
    await waitFor(() =>
      expect(decideRevision).toHaveBeenCalledWith(
        'project-1',
        'wording',
        7,
        expect.any(String),
        'ACCEPT',
        true,
      ),
    );
  });

  it('does not accept a document proposal that needs a new data run', async () => {
    const proposed = {
      ...project,
      operation_state: 'REVIEW',
      pending_proposal: {
        id: 'needs-run',
        kind: 'REVISION',
        payload: {
          agent_result: {
            proposal: {
              requires_new_run: true,
              summary: 'Нужны текущие итоги',
              checks: { status: 'NEEDS_RUN', reason: 'Повторите запрос' },
            },
            source: { target: 'DOCUMENT' },
          },
        },
      },
    };
    render(
      <SqlRevisionWorkspacePanel
        project={proposed as never}
        onRefresh={jest.fn()}
      />,
    );
    await screen.findByTestId('sql-revision-proposal');
    expect(screen.getByText('Повторите запрос')).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'Принять версию' }),
    ).toBeDisabled();
  });
});
