import {
  compileBusinessDocumentSqlQuery,
  createBusinessDocumentSqlAgentProject,
  decideBusinessDocumentSqlAgentProposal,
  fetchBusinessDocumentSqlAgentProject,
  listBusinessDocumentSqlAgentProjects,
  requestBusinessDocumentSqlAgent,
} from '@/services/business-document-service';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { SqlAgentWorkbench } from './sql-agent-workbench';

jest.mock('@/services/business-document-service', () => ({
  compileBusinessDocumentSqlQuery: jest.fn(),
  createBusinessDocumentSqlAgentProject: jest.fn(),
  decideBusinessDocumentSqlAgentProposal: jest.fn(),
  fetchBusinessDocumentSqlAgentProject: jest.fn(),
  listBusinessDocumentSqlAgentProjects: jest.fn(),
  loadBusinessDocumentSqlSchemaEntities: jest.fn(),
  requestBusinessDocumentSqlAgent: jest.fn(),
}));

const mockedList = listBusinessDocumentSqlAgentProjects as jest.Mock;
const mockedFetch = fetchBusinessDocumentSqlAgentProject as jest.Mock;
const mockedCreate = createBusinessDocumentSqlAgentProject as jest.Mock;
const mockedRequest = requestBusinessDocumentSqlAgent as jest.Mock;
const mockedDecide = decideBusinessDocumentSqlAgentProposal as jest.Mock;
const mockedCompile = compileBusinessDocumentSqlQuery as jest.Mock;

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

  it('creates a server project and starts the requirements agent', async () => {
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
      (await screen.findAllByRole('button', { name: 'Новый SQL-проект' })).at(
        -1,
      )!,
    );
    fireEvent.change(screen.getByLabelText('Название проекта'), {
      target: { value: 'Продажи по регионам' },
    });
    fireEvent.change(screen.getByLabelText('Исходные требования'), {
      target: { value: 'Вывести продажи по регионам за месяц.' },
    });
    fireEvent.click(
      screen.getByRole('button', { name: 'Создать и продолжить' }),
    );

    expect(
      await screen.findByText('Разобрать исходные требования'),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('sql-agent-run-requirements'));

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
        artifact_ids: {
          requirements: 'artifact-r',
          schema: null,
          query: null,
        },
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
  });

  it('compiles a completed project and renders the final SQL document', async () => {
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
    mockedFetch.mockResolvedValue(complete);
    mockedCompile.mockResolvedValue({
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
    });

    render(<SqlAgentWorkbench />);

    expect(await screen.findByTestId('sql-agent-complete')).toHaveTextContent(
      'SQL и спецификация собраны',
    );
    expect(await screen.findByText(/SELECT SUM/)).toBeInTheDocument();
    expect(
      screen.getByText('Read-only · проверка пройдена'),
    ).toBeInTheDocument();
    expect(screen.getByText('Постобработка на Python')).toBeInTheDocument();
  });
});
