import {
  createBusinessDocumentSqlRevisionThread,
  decideBusinessDocumentSqlRevision,
  fetchBusinessDocumentSqlRevisionWorkspace,
  reopenBusinessDocumentSqlSchema,
  requestBusinessDocumentSqlRevision,
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
}));

const fetchWorkspace = fetchBusinessDocumentSqlRevisionWorkspace as jest.Mock;
const createThread = createBusinessDocumentSqlRevisionThread as jest.Mock;
const requestRevision = requestBusinessDocumentSqlRevision as jest.Mock;
const decideRevision = decideBusinessDocumentSqlRevision as jest.Mock;
const reopenSchema = reopenBusinessDocumentSqlSchema as jest.Mock;
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
