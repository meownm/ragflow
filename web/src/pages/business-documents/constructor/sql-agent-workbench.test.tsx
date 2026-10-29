import '@/locales/config';
import {
  cancelBusinessDocumentSqlRun,
  checkBusinessDocumentSqlProjectConnection,
  compileBusinessDocumentSqlProject,
  completeBusinessDocumentSqlProject,
  configureBusinessDocumentSqlProjectConnection,
  confirmBusinessDocumentSqlConclusion,
  copyBusinessDocumentSqlAgentProject,
  createBusinessDocumentSqlAgentProject,
  decideBusinessDocumentSqlAgentProposal,
  fetchBusinessDocumentSqlAgentProject,
  fetchBusinessDocumentSqlRevisionWorkspace,
  listBusinessDocumentSqlAgentProjects,
  preflightBusinessDocumentSqlProject,
  previewBusinessDocumentSqlRun,
  proposeBusinessDocumentSqlConclusion,
  requestBusinessDocumentSqlAgent,
  reviseBusinessDocumentSqlQuestion,
  runBusinessDocumentSqlProject,
  updateBusinessDocumentSqlAgentProject,
} from '@/services/business-document-service';
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { SqlAgentWorkbench } from './sql-agent-workbench';

jest.mock('./sql-schema-graph', () => ({ SqlSchemaGraph: () => null }));

jest.mock('@/services/business-document-service', () => ({
  copyBusinessDocumentSqlAgentProject: jest.fn(),
  updateBusinessDocumentSqlAgentProject: jest.fn(),
  compileBusinessDocumentSqlProject: jest.fn(),
  configureBusinessDocumentSqlProjectConnection: jest.fn(),
  checkBusinessDocumentSqlProjectConnection: jest.fn(),
  createBusinessDocumentSqlAgentProject: jest.fn(),
  decideBusinessDocumentSqlAgentProposal: jest.fn(),
  fetchBusinessDocumentSqlAgentProject: jest.fn(),
  fetchBusinessDocumentSqlRevisionWorkspace: jest.fn(),
  saveBusinessDocumentSqlRevisionDraft: jest.fn(),
  listBusinessDocumentSqlAgentProjects: jest.fn(),
  preflightBusinessDocumentSqlProject: jest.fn(),
  runBusinessDocumentSqlProject: jest.fn(),
  previewBusinessDocumentSqlRun: jest.fn(),
  proposeBusinessDocumentSqlConclusion: jest.fn(),
  confirmBusinessDocumentSqlConclusion: jest.fn(),
  completeBusinessDocumentSqlProject: jest.fn(),
  cancelBusinessDocumentSqlRun: jest.fn(),
  loadBusinessDocumentSqlSchemaEntities: jest.fn(),
  requestBusinessDocumentSqlAgent: jest.fn(),
  reviseBusinessDocumentSqlQuestion: jest.fn(),
}));

const mockedList = listBusinessDocumentSqlAgentProjects as jest.Mock;
const mockedFetch = fetchBusinessDocumentSqlAgentProject as jest.Mock;
const mockedWorkspace = fetchBusinessDocumentSqlRevisionWorkspace as jest.Mock;
const mockedCreate = createBusinessDocumentSqlAgentProject as jest.Mock;
const mockedRequest = requestBusinessDocumentSqlAgent as jest.Mock;
const mockedRevise = reviseBusinessDocumentSqlQuestion as jest.Mock;
const mockedDecide = decideBusinessDocumentSqlAgentProposal as jest.Mock;
const mockedCompile = compileBusinessDocumentSqlProject as jest.Mock;
const mockedComplete = completeBusinessDocumentSqlProject as jest.Mock;
const mockedPreflight = preflightBusinessDocumentSqlProject as jest.Mock;
const mockedRun = runBusinessDocumentSqlProject as jest.Mock;
const mockedPreview = previewBusinessDocumentSqlRun as jest.Mock;
const mockedCancel = cancelBusinessDocumentSqlRun as jest.Mock;
const mockedProposeConclusion =
  proposeBusinessDocumentSqlConclusion as jest.Mock;
const mockedConfirmConclusion =
  confirmBusinessDocumentSqlConclusion as jest.Mock;

function project(patch: Record<string, unknown> = {}) {
  return {
    schema_version: '1',
    id: 'project-1',
    title: 'Продажи по регионам',
    execution_profile_id: null as string | null,
    source_request: 'Вывести продажи по регионам за месяц, максимум 100 строк.',
    locale: 'ru',
    stage: 'REQUIREMENTS',
    operation_state: 'IDLE',
    state_version: 1,
    next_agent: 'REQUIREMENTS',
    current_job: null,
    pending_proposal: null,
    artifact_ids: { requirements: null, schema: null, query: null },
    artifacts: { requirements: null, schema: null, query: null },
    last_error: null,
    capabilities: {
      requirements_agent: true,
      schema_agent: true,
      query_agent: true,
      result_agent: false,
      python_agent: false,
    },
    ...patch,
  };
}

describe('SqlAgentWorkbench', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockedWorkspace.mockResolvedValue({
      query_artifact_id: null,
      document_artifact_id: null,
      draft: null,
      threads: [],
      blocks: [],
      sql: '',
    });
    mockedList.mockResolvedValue([]);
  });

  it('does not show an error for an empty persisted error object', async () => {
    const saved = project({ last_error: {} });
    mockedList.mockResolvedValue([saved]);
    mockedFetch.mockResolvedValue(saved);

    render(<SqlAgentWorkbench />);

    expect(
      await screen.findByText('Разобрать исходные требования'),
    ).toBeInTheDocument();
    expect(screen.queryByText('Операция не выполнена')).not.toBeInTheDocument();
  });

  it('saves the project before starting the requirements analysis automatically', async () => {
    const created = project();
    mockedCreate.mockResolvedValue(created);
    mockedRequest.mockResolvedValue(
      project({
        operation_state: 'RUNNING',
        state_version: 2,
        next_agent: null,
        current_job: {
          id: 'job-1',
          kind: 'REQUIREMENTS',
          status: 'PENDING',
          progress: 0,
          progress_stage: 'QUEUED',
          progress_message: null,
          attempt: 0,
          max_attempts: 3,
          error: null,
        },
      }),
    );

    render(<SqlAgentWorkbench />);

    fireEvent.click(
      (await screen.findAllByRole('button', { name: 'Новый запрос' })).at(-1)!,
    );
    fireEvent.change(screen.getByLabelText('Название (необязательно)'), {
      target: { value: 'Продажи по регионам' },
    });
    fireEvent.change(screen.getByLabelText('Какой вопрос нужно решить?'), {
      target: { value: 'Вывести продажи по регионам за месяц.' },
    });
    fireEvent.click(
      screen.getByRole('button', { name: 'Создать и продолжить' }),
    );

    await waitFor(() =>
      expect(mockedRequest).toHaveBeenCalledWith(
        'project-1',
        expect.objectContaining({
          expected_state_version: 1,
          kind: 'REQUIREMENTS',
          payload: { locale: 'ru' },
        }),
      ),
    );
    expect(mockedCreate.mock.invocationCallOrder[0]).toBeLessThan(
      mockedRequest.mock.invocationCallOrder[0],
    );
  });

  it('revises the question at the expected version and restarts requirements', async () => {
    const original = project();
    mockedList.mockResolvedValue([original]);
    mockedFetch.mockResolvedValue(original);
    mockedRevise.mockResolvedValue(
      project({ source_request: 'Покажи новые заказы', state_version: 2 }),
    );
    mockedRequest.mockResolvedValue(
      project({
        source_request: 'Покажи новые заказы',
        state_version: 3,
        operation_state: 'RUNNING',
        next_agent: null,
      }),
    );
    render(<SqlAgentWorkbench />);
    fireEvent.click(
      await screen.findByRole('button', { name: 'Изменить задачу' }),
    );
    fireEvent.change(screen.getByLabelText('Уточнённая задача'), {
      target: { value: 'Покажи новые заказы' },
    });
    fireEvent.click(
      screen.getByRole('button', { name: 'Сохранить и проанализировать' }),
    );
    await waitFor(() =>
      expect(mockedRevise).toHaveBeenCalledWith(
        'project-1',
        1,
        expect.any(String),
        'Покажи новые заказы',
      ),
    );
    await waitFor(() =>
      expect(mockedRequest).toHaveBeenCalledWith(
        'project-1',
        expect.objectContaining({
          kind: 'REQUIREMENTS',
          expected_state_version: 2,
        }),
      ),
    );
  });

  it('keeps a pending requirements proposal readable but disabled for a reader', async () => {
    const review = project({
      operation_state: 'REVIEW',
      state_version: 2,
      next_agent: null,
      capabilities: { edit: false, execute: false },
      pending_proposal: {
        id: 'proposal-1',
        kind: 'REQUIREMENTS',
        status: 'PENDING',
        source_state_version: 2,
        payload: {
          agent_result: {
            proposal: {
              requirements: [
                {
                  id: 'REQ-OUT-001',
                  kind: 'output',
                  statement: 'Вывести регион и сумму продаж.',
                },
              ],
              questions: [],
            },
          },
        },
      },
    });
    mockedList.mockResolvedValue([review]);
    mockedFetch.mockResolvedValue(review);
    render(<SqlAgentWorkbench />);
    expect(
      await screen.findByTestId('sql-agent-requirements-review'),
    ).toHaveTextContent('Вывести регион и сумму продаж.');
    const accept = screen.getByRole('button', {
      name: 'Подтвердить требования',
    });
    const reject = screen.getByRole('button', {
      name: 'Отклонить и повторить',
    });
    expect(accept).toBeDisabled();
    expect(reject).toBeDisabled();
    fireEvent.click(accept);
    fireEvent.click(reject);
    expect(mockedDecide).not.toHaveBeenCalled();
  });

  it('requires an answer to a blocking question before accepting requirements', async () => {
    const review = project({
      operation_state: 'REVIEW',
      state_version: 2,
      next_agent: null,
      pending_proposal: {
        id: 'proposal-1',
        kind: 'REQUIREMENTS',
        status: 'PENDING',
        source_state_version: 2,
        payload: {
          agent_result: {
            proposal: {
              requirements: [
                {
                  id: 'REQ-OUT-001',
                  kind: 'output',
                  statement: 'Вывести регион и сумму продаж.',
                  rationale: 'Поля явно указаны.',
                },
              ],
              questions: [
                {
                  id: 'Q-001',
                  question: 'Какой период использовать?',
                  reason: 'Период влияет на фильтр.',
                  options: ['Календарный месяц', 'Последние 30 дней'],
                  allow_custom_answer: true,
                  blocking: true,
                },
              ],
            },
          },
        },
      },
    });
    mockedList.mockResolvedValue([review]);
    mockedFetch.mockResolvedValue(review);
    mockedDecide.mockResolvedValue(
      project({
        stage: 'SCHEMA',
        state_version: 3,
        next_agent: 'SCHEMA',
        artifacts: {
          requirements: {
            requirements: [{ statement: 'Вывести регион и продажи' }],
          },
          schema: null,
          query: null,
        },
        artifact_ids: {
          requirements: 'artifact-r',
          schema: null,
          query: null,
        },
      }),
    );
    mockedRequest.mockResolvedValue(
      project({
        stage: 'SCHEMA',
        operation_state: 'RUNNING',
        state_version: 4,
        next_agent: null,
      }),
    );

    render(<SqlAgentWorkbench />);

    const accept = await screen.findByRole('button', {
      name: 'Подтвердить требования',
    });
    expect(accept).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Календарный месяц' }));
    expect(accept).toBeEnabled();
    fireEvent.click(accept);

    await waitFor(() =>
      expect(mockedDecide).toHaveBeenCalledWith(
        'project-1',
        'proposal-1',
        expect.objectContaining({
          decision: 'ACCEPT',
          artifact_payload: {
            answers: { 'Q-001': 'Календарный месяц' },
          },
        }),
      ),
    );
    await screen.findByText('Уточнить поиск (необязательно)');
    expect(mockedRequest).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId('sql-agent-run-schema'));
    await waitFor(() =>
      expect(mockedRequest).toHaveBeenCalledWith(
        'project-1',
        expect.objectContaining({
          kind: 'SCHEMA',
          expected_state_version: 3,
          payload: { locale: 'ru', terms: [] },
        }),
      ),
    );
  });

  it('restarts schema search at the version returned by rejection without accepting empty data', async () => {
    const review = project({
      stage: 'SCHEMA',
      operation_state: 'REVIEW',
      state_version: 7,
      next_agent: null,
      artifacts: {
        requirements: { requirements: [{ statement: 'Посчитать источники' }] },
        schema: null,
        query: null,
      },
      pending_proposal: {
        id: 'schema-proposal',
        kind: 'SCHEMA',
        payload: { agent_result: { resolutions: [] } },
      },
    });
    mockedList.mockResolvedValue([review]);
    mockedFetch.mockResolvedValue(review);
    mockedDecide.mockResolvedValue(
      project({ stage: 'SCHEMA', next_agent: 'SCHEMA', state_version: 8 }),
    );
    mockedRequest.mockRejectedValue(new Error('Поиск временно недоступен'));
    render(<SqlAgentWorkbench />);
    expect(await screen.findByText('Данные не найдены')).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'Подтвердить схему' }),
    ).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Повторить поиск' }));
    await waitFor(() =>
      expect(mockedDecide).toHaveBeenCalledWith(
        'project-1',
        'schema-proposal',
        expect.objectContaining({
          expected_state_version: 7,
          decision: 'REJECT',
          artifact_payload: null,
        }),
      ),
    );
    await waitFor(() =>
      expect(mockedRequest).toHaveBeenCalledWith(
        'project-1',
        expect.objectContaining({
          kind: 'SCHEMA',
          expected_state_version: 8,
          payload: { locale: 'ru', terms: [] },
        }),
      ),
    );
    expect(
      await screen.findByText('Поиск временно недоступен'),
    ).toBeInTheDocument();
    expect(screen.getByTestId('sql-agent-run-schema')).toBeEnabled();
  });

  it.each([false, true])(
    'requires JOIN/filter acknowledgement and shows per-group selection: %s',
    async (perGroup) => {
      const review = project({
        stage: 'QUERY',
        operation_state: 'REVIEW',
        next_agent: null,
        pending_proposal: {
          id: 'proposal-query',
          kind: 'QUERY',
          status: 'PENDING',
          source_state_version: 3,
          payload: {
            agent_result: {
              proposal: {
                select: [
                  {
                    id: 'select-1',
                    alias: 'value',
                    kind: 'column',
                    column_id: 'column-1',
                  },
                ],
                joins: [
                  {
                    id: 'join-1',
                    join_type: 'INNER',
                    entity_id: 'table-2',
                    left_column_id: 'a',
                    right_column_id: 'b',
                    description: 'Связь заказов',
                  },
                ],
                filters: [
                  {
                    id: 'filter-1',
                    column_id: 'column-1',
                    operator: 'eq',
                    parameter_name: 'period',
                    description: 'Период',
                  },
                ],
                order_by: [],
                row_limit: 100,
                ...(perGroup
                  ? {
                      top_per_group: {
                        partition_by: ['select-1'],
                        order_by: [
                          { select_item_id: 'select-1', direction: 'DESC' },
                        ],
                        row_limit: 1,
                      },
                    }
                  : {}),
              },
            },
          },
        },
      });
      mockedList.mockResolvedValue([review]);
      mockedFetch.mockResolvedValue(review);
      mockedDecide.mockResolvedValue(
        project({ stage: 'COMPLETE', state_version: 4, next_agent: null }),
      );
      render(<SqlAgentWorkbench />);
      const accept = await screen.findByRole('button', {
        name: 'Подтвердить и собрать SQL',
      });
      if (perGroup) {
        expect(screen.getByText('В каждой группе')).toBeInTheDocument();
        expect(
          screen.getByText(
            /Лимит строк: 1.*Группы: value.*Порядок выбора: value по убыванию/,
          ),
        ).toBeInTheDocument();
      }
      expect(accept).toBeDisabled();
      fireEvent.click(
        screen.getByRole('checkbox', { name: /Подтверждаю связь/ }),
      );
      expect(accept).toBeDisabled();
      fireEvent.click(
        screen.getByRole('checkbox', { name: /Подтверждаю исключение строк/ }),
      );
      expect(accept).toBeEnabled();
      fireEvent.click(accept);
      await waitFor(() =>
        expect(mockedDecide).toHaveBeenCalledWith(
          'project-1',
          'proposal-query',
          expect.objectContaining({
            artifact_payload: {
              confirmed_join_ids: ['join-1'],
              confirmed_filter_ids: ['filter-1'],
            },
          }),
        ),
      );
    },
  );

  it('opens an earlier verified result after a later run fails', async () => {
    const ready = {
      id: 'ready-run',
      status: 'READY',
      row_count: 1,
      duration_ms: 10,
      columns: ['value'],
      compilation_id: 'compilation-1',
      checks: { status: 'PASS', completeness: 'FULL' },
    };
    const failed = {
      id: 'failed-run',
      status: 'FAILED',
      row_count: 0,
      duration_ms: 0,
      columns: [],
      compilation_id: 'compilation-1',
      checks: {},
      error: { code: 'SQL_TIMEOUT', message: 'Timed out' },
    };
    const current = project({
      stage: 'COMPLETE',
      next_agent: null,
      latest_run: failed,
      runs: [failed, ready],
      compilation: {
        id: 'compilation-1',
        result: {
          sql: 'SELECT value FROM public.sales LIMIT :row_limit',
          parameters: { row_limit: 100 },
          guard: { status: 'PASS' },
        },
      },
    });
    mockedList.mockResolvedValue([current]);
    mockedFetch.mockResolvedValue(current);
    mockedPreflight.mockResolvedValue({ binding: { status: 'UNAVAILABLE' } });
    mockedPreview.mockResolvedValue({
      run_id: 'ready-run',
      columns: ['value'],
      rows: [[42]],
      row_count: 1,
      offset: 0,
      checks: { status: 'PASS', completeness: 'FULL' },
    });
    render(<SqlAgentWorkbench />);
    fireEvent.click(
      await screen.findByRole('button', { name: /Запуск 1 · данные доступны/ }),
    );
    await waitFor(() =>
      expect(mockedPreview).toHaveBeenCalledWith(
        'project-1',
        'ready-run',
        0,
        expect.any(AbortSignal),
      ),
    );
    expect(await screen.findByText('42')).toBeInTheDocument();
  });

  it.each(['visible', 'pending', 'server-410'])(
    'clears expired result data and rejects stale replies: %s',
    async (scenario) => {
      const start = Date.now();
      let now = start;
      const deadline = start + 60_000;
      const clock = jest.spyOn(Date, 'now').mockImplementation(() => now);
      const ready = {
        id: 'retained-run',
        status: 'READY',
        expires_at: deadline,
        row_count: 2,
        duration_ms: 1,
        columns: ['private_value'],
        compilation_id: 'compilation-1',
        checks: { status: 'PASS', completeness: 'FULL' },
      };
      const current = project({
        stage: 'COMPLETE',
        next_agent: null,
        latest_run: ready,
        runs: [ready],
        compilation: {
          id: 'compilation-1',
          result: {
            sql: 'SELECT value FROM public.sales LIMIT :row_limit',
            parameters: { row_limit: 100 },
            guard: { status: 'PASS' },
          },
        },
      });
      mockedList.mockResolvedValue([current]);
      mockedFetch.mockResolvedValue(current);
      mockedPreflight.mockResolvedValue({ binding: { status: 'UNAVAILABLE' } });
      const rows = {
        run_id: 'retained-run',
        expires_at: deadline,
        columns: ['private_value'],
        rows: [['private retained cell']],
        row_count: 2,
        offset: 0,
        checks: { status: 'PASS' },
      };
      let resolve!: (value: unknown) => void;
      if (scenario === 'pending')
        mockedPreview.mockImplementation(
          () =>
            new Promise((answer) => {
              resolve = answer;
            }),
        );
      else mockedPreview.mockResolvedValue(rows);
      const rendered = render(<SqlAgentWorkbench />);
      try {
        if (scenario === 'pending') {
          await waitFor(() => expect(mockedPreview).toHaveBeenCalled());
          const signal = mockedPreview.mock.calls[0][3] as AbortSignal;
          act(() => {
            now = deadline;
            window.dispatchEvent(new Event('focus'));
            expect(signal.aborted).toBe(true);
          });
          await act(async () => resolve(rows));
        } else {
          expect(
            await screen.findByText('private retained cell'),
          ).toBeVisible();
          if (scenario === 'server-410') {
            mockedPreview.mockRejectedValue({ code: 'SQL_RESULT_EXPIRED' });
            fireEvent.click(
              screen.getByRole('button', { name: 'Показать ещё' }),
            );
          } else {
            act(() => {
              now = deadline;
              window.dispatchEvent(new Event('focus'));
              expect(
                screen.queryByTestId('sql-result-table'),
              ).not.toBeInTheDocument();
            });
          }
        }
        expect(
          await screen.findByText('Срок хранения результата истёк'),
        ).toBeVisible();
        expect(
          screen.queryByText('private retained cell'),
        ).not.toBeInTheDocument();
        expect(
          screen.queryByRole('button', { name: 'Скачать результат JSON' }),
        ).not.toBeInTheDocument();
        expect(mockedRun).not.toHaveBeenCalled();
      } finally {
        rendered.unmount();
        clock.mockRestore();
      }
    },
  );

  it('compiles a completed project and requires an explicit run before showing rows', async () => {
    const complete = project({
      stage: 'COMPLETE',
      state_version: 7,
      next_agent: null,
      artifact_ids: {
        requirements: 'artifact-r',
        schema: 'artifact-s',
        query: 'artifact-q',
      },
      artifacts: {
        requirements: {
          requirements: [
            { statement: 'Вывести сумму продаж.', status: 'ACCEPTED' },
          ],
          questions: [],
        },
        schema: {
          schema_snapshot: {
            format: 'ragflow-sql-schema-snapshot',
            schema_version: '1',
            status: 'READY',
            requirements: [],
          },
          accepted_schema: [
            {
              entity_id: 'sales',
              version: 1,
              schema_fingerprint: `sha256:${'a'.repeat(64)}`,
            },
          ],
        },
        query: {
          base_entity_id: 'sales',
          aliases: { sales: 't1' },
          select: [
            {
              id: 'select-1',
              kind: 'sum',
              column_id: 'sales.amount',
              alias: 'total_amount',
              grain: null,
            },
          ],
          joins: [],
          filters: [],
          order_by: [{ select_item_id: 'select-1', direction: 'DESC' }],
          row_limit: 100,
        },
      },
    });
    mockedList.mockResolvedValue([complete]);
    mockedFetch.mockResolvedValueOnce(complete).mockResolvedValue(
      project({
        ...complete,
        state_version: 9,
        compilation: {
          id: 'compilation-1',
          result: {
            sql: 'SELECT SUM(t1.amount) AS total_amount FROM sales AS t1 LIMIT :row_limit',
            parameters: { row_limit: 100 },
            guard: { status: 'PASS' },
          },
        },
        latest_run: {
          id: 'run-1',
          status: 'READY',
          row_count: 1,
          duration_ms: 10,
          columns: ['total_amount'],
          compilation_id: 'compilation-1',
          checks: {
            status: 'PASS',
            schema: 'PASS',
            bounds: 'PASS',
            truncated: false,
            null_cells: 0,
            completeness: 'FULL',
          },
        },
      }),
    );
    const compilation = {
      schema_version: '1',
      status: 'READY',
      snapshot_fingerprint: `sha256:${'b'.repeat(64)}`,
      blocking_issues: [],
      sql: 'SELECT SUM(t1.amount) AS total_amount FROM sales AS t1 LIMIT :row_limit',
      parameters: { row_limit: 100 },
      guard: {
        status: 'PASS',
        dialect: 'postgres',
        statement_count: 1,
        read_only: true,
        tables: ['sales'],
        parameters: ['row_limit'],
      },
    };
    mockedCompile.mockResolvedValue({
      compilation_id: 'compilation-1',
      compilation,
      project: project({
        ...complete,
        state_version: 8,
        compilation: { id: 'compilation-1', result: compilation },
      }),
    });
    mockedPreflight.mockResolvedValue({
      compilation_id: 'compilation-1',
      state_version: 8,
      binding: {
        status: 'BOUND',
        selection: {
          profile: {
            id: 'profile-1',
            name: 'PostgreSQL',
            max_rows: 1000,
            statement_timeout_ms: 5000,
          },
        },
        candidates: [],
      },
    });
    mockedRun.mockResolvedValue({
      run_id: 'run-1',
      status: 'QUEUED',
      state_version: 9,
    });
    mockedPreview.mockResolvedValue({
      run_id: 'run-1',
      columns: ['total_amount'],
      rows: [[42]],
      offset: 0,
      row_count: 1,
      duration_ms: 10,
      result_bytes: 5,
      checks: {
        status: 'PASS',
        schema: 'PASS',
        bounds: 'PASS',
        truncated: false,
        null_cells: 0,
        completeness: 'FULL',
      },
    });
    mockedPreflight.mockResolvedValue({
      compilation_id: 'compilation-1',
      state_version: 8,
      binding: {
        status: 'BOUND',
        selection: {
          profile: {
            id: 'profile-1',
            name: 'PostgreSQL',
            max_rows: 1000,
            statement_timeout_ms: 5000,
          },
        },
        candidates: [],
      },
    });
    mockedRun.mockResolvedValue({
      run_id: 'run-1',
      status: 'QUEUED',
      state_version: 9,
    });
    mockedPreview.mockResolvedValue({
      run_id: 'run-1',
      columns: ['total_amount'],
      rows: [[42]],
      offset: 0,
      row_count: 1,
      duration_ms: 10,
      result_bytes: 5,
      checks: {
        status: 'PASS',
        schema: 'PASS',
        bounds: 'PASS',
        truncated: false,
        null_cells: 0,
        completeness: 'FULL',
      },
    });

    render(<SqlAgentWorkbench />);

    expect(await screen.findByTestId('sql-agent-complete')).toHaveTextContent(
      'SQL проверен. Данные ещё не получены.',
    );
    expect(screen.queryByTestId('sql-result-table')).not.toBeInTheDocument();
    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: 'Выполнить запрос' }),
      ).toBeEnabled(),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Выполнить запрос' }));
    expect(await screen.findByTestId('sql-result-table')).toHaveTextContent(
      '42',
    );
    expect(mockedRun).toHaveBeenCalledWith(
      'project-1',
      8,
      expect.any(String),
      null,
    );
    fireEvent.click(
      screen.getByText('SQL и параметры', { selector: 'summary' }),
    );
    expect(await screen.findByText(/SELECT SUM/)).toBeInTheDocument();
    expect(
      screen.getByText('Read-only · проверка пройдена'),
    ).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole('button', { name: 'Удалить данные этого запуска' }),
    );
    expect(screen.getByText('Удалить полученные данные?')).toBeInTheDocument();
    expect(mockedCancel).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Оставить данные' }));
    expect(mockedCancel).not.toHaveBeenCalled();
  });

  it('saves a checked SQL document without suggesting that rows were returned', async () => {
    const specification = project({
      stage: 'COMPLETE',
      next_agent: null,
      state_version: 4,
      artifact_ids: {
        requirements: 'artifact-r',
        schema: 'artifact-s',
        query: 'artifact-q',
      },
      compilation: {
        id: 'compilation-1',
        result: {
          sql: 'SELECT 1 AS value LIMIT :row_limit',
          parameters: { row_limit: 1 },
          guard: { status: 'PASS' },
        },
      },
      latest_run: null,
      document: null,
    });
    const document = {
      id: 'document-1',
      revision: 1,
      payload: {
        requirements: 'Покажи значение',
        execution_status: 'NOT_EXECUTED',
        python_status: 'NOT_EXECUTED',
        row_count: null,
      },
    };
    const saved = project({
      ...specification,
      state_version: 5,
      document,
    });
    mockedList.mockResolvedValue([specification]);
    mockedFetch.mockResolvedValueOnce(specification).mockResolvedValue(saved);
    mockedPreflight.mockResolvedValue({
      blocker: {
        code: 'SQL_EXECUTION_FORBIDDEN',
        message: 'Нет права запуска',
      },
      binding: { status: 'UNAVAILABLE', candidates: [] },
    });
    mockedComplete.mockResolvedValue({
      document_id: 'document-1',
      revision: 1,
      document: document.payload,
      rows_status: 'NOT_EXECUTED',
      state_version: 5,
    });

    render(<SqlAgentWorkbench />);
    fireEvent.click(
      await screen.findByRole('button', {
        name: 'Сохранить документ без выполнения',
      }),
    );
    await waitFor(() =>
      expect(mockedComplete).toHaveBeenCalledWith(
        'project-1',
        4,
        expect.any(String),
        undefined,
      ),
    );
    expect(
      await screen.findByTestId('sql-completed-document'),
    ).toHaveTextContent('Запрос не выполнялся');
    expect(screen.queryByText(/Получено .* строк/)).not.toBeInTheDocument();
  });

  it('generates a conclusion only on request and requires confirmation', async () => {
    const ready = project({
      stage: 'COMPLETE',
      next_agent: null,
      state_version: 2,
      compilation: {
        id: 'compilation-1',
        result: {
          sql: 'SELECT value AS value FROM public.sales LIMIT :row_limit',
          parameters: { row_limit: 100 },
          guard: { status: 'PASS' },
        },
      },
      latest_run: {
        id: 'run-1',
        status: 'READY',
        row_count: 1,
        duration_ms: 10,
        columns: ['value'],
        compilation_id: 'compilation-1',
        checks: {
          status: 'PASS',
          schema: 'PASS',
          bounds: 'PASS',
          completeness: 'FULL',
        },
      },
    });
    let current = ready;
    mockedList.mockResolvedValue([ready]);
    mockedFetch.mockImplementation(async () => current);
    mockedPreflight.mockResolvedValue({
      binding: {
        status: 'BOUND',
        selection: {
          profile: {
            name: 'PostgreSQL',
            max_rows: 100,
            statement_timeout_ms: 5000,
          },
        },
      },
    });
    mockedPreview.mockResolvedValue({
      run_id: 'run-1',
      columns: ['value'],
      rows: [[42]],
      offset: 0,
      row_count: 1,
      duration_ms: 10,
    });
    mockedProposeConclusion.mockImplementation(async () => {
      current = project({
        ...ready,
        state_version: 3,
        latest_conclusion: {
          id: 'conclusion-1',
          payload: {
            status: 'DRAFT',
            source_run_id: 'run-1',
            text: 'Значение 42.',
            citations: [{ row_index: 0, column: 'value' }],
          },
        },
      });
      return {
        proposal_id: 'conclusion-1',
        text: 'Значение 42.',
        citations: [{ row_index: 0, column: 'value' }],
        state_version: 3,
      };
    });
    mockedConfirmConclusion.mockResolvedValue({
      conclusion_id: 'conclusion-2',
      text: 'Значение 42.',
      state_version: 4,
    });
    render(<SqlAgentWorkbench />);
    expect(await screen.findByTestId('sql-result-table')).toHaveTextContent(
      '42',
    );
    expect(mockedProposeConclusion).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Сформировать вывод' }));
    expect(await screen.findByLabelText('Предложенный вывод')).toHaveValue(
      'Значение 42.',
    );
    fireEvent.click(screen.getByRole('button', { name: 'Подтвердить вывод' }));
    await waitFor(() =>
      expect(mockedConfirmConclusion).toHaveBeenCalledWith(
        'project-1',
        'run-1',
        3,
        expect.any(String),
        'conclusion-1',
        'Значение 42.',
      ),
    );
  });
});

describe('persisted project connection', () => {
  const configure = configureBusinessDocumentSqlProjectConnection as jest.Mock;
  const probe = checkBusinessDocumentSqlProjectConnection as jest.Mock;
  const profiles = [
    {
      id: 'profile-1',
      name: 'QA A',
      version: 1,
      max_rows: 1000,
      statement_timeout_ms: 5000,
    },
    {
      id: 'profile-2',
      name: 'QA B',
      version: 2,
      max_rows: 1000,
      statement_timeout_ms: 5000,
    },
  ];
  const bound = (profileId: string | null = null) => ({
    compilation_id: 'compilation-1',
    state_version: 7,
    binding: {
      status: 'BOUND',
      candidates: profiles,
      selection: {
        profile: profiles.find((item) => item.id === profileId) || profiles[0],
      },
    },
  });
  const completed = (patch: Record<string, unknown> = {}) =>
    project({
      stage: 'COMPLETE',
      next_agent: null,
      state_version: 7,
      artifact_ids: { requirements: 'r1', schema: 's1', query: 'q1' },
      execution_profile_id: null,
      capabilities: {
        edit: true,
        execute: true,
        requirements_agent: true,
        schema_agent: true,
        query_agent: true,
      },
      compilation: {
        id: 'compilation-1',
        result: {
          schema_version: '1',
          status: 'READY',
          sql: 'SELECT value FROM public.sales LIMIT :row_limit',
          parameters: { row_limit: 100 },
          guard: { status: 'PASS' },
          blocking_issues: [],
          output_columns: ['value'],
        },
      },
      ...patch,
    });
  const healthy = (profileId = 'profile-1') => ({
    schema_version: '1',
    project_id: 'project-1',
    state_version: 7,
    status: 'AVAILABLE',
    binding: bound(profileId).binding,
    query_checked: false,
    checked_at: Date.now(),
    duration_ms: 1,
  });
  const deferred = <T,>() => {
    let resolve!: (value: T) => void;
    const promise = new Promise<T>((done) => {
      resolve = done;
    });
    return { promise, resolve };
  };
  function setup(patch: Record<string, unknown> = {}) {
    const store = { current: completed(patch) };
    mockedList.mockImplementation(async () => [
      store.current,
      completed({
        id: 'project-2',
        title: 'Другой проект',
        execution_profile_id: 'profile-1',
      }),
    ]);
    mockedFetch.mockImplementation(async (id: string) =>
      id === 'project-1'
        ? store.current
        : completed({
            id,
            title: 'Другой проект',
            execution_profile_id: 'profile-1',
          }),
    );
    configure.mockImplementation(async (_id, _version, _key, profileId) => {
      store.current = {
        ...store.current,
        execution_profile_id: profileId,
        state_version: store.current.state_version + 1,
      };
      return store.current;
    });
    return store;
  }
  beforeEach(() => {
    jest.resetAllMocks();
    probe.mockResolvedValue(healthy());
    mockedWorkspace.mockResolvedValue({
      query_artifact_id: null,
      document_artifact_id: null,
      draft: null,
      threads: [],
      blocks: [],
      sql: '',
    });
    mockedPreflight.mockImplementation(async (_id, profileId) =>
      bound(profileId),
    );
    mockedPreview.mockResolvedValue({
      run_id: 'old-run',
      columns: ['value'],
      rows: [[42]],
      row_count: 1,
      offset: 0,
      checks: { status: 'PASS', completeness: 'FULL' },
    });
  });

  it('retries a failed SQL preflight explicitly without changing the selected source', async () => {
    setup({ execution_profile_id: 'profile-2' });
    mockedPreflight.mockRejectedValueOnce(new Error('Источник не ответил'));
    render(<SqlAgentWorkbench />);
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Источник не ответил',
    );
    expect(screen.getByLabelText('Источник данных')).toHaveValue('profile-2');
    expect(
      screen.queryByText('Проверяем запрос и доступ к источнику…'),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'Выполнить запрос' }),
    ).toBeDisabled();
    expect(mockedPreflight).toHaveBeenCalledTimes(1);
    fireEvent.click(
      screen.getByRole('button', { name: 'Повторить проверку запроса' }),
    );
    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: 'Выполнить запрос' }),
      ).toBeEnabled(),
    );
    expect(mockedPreflight).toHaveBeenCalledTimes(2);
    expect(configure).not.toHaveBeenCalled();
    expect(probe).not.toHaveBeenCalled();
    expect(mockedRun).not.toHaveBeenCalled();
  });

  it('explains a stale catalog without running or changing the source', async () => {
    setup({ execution_profile_id: 'profile-1' });
    mockedPreflight.mockRejectedValueOnce(
      Object.assign(
        new Error(
          'Catalog schema changed or is unavailable; refresh the selected tables',
        ),
        { code: 'SQL_SCHEMA_STALE' },
      ),
    );
    render(<SqlAgentWorkbench />);
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Обновите выбранные таблицы в разделе «Данные», затем повторите проверку SQL.',
    );
    expect(
      screen.getByRole('button', { name: 'Выполнить запрос' }),
    ).toBeDisabled();
    expect(mockedRun).not.toHaveBeenCalled();
    expect(configure).not.toHaveBeenCalled();
  });

  it('keeps connection availability separate from failed SQL verification', async () => {
    setup({ execution_profile_id: 'profile-1' });
    mockedPreflight.mockRejectedValueOnce(
      new Error('В SQL отсутствует колонка'),
    );
    render(<SqlAgentWorkbench />);
    await screen.findByText('В SQL отсутствует колонка');
    fireEvent.click(
      screen.getByRole('button', { name: 'Проверить подключение' }),
    );
    expect(
      await screen.findByText(/Подключение доступно: QA A/),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'Выполнить запрос' }),
    ).toBeDisabled();
    expect(mockedPreflight).toHaveBeenCalledTimes(1);
    expect(probe).toHaveBeenCalledWith(
      'project-1',
      'profile-1',
      expect.any(AbortSignal),
    );
    expect(configure).not.toHaveBeenCalled();
    expect(mockedRun).not.toHaveBeenCalled();
  });

  it('allows connection checks when compilation fails without asserting SQL passed', async () => {
    setup({ execution_profile_id: 'profile-1', compilation: null });
    mockedCompile.mockRejectedValueOnce(new Error('Неверный SQL'));
    render(<SqlAgentWorkbench />);
    await screen.findByText('Неверный SQL');
    fireEvent.click(
      screen.getByRole('button', { name: 'Проверить подключение' }),
    );
    expect(
      await screen.findByText(/Подключение доступно: QA A/),
    ).toBeInTheDocument();
    expect(
      screen.queryByText('SQL проверен. Данные ещё не получены.'),
    ).not.toBeInTheDocument();
    expect(mockedPreflight).not.toHaveBeenCalled();
    expect(mockedRun).not.toHaveBeenCalled();
  });

  it('ignores a late connection response after source selection changes', async () => {
    setup({ execution_profile_id: 'profile-1' });
    const old = deferred<ReturnType<typeof healthy>>();
    probe.mockReturnValueOnce(old.promise);
    render(<SqlAgentWorkbench />);
    await screen.findByText('Выбран профиль: QA A');
    fireEvent.click(
      screen.getByRole('button', { name: 'Проверить подключение' }),
    );
    const signal = probe.mock.calls[0][2] as AbortSignal;
    fireEvent.change(screen.getByLabelText('Источник данных'), {
      target: { value: 'profile-2' },
    });
    expect(signal.aborted).toBe(true);
    await act(async () => old.resolve(healthy()));
    expect(
      screen.queryByText(/Подключение доступно: QA A/),
    ).not.toBeInTheDocument();
    expect(screen.getByLabelText('Источник данных')).toHaveValue('profile-2');
  });

  it('requires an explicit retry after a connection error and blocks Run meanwhile', async () => {
    setup({ execution_profile_id: 'profile-1' });
    probe.mockRejectedValueOnce(new Error('Сбой TLS источника'));
    render(<SqlAgentWorkbench />);
    await screen.findByText('Выбран профиль: QA A');
    fireEvent.click(
      screen.getByRole('button', { name: 'Проверить подключение' }),
    );
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Сбой TLS источника',
    );
    expect(
      screen.getByRole('button', { name: 'Выполнить запрос' }),
    ).toBeDisabled();
    expect(probe).toHaveBeenCalledTimes(1);
    fireEvent.click(
      screen.getByRole('button', { name: 'Проверить подключение' }),
    );
    await screen.findByText(/Подключение доступно: QA A/);
    expect(probe).toHaveBeenCalledTimes(2);
    expect(mockedRun).not.toHaveBeenCalled();
  });

  it('keeps the selector after binding and persists a choice across reload without running SQL', async () => {
    const store = setup();
    const page = render(<SqlAgentWorkbench />);
    const source = await screen.findByLabelText('Источник данных');
    await screen.findByRole('option', { name: 'QA B' });
    fireEvent.change(source, { target: { value: 'profile-2' } });
    await screen.findByText('Выбран профиль: QA B');
    expect(source).toHaveValue('profile-2');
    expect(
      screen.getByRole('button', { name: 'Выполнить запрос' }),
    ).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить источник' }));
    await waitFor(() =>
      expect(configure).toHaveBeenCalledWith(
        'project-1',
        7,
        expect.any(String),
        'profile-2',
      ),
    );
    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: 'Сохранить источник' }),
      ).toBeDisabled(),
    );
    expect(store.current.execution_profile_id).toBe('profile-2');
    page.unmount();
    render(<SqlAgentWorkbench />);
    expect(await screen.findByLabelText('Источник данных')).toHaveValue(
      'profile-2',
    );
    expect(configure).toHaveBeenCalledTimes(1);
    expect(mockedRun).not.toHaveBeenCalled();
    expect(mockedCompile).not.toHaveBeenCalled();
  });

  it('uses the same idempotency key when an uncertain save is retried', async () => {
    setup();
    configure.mockRejectedValueOnce(new Error('Соединение прервано'));
    render(<SqlAgentWorkbench />);
    await screen.findByRole('option', { name: 'QA B' });
    fireEvent.change(screen.getByLabelText('Источник данных'), {
      target: { value: 'profile-2' },
    });
    await screen.findByText('Выбран профиль: QA B');
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить источник' }));
    await screen.findByText('Соединение прервано');
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить источник' }));
    await waitFor(() => expect(configure).toHaveBeenCalledTimes(2));
    expect(configure.mock.calls[0]).toEqual(configure.mock.calls[1]);
  });

  it('ignores an older preflight after the user chooses another profile', async () => {
    setup();
    const late = deferred<ReturnType<typeof bound>>();
    let oldSignal!: AbortSignal;
    mockedPreflight.mockImplementation(async (_id, profileId, signal) => {
      if (profileId === 'profile-2') {
        oldSignal = signal;
        return late.promise;
      }
      return bound(profileId);
    });
    render(<SqlAgentWorkbench />);
    await screen.findByRole('option', { name: 'QA B' });
    fireEvent.change(screen.getByLabelText('Источник данных'), {
      target: { value: 'profile-2' },
    });
    await waitFor(() => expect(oldSignal).toBeDefined());
    fireEvent.change(screen.getByLabelText('Источник данных'), {
      target: { value: 'profile-1' },
    });
    await screen.findByText('Выбран профиль: QA A');
    await act(async () => late.resolve(bound('profile-2')));
    expect(oldSignal.aborted).toBe(true);
    expect(screen.getByText('Выбран профиль: QA A')).toBeInTheDocument();
  });

  it('cannot configure or execute when a reader receives permissive binding data', async () => {
    setup({ capabilities: { edit: false, execute: false } });
    render(<SqlAgentWorkbench />);
    await screen.findByText(
      'Выбор подключения доступен пользователям с правом выполнения запросов.',
    );
    expect(screen.queryByLabelText('Источник данных')).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Сохранить источник' }),
    ).not.toBeInTheDocument();
    expect(
      await screen.findByRole('button', { name: 'Выполнить запрос' }),
    ).toBeDisabled();
    expect(configure).not.toHaveBeenCalled();
  });

  it.each(['Original source', null])(
    'keeps the old result provenance (%s) when the next source is saved',
    async (sourceName) => {
      const ready = {
        id: 'old-run',
        status: 'READY',
        row_count: 1,
        columns: ['value'],
        duration_ms: 1,
        compilation_id: 'compilation-1',
        profile_id: 'profile-1',
        profile_version: 3,
        profile_name: sourceName,
      };
      setup({
        execution_profile_id: 'profile-2',
        latest_run: ready,
        runs: [ready],
      });
      render(<SqlAgentWorkbench />);
      await screen.findByTestId('sql-result-table');
      await screen.findByRole('option', { name: 'QA A' });
      fireEvent.change(screen.getByLabelText('Источник данных'), {
        target: { value: 'profile-1' },
      });
      await screen.findByText('Выбран профиль: QA A');
      fireEvent.click(
        screen.getByRole('button', { name: 'Сохранить источник' }),
      );
      await waitFor(() => expect(configure).toHaveBeenCalledTimes(1));
      expect(
        await screen.findByText(
          `Источник выбранного запуска: ${sourceName || 'имя не записано'} · версия профиля 3`,
        ),
      ).toBeInTheDocument();
      expect(await screen.findByTestId('sql-result-table')).toHaveTextContent(
        '42',
      );
      expect(mockedRun).not.toHaveBeenCalled();
    },
  );

  it('keeps a reader result view usable without exposing result mutations', async () => {
    const ready = {
      id: 'old-run',
      status: 'READY',
      row_count: 1,
      columns: ['value'],
      duration_ms: 1,
      compilation_id: 'compilation-1',
      profile_id: 'profile-1',
      profile_version: 3,
    };
    setup({
      latest_run: ready,
      runs: [ready],
      capabilities: {
        edit: false,
        execute: false,
        python_agent: true,
        result_agent: true,
      },
      artifacts: {
        requirements: {},
        query: {},
        schema: {
          accepted_schema: [{ entity_id: 'sales' }],
          schema_snapshot: {
            requirements: [
              {
                selected_table: {
                  id: 'sales',
                  name: 'Sales',
                  columns: [{ id: 'value', name: 'value' }],
                },
              },
            ],
          },
        },
      },
    });
    render(<SqlAgentWorkbench />);
    expect(await screen.findByTestId('sql-result-table')).toHaveTextContent(
      '42',
    );
    for (const name of [
      'Сохранить документ и удалить строки',
      'Удалить данные этого запуска',
      'Сформировать вывод',
      'Применить Python',
      'Преобразовать в Python',
      'Дополнить данные',
      'Редактировать финальный SQL',
      'Изменить задачу',
    ]) {
      expect(screen.queryByRole('button', { name })).not.toBeInTheDocument();
    }
    fireEvent.click(
      screen.getByText('SQL и параметры', { selector: 'summary' }),
    );
    expect(
      await screen.findByText(
        'SELECT value FROM public.sales LIMIT :row_limit',
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Копировать' })).toBeEnabled();
    expect(mockedComplete).not.toHaveBeenCalled();
    expect(mockedCancel).not.toHaveBeenCalled();
    expect(mockedProposeConclusion).not.toHaveBeenCalled();
  });

  it('does not offer a reader to save a document without execution', async () => {
    setup({ capabilities: { edit: false, execute: false } });
    render(<SqlAgentWorkbench />);
    await screen.findByTestId('sql-agent-complete');
    expect(
      screen.queryByRole('button', {
        name: 'Сохранить документ без выполнения',
      }),
    ).not.toBeInTheDocument();
    expect(mockedComplete).not.toHaveBeenCalled();
  });

  it('does not automatically create a compilation when an owner now has reader access', async () => {
    setup({ compilation: null, capabilities: { edit: false, execute: false } });
    render(<SqlAgentWorkbench />);
    await screen.findByTestId('sql-agent-complete');
    expect(mockedCompile).not.toHaveBeenCalled();
    expect(mockedRun).not.toHaveBeenCalled();
  });

  it('preserves document and Python editing for an editor without database execution rights', async () => {
    const ready = {
      id: 'old-run',
      status: 'READY',
      row_count: 1,
      columns: ['value'],
      duration_ms: 1,
      compilation_id: 'compilation-1',
      profile_id: 'profile-1',
      profile_version: 3,
    };
    setup({
      latest_run: ready,
      runs: [ready],
      capabilities: { edit: true, execute: false, python_agent: true },
      artifacts: {
        requirements: {},
        query: {},
        schema: {
          accepted_schema: [{ entity_id: 'sales' }],
          schema_snapshot: {
            requirements: [
              {
                selected_table: {
                  id: 'sales',
                  name: 'Sales',
                  columns: [{ id: 'value', name: 'value' }],
                },
              },
            ],
          },
        },
      },
    });
    render(<SqlAgentWorkbench />);
    expect(await screen.findByTestId('sql-result-table')).toHaveTextContent(
      '42',
    );
    expect(
      screen.getByRole('button', {
        name: 'Сохранить документ и удалить строки',
      }),
    ).toBeEnabled();
    expect(
      screen.getByRole('button', { name: 'Сформировать вывод' }),
    ).toBeEnabled();
    expect(
      screen.getByRole('button', { name: 'Преобразовать в Python' }),
    ).toBeEnabled();
    expect(
      screen.getByRole('button', { name: 'Редактировать финальный SQL' }),
    ).toBeEnabled();
    expect(
      screen.queryByRole('button', { name: 'Дополнить данные' }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'Выполнить запрос' }),
    ).toBeDisabled();
    expect(mockedRun).not.toHaveBeenCalled();
  });

  it('places the immutable result first and confirms clearing every ready root and derived result', async () => {
    const ready = {
      id: 'old-run',
      status: 'READY',
      row_count: 1,
      columns: ['value'],
      duration_ms: 1,
      compilation_id: 'compilation-1',
      profile_id: 'old-profile',
      profile_name: 'Original source',
      profile_version: 3,
    };
    setup({
      execution_profile_id: 'profile-2',
      latest_run: ready,
      runs: [
        ready,
        { ...ready, id: 'other-ready' },
        { ...ready, id: 'purged', status: 'PURGED' },
      ],
      derived_runs: [
        {
          id: 'py',
          kind: 'PYTHON',
          source_run_id: 'other-ready',
          status: 'READY',
        },
        {
          id: 'lookup',
          kind: 'LOOKUP',
          source_run_id: 'old-run',
          status: 'READY',
        },
        {
          id: 'removed-py',
          kind: 'PYTHON',
          source_run_id: 'old-run',
          status: 'PURGED',
        },
      ],
    });
    render(<SqlAgentWorkbench />);
    const result = await screen.findByTestId('sql-result-table');
    expect(screen.getByTestId('sql-run-identity')).toHaveTextContent(
      'Original source · версия профиля 3',
    );
    expect(screen.getByTestId('sql-run-identity')).not.toHaveTextContent(
      'QA B',
    );
    const context = screen
      .getByText('Сущности и связи', { selector: 'summary' })
      .closest('details')!;
    expect(
      result.compareDocumentPosition(context) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(context.open).toBe(false);
    expect(
      screen
        .getByText('История запусков', { selector: 'summary' })
        .closest('details')!.open,
    ).toBe(false);
    expect(screen.getByTestId('sql-completion-warning')).toHaveTextContent(
      'Запусков SQL: 2; производных результатов: 2',
    );
    fireEvent.click(
      screen.getByRole('button', {
        name: 'Сохранить документ и удалить строки',
      }),
    );
    const dialog = screen.getByRole('alertdialog');
    expect(dialog).toHaveTextContent('Это затронет и другие запуски');
    expect(dialog).toHaveTextContent(
      'Запусков SQL: 2; производных результатов: 2',
    );
    expect(mockedComplete).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Отмена' }));
    expect(mockedComplete).not.toHaveBeenCalled();
    expect(screen.getByTestId('sql-result-table')).toHaveTextContent('42');
    fireEvent.click(
      screen.getByRole('button', {
        name: 'Сохранить документ и удалить строки',
      }),
    );
    fireEvent.click(
      within(screen.getByRole('alertdialog')).getByRole('button', {
        name: 'Сохранить и удалить строки',
      }),
    );
    await waitFor(() => expect(mockedComplete).toHaveBeenCalledTimes(1));
    expect(mockedComplete).toHaveBeenCalledWith(
      'project-1',
      7,
      expect.any(String),
      'old-run',
    );
    expect(mockedRun).not.toHaveBeenCalled();
  });

  it('closes a completion confirmation when the retained rows expire', async () => {
    const start = Date.now();
    let now = start;
    const clock = jest.spyOn(Date, 'now').mockImplementation(() => now);
    const ready = {
      id: 'old-run',
      status: 'READY',
      expires_at: start + 60_000,
      row_count: 1,
      columns: ['value'],
      duration_ms: 1,
      compilation_id: 'compilation-1',
    };
    setup({ latest_run: ready, runs: [ready] });
    const rendered = render(<SqlAgentWorkbench />);
    try {
      await screen.findByTestId('sql-result-table');
      fireEvent.click(
        screen.getByRole('button', {
          name: 'Сохранить документ и удалить строки',
        }),
      );
      expect(screen.getByRole('alertdialog')).toBeInTheDocument();
      act(() => {
        now = start + 60_000;
        window.dispatchEvent(new Event('focus'));
      });
      await waitFor(() =>
        expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument(),
      );
      expect(screen.queryByTestId('sql-result-table')).not.toBeInTheDocument();
      expect(mockedComplete).not.toHaveBeenCalled();
    } finally {
      rendered.unmount();
      clock.mockRestore();
    }
  });

  it('does not offer new or copied projects without create capability', async () => {
    setup({ capabilities: { edit: true, execute: false, copy: true } });
    render(<SqlAgentWorkbench canCreate={false} />);
    await screen.findByTestId('sql-agent-complete');
    expect(
      screen.queryByRole('button', { name: 'Новый запрос' }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Создать копию' }),
    ).not.toBeInTheDocument();
    expect(mockedCreate).not.toHaveBeenCalled();
    expect(mockedRun).not.toHaveBeenCalled();
  });

  it.each(['compilation-1', 'previous-compilation'])(
    'permits another edit and run while retaining a completed result from %s',
    async (compilationId) => {
      const ready = {
        id: 'old-run',
        status: 'READY',
        row_count: 1,
        columns: ['value'],
        duration_ms: 1,
        compilation_id: compilationId,
        profile_id: 'profile-1',
        profile_version: 3,
        profile_name: 'Original source',
      };
      setup({
        execution_profile_id: 'profile-1',
        latest_run: ready,
        runs: [ready],
      });
      render(<SqlAgentWorkbench />);
      expect(await screen.findByTestId('sql-result-table')).toHaveTextContent(
        '42',
      );
      expect(screen.getByTestId('guided-sql-form')).toBeInTheDocument();
      expect(Boolean(screen.queryByTestId('sql-historical-result'))).toBe(
        compilationId !== 'compilation-1',
      );
      if (compilationId !== 'compilation-1') {
        expect(
          screen.getByRole('button', {
            name: 'Сохранить документ и удалить строки',
          }),
        ).toBeDisabled();
      }
      await waitFor(() =>
        expect(
          screen.getByRole('button', { name: 'Выполнить запрос' }),
        ).toBeEnabled(),
      );
      expect(mockedRun).not.toHaveBeenCalled();
      expect(
        screen.getByText(
          'Источник выбранного запуска: Original source · версия профиля 3',
        ),
      ).toBeInTheDocument();
    },
  );

  it.each(['QUEUED', 'RUNNING', 'CANCEL_REQUESTED', 'UNKNOWN'])(
    'keeps editing and new execution unavailable while another retained run is %s',
    async (status) => {
      const ready = {
        id: 'old-run',
        status: 'READY',
        row_count: 1,
        columns: ['value'],
        duration_ms: 1,
        compilation_id: 'compilation-1',
        profile_id: 'profile-1',
        profile_version: 3,
      };
      setup({
        execution_profile_id: 'profile-1',
        latest_run: ready,
        runs: [ready, { ...ready, id: 'new-run', status }],
      });
      render(<SqlAgentWorkbench />);
      expect(await screen.findByTestId('sql-result-table')).toHaveTextContent(
        '42',
      );
      expect(screen.queryByTestId('guided-sql-form')).not.toBeInTheDocument();
      expect(
        screen.queryByRole('button', { name: 'Выполнить запрос' }),
      ).not.toBeInTheDocument();
      expect(
        screen.getByRole('button', { name: 'Сохранить источник' }),
      ).toBeDisabled();
      expect(mockedRun).not.toHaveBeenCalled();
    },
  );

  it.each([
    ['UNKNOWN', 'Исход выполнения неизвестен'],
    ['SUCCEEDED', 'SQL завершён, строки недоступны'],
    ['CANCELLED', 'Выполнение отменено'],
  ])(
    'shows the confirmed execution meaning for %s',
    async (status, heading) => {
      const run = {
        id: 'run-1',
        status,
        row_count: 0,
        columns: [],
        compilation_id: 'compilation-1',
        profile_id: 'profile-1',
        profile_version: 3,
      };
      setup({
        execution_profile_id: 'profile-1',
        latest_run: run,
        runs: [run],
      });
      render(<SqlAgentWorkbench />);
      expect(
        await screen.findByRole('heading', { name: heading }),
      ).toBeInTheDocument();
      expect(mockedRun).not.toHaveBeenCalled();
      if (status === 'UNKNOWN')
        expect(
          screen.getByText(/Повторная отправка заблокирована/),
        ).toBeInTheDocument();
    },
  );

  it('does not reopen a project when its connection save finishes after navigation', async () => {
    const store = setup();
    const late = deferred<ReturnType<typeof completed>>();
    configure.mockReturnValueOnce(late.promise);
    render(<SqlAgentWorkbench />);
    await screen.findByRole('option', { name: 'QA B' });
    fireEvent.change(screen.getByLabelText('Источник данных'), {
      target: { value: 'profile-2' },
    });
    await screen.findByText('Выбран профиль: QA B');
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить источник' }));
    await waitFor(() => expect(configure).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole('button', { name: /Другой проект/ }));
    await screen.findByRole('heading', { name: 'Другой проект' });
    await act(async () =>
      late.resolve({
        ...store.current,
        execution_profile_id: 'profile-2',
        state_version: 8,
      }),
    );
    expect(
      screen.getByRole('heading', { name: 'Другой проект' }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText('Источник данных')).toHaveValue('profile-1');
    expect(
      mockedFetch.mock.calls.filter(([id]) => id === 'project-1'),
    ).toHaveLength(1);
  });

  it('aborts preflight on pagehide and renews it after a persisted pageshow', async () => {
    setup();
    const late = deferred<ReturnType<typeof bound>>();
    let oldSignal!: AbortSignal;
    mockedPreflight.mockImplementationOnce((_id, _profileId, signal) => {
      oldSignal = signal;
      return late.promise;
    });
    render(<SqlAgentWorkbench />);
    await screen.findByLabelText('Источник данных');
    await waitFor(() => expect(oldSignal).toBeDefined());
    act(() => window.dispatchEvent(new Event('pagehide')));
    expect(oldSignal.aborted).toBe(true);
    const restored = new Event('pageshow');
    Object.defineProperty(restored, 'persisted', { value: true });
    act(() => window.dispatchEvent(restored));
    await screen.findByText('Выбран профиль: QA A');
    await act(async () => late.resolve(bound('profile-2')));
    expect(screen.getByText('Выбран профиль: QA A')).toBeInTheDocument();
    expect(mockedPreflight.mock.calls.at(-1)[2].aborted).toBe(false);
  });
});

describe('SQL project registry', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockedWorkspace.mockResolvedValue({
      query_artifact_id: null,
      document_artifact_id: null,
      draft: null,
      threads: [],
      blocks: [],
      sql: '',
    });
  });

  it('reuses the copy command key after an uncertain network failure', async () => {
    const first = project();
    mockedList.mockResolvedValue([first]);
    mockedFetch.mockResolvedValue(first);
    (copyBusinessDocumentSqlAgentProject as jest.Mock)
      .mockRejectedValueOnce(new Error('Ответ потерян'))
      .mockResolvedValueOnce(
        project({ id: 'copy-retried', title: 'Копия после повтора' }),
      );
    render(<SqlAgentWorkbench />);
    await screen.findByRole('heading', { name: first.title });
    fireEvent.click(screen.getByRole('button', { name: 'Создать копию' }));
    await screen.findByText('Ответ потерян');
    fireEvent.click(screen.getByRole('button', { name: 'Создать копию' }));
    await screen.findByRole('heading', { name: 'Копия после повтора' });
    const calls = (copyBusinessDocumentSqlAgentProject as jest.Mock).mock.calls;
    expect(calls).toHaveLength(2);
    expect(calls[1]).toEqual(calls[0]);
  });

  it('filters titles literally without selecting or changing a project', async () => {
    const first = project();
    const second = project({ id: 'project-2', title: 'Остатки 50%_дня' });
    mockedList.mockResolvedValue([first, second]);
    mockedFetch.mockResolvedValue(first);
    render(<SqlAgentWorkbench />);
    await screen.findByRole('heading', { name: first.title });
    fireEvent.change(screen.getByRole('textbox', { name: 'Поиск проектов' }), {
      target: { value: '50%_ДНЯ' },
    });
    expect(
      screen.getByRole('button', { name: /Остатки 50%_дня/ }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: /Продажи по регионам/ }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole('heading', { name: first.title }),
    ).toBeInTheDocument();
    fireEvent.change(screen.getByRole('textbox', { name: 'Поиск проектов' }), {
      target: { value: '' },
    });
    expect(
      screen.getByRole('button', { name: /Продажи по регионам/ }),
    ).toBeInTheDocument();
    expect(mockedFetch).toHaveBeenCalledTimes(1);
  });

  it('saves a renamed title with the current version and keeps the question', async () => {
    const first = project();
    mockedList.mockResolvedValue([first]);
    mockedFetch.mockResolvedValue(first);
    (updateBusinessDocumentSqlAgentProject as jest.Mock).mockResolvedValue(
      project({ title: 'Новый отчёт', state_version: 2 }),
    );
    render(<SqlAgentWorkbench />);
    await screen.findByRole('heading', { name: first.title });
    fireEvent.click(screen.getByRole('button', { name: 'Переименовать' }));
    fireEvent.change(
      screen.getByRole('textbox', { name: 'Название проекта' }),
      { target: { value: 'Новый отчёт' } },
    );
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить название' }));
    await screen.findByRole('heading', { name: 'Новый отчёт' });
    expect(updateBusinessDocumentSqlAgentProject).toHaveBeenCalledWith(
      first.id,
      1,
      expect.any(String),
      { title: 'Новый отчёт' },
    );
    expect(
      document.querySelector('.sql-question-summary summary'),
    ).toHaveTextContent(first.source_request);
    expect(mockedRequest).not.toHaveBeenCalled();
  });

  it('archives, reads and restores with explicit versioned commands', async () => {
    const first = project();
    const archived = project({
      archived_at: 100,
      state_version: 2,
      capabilities: {
        ...first.capabilities,
        edit: false,
        execute: false,
        manage: true,
        copy: true,
      },
    });
    mockedList.mockImplementation((options) =>
      Promise.resolve(options?.archived ? [archived] : [first]),
    );
    mockedFetch.mockResolvedValue(first);
    (updateBusinessDocumentSqlAgentProject as jest.Mock)
      .mockResolvedValueOnce(archived)
      .mockResolvedValueOnce(project({ state_version: 3 }));
    render(<SqlAgentWorkbench />);
    await screen.findByRole('heading', { name: first.title });
    fireEvent.click(screen.getByRole('button', { name: 'В архив' }));
    await screen.findByText(
      'Проект в архиве. Для изменения и выполнения восстановите его.',
    );
    expect(
      screen.queryByRole('button', { name: 'Изменить задачу' }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Переименовать' }),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('checkbox', { name: 'Показать архив' }));
    await waitFor(() =>
      expect(mockedList).toHaveBeenCalledWith({ archived: true }),
    );
    fireEvent.click(
      screen.getByRole('button', { name: 'Восстановить из архива' }),
    );
    await screen.findByRole('button', { name: 'Переименовать' });
    expect(updateBusinessDocumentSqlAgentProject).toHaveBeenLastCalledWith(
      first.id,
      2,
      expect.any(String),
      { archived: false },
    );
  });

  it('opens an independent copy and updates the direct link', async () => {
    const first = project();
    const copied = project({ id: 'copy-1', title: 'Копия продаж' });
    mockedList.mockResolvedValue([first]);
    mockedFetch.mockResolvedValue(first);
    (copyBusinessDocumentSqlAgentProject as jest.Mock).mockResolvedValue(
      copied,
    );
    const selected = jest.fn();
    render(
      <SqlAgentWorkbench projectId={first.id} onProjectSelected={selected} />,
    );
    await screen.findByRole('heading', { name: first.title });
    fireEvent.click(screen.getByRole('button', { name: 'Создать копию' }));
    await screen.findByRole('heading', { name: copied.title });
    expect(copyBusinessDocumentSqlAgentProject).toHaveBeenCalledWith(
      first.id,
      1,
      expect.any(String),
    );
    expect(selected).toHaveBeenLastCalledWith(copied.id);
    expect(mockedRequest).not.toHaveBeenCalled();
  });

  it('honors a direct link and clears old private content on a denied route', async () => {
    const first = project();
    mockedList.mockResolvedValue([first]);
    mockedFetch.mockImplementation((id) =>
      id === first.id
        ? Promise.resolve(first)
        : Promise.reject(new Error('Нет доступа')),
    );
    const page = render(<SqlAgentWorkbench projectId={first.id} />);
    await screen.findByRole('heading', { name: first.title });
    page.rerender(<SqlAgentWorkbench projectId="foreign-project" />);
    await screen.findByText('Нет доступа');
    expect(
      screen.queryByRole('heading', { name: first.title }),
    ).not.toBeInTheDocument();
    expect(mockedFetch).toHaveBeenLastCalledWith('foreign-project');
  });

  it('does not apply a late rename response after selecting another project', async () => {
    const first = project();
    const second = project({ id: 'project-2', title: 'Другой проект' });
    mockedList.mockResolvedValue([first, second]);
    mockedFetch.mockImplementation((id) =>
      Promise.resolve(id === second.id ? second : first),
    );
    let finish!: (value: ReturnType<typeof project>) => void;
    (updateBusinessDocumentSqlAgentProject as jest.Mock).mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    render(<SqlAgentWorkbench />);
    await screen.findByRole('heading', { name: first.title });
    fireEvent.click(screen.getByRole('button', { name: 'Переименовать' }));
    fireEvent.change(
      screen.getByRole('textbox', { name: 'Название проекта' }),
      { target: { value: 'Late title' } },
    );
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить название' }));
    fireEvent.click(screen.getByRole('button', { name: /Другой проект/ }));
    await screen.findByRole('heading', { name: second.title });
    await act(async () => {
      finish(project({ title: 'Late title' }));
    });
    expect(
      screen.getByRole('heading', { name: second.title }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole('heading', { name: 'Late title' }),
    ).not.toBeInTheDocument();
  });
});

describe('connection controls before query acceptance', () => {
  it('shows the connection panel after schema acceptance without starting a SQL check or run', async () => {
    jest.clearAllMocks();
    const preparing = project({
      stage: 'QUERY',
      next_agent: 'QUERY',
      artifact_ids: { requirements: 'req', schema: 'schema', query: null },
      artifacts: { requirements: {}, schema: {}, query: null },
      compilation: null,
    });
    mockedList.mockResolvedValue([preparing]);
    mockedFetch.mockResolvedValue(preparing);
    mockedWorkspace.mockResolvedValue({
      query_artifact_id: null,
      document_artifact_id: null,
      draft: null,
      threads: [],
      blocks: [],
      sql: '',
    });
    render(<SqlAgentWorkbench />);
    expect(
      await screen.findByRole('region', { name: 'Подключение проекта' }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'Проверить подключение' }),
    ).toBeEnabled();
    expect(mockedCompile).not.toHaveBeenCalled();
    expect(mockedPreflight).not.toHaveBeenCalled();
    expect(mockedRun).not.toHaveBeenCalled();
    expect(mockedRequest).not.toHaveBeenCalled();
  });
});
