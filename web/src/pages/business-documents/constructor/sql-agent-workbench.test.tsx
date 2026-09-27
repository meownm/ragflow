import {
  cancelBusinessDocumentSqlRun,
  compileBusinessDocumentSqlProject,
  confirmBusinessDocumentSqlConclusion,
  createBusinessDocumentSqlAgentProject,
  decideBusinessDocumentSqlAgentProposal,
  fetchBusinessDocumentSqlAgentProject,
  listBusinessDocumentSqlAgentProjects,
  preflightBusinessDocumentSqlProject,
  previewBusinessDocumentSqlRun,
  proposeBusinessDocumentSqlConclusion,
  requestBusinessDocumentSqlAgent,
  reviseBusinessDocumentSqlQuestion,
  runBusinessDocumentSqlProject,
} from '@/services/business-document-service';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { SqlAgentWorkbench } from './sql-agent-workbench';

jest.mock('@/services/business-document-service', () => ({
  compileBusinessDocumentSqlProject: jest.fn(),
  createBusinessDocumentSqlAgentProject: jest.fn(),
  decideBusinessDocumentSqlAgentProposal: jest.fn(),
  fetchBusinessDocumentSqlAgentProject: jest.fn(),
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
const mockedCreate = createBusinessDocumentSqlAgentProject as jest.Mock;
const mockedRequest = requestBusinessDocumentSqlAgent as jest.Mock;
const mockedRevise = reviseBusinessDocumentSqlQuestion as jest.Mock;
const mockedDecide = decideBusinessDocumentSqlAgentProposal as jest.Mock;
const mockedCompile = compileBusinessDocumentSqlProject as jest.Mock;
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
    const search = await screen.findByPlaceholderText(
      'запуски импорта глоссария',
    );
    expect(mockedRequest).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId('sql-agent-run-schema'));
    expect(mockedRequest).not.toHaveBeenCalled();
    expect(
      screen.getByText('Опишите, какие данные нужно найти в каталоге.'),
    ).toBeInTheDocument();
    fireEvent.change(search, {
      target: { value: 'запуски импорта глоссария' },
    });
    fireEvent.click(screen.getByTestId('sql-agent-run-schema'));
    await waitFor(() =>
      expect(mockedRequest).toHaveBeenCalledWith(
        'project-1',
        expect.objectContaining({
          kind: 'SCHEMA',
          expected_state_version: 3,
          payload: { locale: 'ru', terms: ['запуски импорта глоссария'] },
        }),
      ),
    );
  });

  it('requires separate acknowledgement of each JOIN and filter before accepting SQL', async () => {
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
  });

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
      expect(mockedPreview).toHaveBeenCalledWith('project-1', 'ready-run'),
    );
    expect(await screen.findByText('42')).toBeInTheDocument();
  });

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
    fireEvent.click(screen.getByRole('button', { name: 'Открыть SQL' }));
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
