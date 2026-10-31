import type {
  AssistantDialogueOptions,
  AssistantSession,
} from '@/interfaces/database/sql-assistant';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { useState } from 'react';
import { assistantFailure } from './failure';
import { MentionInput } from './mention-input';
import { ResultDialogue } from './result-dialogue';

const context = {
  run_id: 'saved',
  query_artifact_id: 'query-v1',
  compilation_id: 'compile',
  sql_hash: 'hash',
  parameters_hash: 'params',
  timezone: 'Europe/Moscow',
  view_revision: 2,
};
const options: AssistantDialogueOptions = {
  references: [
    { kind: 'TABLE', id: 'table-id', title: 'bot.orders', fqn: 'bot.orders' },
  ],
  metrics: [
    {
      kind: 'DEFINITION',
      id: 'metric',
      title: 'Выручка',
      version: 3,
      content_hash: 'metric-hash',
      freshness: 'CURRENT',
      description: 'Сумма оплаченных заказов',
    },
  ],
};
const session = {
  id: 'session',
  selected_result_id: 'saved',
  draft: null,
  messages: [],
  project: {
    capabilities: { edit: true },
    artifact_ids: { query: 'current-query' },
  },
  cards: [
    {
      kind: 'RESULT',
      run_id: 'saved',
      query_artifact_id: 'query-v1',
      created_at: Date.now(),
      expires_at: Date.now() + 60000,
      context,
      parameters: { period: '2026-10' },
    },
    { kind: 'SOURCE', entity_id: 'table-id', title: 'bot.orders' },
    {
      kind: 'QUERY_CHANGE',
      proposal_id: 'proposal',
      before_sql: 'SELECT old',
      after_sql: 'SELECT next',
      summary: 'Другой период',
    },
  ],
} as unknown as AssistantSession;

function setup(overrides: Partial<AssistantSession> = {}) {
  const send = jest.fn().mockResolvedValue({ kind: 'message' });
  render(
    <ResultDialogue
      session={{ ...session, ...overrides }}
      send={send}
      busy={false}
      preparingQuery={false}
      parentRow={null}
      onClearRow={jest.fn()}
      options={options}
    />,
  );
  return { send };
}

it('shows the actual SQL and parameter diff before applying an impactful edit', async () => {
  const { send } = setup({
    conversation: {
      phase: 'WAITING',
      pending: { kind: 'REVISION_REVIEW' },
      question: {
        text: 'Применить эту правку?',
        options: ['Применить правку', 'Оставить прежний расчёт'],
      },
    },
    cards: session.cards?.map((card) =>
      card.kind === 'QUERY_CHANGE'
        ? {
            ...card,
            sql_diff: '- WHERE customer_id = :customer\n+ без фильтра',
            parameter_diff: '- customer: 42',
          }
        : card,
    ),
  });
  fireEvent.click(screen.getByText('Изменения SQL и параметров'));
  expect(screen.getByText(/WHERE customer_id/)).toBeVisible();
  expect(screen.getByText('- customer: 42')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Применить правку' }));
  await waitFor(() =>
    expect(send).toHaveBeenCalledWith(
      'message',
      expect.objectContaining({
        text: 'Применить правку',
        run_id: 'saved',
        context,
      }),
    ),
  );
  expect(send).toHaveBeenCalledTimes(1);
});

it('does not offer application controls to a reader of a reviewed calculation', () => {
  setup({
    project: {
      ...session.project,
      capabilities: { ...session.project.capabilities, edit: false },
    },
    conversation: {
      phase: 'WAITING',
      pending: { kind: 'REVISION_REVIEW' },
      question: { text: 'Применить?', options: ['Применить правку'] },
    },
  });
  expect(
    screen.queryByRole('button', { name: 'Применить правку' }),
  ).not.toBeInTheDocument();
  expect(screen.getByLabelText('Сообщение SQL-помощнику')).toBeDisabled();
});
it('sends the immutable displayed context and resolved mention keys', async () => {
  const { send } = setup();
  fireEvent.change(screen.getByLabelText('Сообщение SQL-помощнику'), {
    target: { value: 'Покажи @bot', selectionStart: 11 },
  });
  fireEvent.click(screen.getByRole('button', { name: /Таблица · bot.orders/ }));
  fireEvent.click(screen.getByRole('button', { name: 'Отправить сообщение' }));
  await waitFor(() =>
    expect(send).toHaveBeenCalledWith(
      'message',
      expect.objectContaining({
        context,
        references: [{ kind: 'TABLE', id: 'table-id' }],
      }),
    ),
  );
});
it('asks for a metric definition only when the server requires clarification', async () => {
  const { send } = setup({
    conversation: {
      phase: 'WAITING',
      pending: {
        kind: 'METRIC',
        candidates: [{ id: 'metric', version: 3, content_hash: 'metric-hash' }],
      },
      question: {
        text: 'Как считать выручку?',
        options: [{ id: 'metric', label: 'Выручка' }],
      },
    },
  });
  fireEvent.click(screen.getByRole('button', { name: /^Выручка$/ }));
  await waitFor(() =>
    expect(send).toHaveBeenCalledWith(
      'message',
      expect.objectContaining({
        text: 'Выручка',
        references: [
          {
            kind: 'DEFINITION',
            id: 'metric',
            version: 3,
            content_hash: 'metric-hash',
          },
        ],
      }),
    ),
  );
});
it('does not block a free question because a catalog metric happens to match', () => {
  setup();
  fireEvent.change(screen.getByLabelText('Сообщение SQL-помощнику'), {
    target: { value: 'Выручка по месяцам' },
  });
  expect(
    screen.getByRole('button', { name: 'Отправить сообщение' }),
  ).toBeEnabled();
  expect(screen.queryByRole('radio')).not.toBeInTheDocument();
});
it('blocks access to a selected result whose authorized context was removed', () => {
  setup({ cards: [] });
  expect(screen.getByLabelText('Сообщение SQL-помощнику')).toBeDisabled();
  expect(
    screen.getByRole('button', { name: 'Отправить сообщение' }),
  ).toBeDisabled();
});
it('does not repeat an execution whose outcome is unknown', () => {
  const { send } = setup({ conversation: { phase: 'RECONCILE' } });
  expect(screen.getByLabelText('Сообщение SQL-помощнику')).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Отправить сообщение' }));
  expect(send).not.toHaveBeenCalled();
});
it('inserts a keyboard mention at the caret and keeps following text', () => {
  function Composer() {
    const [text, setText] = useState('');
    const [refs, setRefs] = useState<typeof options.references>([]);
    return (
      <MentionInput
        aria-label="Вопрос"
        value={text}
        onChange={setText}
        references={refs}
        onReferences={setRefs}
        options={options.references}
      />
    );
  }
  render(<Composer />);
  const input = screen.getByLabelText('Вопрос');
  fireEvent.change(input, {
    target: { value: 'До @bot после', selectionStart: 7 },
  });
  fireEvent.keyDown(input, { key: 'Enter' });
  expect(input).toHaveValue('До @bot.orders  после');
  expect(screen.getByLabelText('Выбранные упоминания')).toHaveTextContent(
    'bot.orders',
  );
  fireEvent.click(
    screen.getByRole('button', { name: 'Убрать упоминание bot.orders' }),
  );
  expect(
    screen.queryByLabelText('Выбранные упоминания'),
  ).not.toBeInTheDocument();
});

it.each([
  [403, 'DENIED'],
  [409, 'REFRESH'],
  [422, 'EDIT'],
  [503, 'RETRY_LATER'],
])('classifies status %s without exposing diagnostics', (status, recovery) => {
  const error = {
    isAxiosError: true,
    response: { status, data: { message: 'private provider traceback' } },
  };
  const result = assistantFailure(error);
  expect(result.recovery).toBe(recovery);
  expect(result.message).not.toContain('private');
});

it('requires reconciliation instead of retry for an unknown execution outcome', () => {
  const error = {
    isAxiosError: true,
    response: {
      status: 503,
      data: { data: { error_code: 'SQL_EXECUTION_UNKNOWN' } },
    },
  };
  expect(assistantFailure(error).recovery).toBe('RECONCILE');
});
