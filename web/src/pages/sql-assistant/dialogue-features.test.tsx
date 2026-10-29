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

function setup() {
  const send = jest.fn().mockResolvedValue({ kind: 'answer_result' });
  const onOpenSql = jest.fn();
  const onUseDefinition = jest.fn();
  render(
    <ResultDialogue
      session={session}
      send={send}
      busy={false}
      preparingQuery={false}
      parentRow={null}
      onClearRow={jest.fn()}
      options={options}
      onOpenSql={onOpenSql}
      onUseDefinition={onUseDefinition}
    />,
  );
  return { send, onOpenSql, onUseDefinition };
}

it('sends the displayed SQL context and resolved reference keys', async () => {
  const { send } = setup();
  expect(screen.getByLabelText('Контекст вопроса')).toHaveTextContent(
    'Europe/Moscow',
  );
  fireEvent.click(screen.getByText('Версия SQL и параметры'));
  expect(screen.getByText(/query-v1/)).toBeVisible();
  fireEvent.change(screen.getByLabelText('Вопрос по выбранному результату'), {
    target: { value: 'Покажи @bot', selectionStart: 11 },
  });
  fireEvent.click(screen.getByRole('button', { name: /Таблица · bot.orders/ }));
  fireEvent.click(screen.getByRole('button', { name: 'Получить ответ' }));
  await waitFor(() =>
    expect(send).toHaveBeenCalledWith(
      'answer_result',
      expect.objectContaining({
        context,
        references: [{ kind: 'TABLE', id: 'table-id' }],
      }),
    ),
  );
});

it('offers an explicit metric choice before a new calculation', async () => {
  const { send, onUseDefinition } = setup();
  fireEvent.click(screen.getByLabelText('Изменить запрос'));
  fireEvent.change(screen.getByLabelText('Изменение запроса'), {
    target: { value: 'Выручка по месяцам' },
  });
  expect(
    screen.getByRole('button', { name: 'Предложить изменение SQL' }),
  ).toBeDisabled();
  fireEvent.click(
    screen.getByRole('button', { name: 'Выбрать определение «Выручка»' }),
  );
  expect(onUseDefinition).toHaveBeenCalledWith('metric', 3);
  expect(send).not.toHaveBeenCalled();
  fireEvent.click(
    screen.getByLabelText(
      'Продолжить как исследовательский расчёт без применения определения',
    ),
  );
  fireEvent.click(
    screen.getByRole('button', { name: 'Предложить изменение SQL' }),
  );
  await waitFor(() =>
    expect(send).toHaveBeenCalledWith(
      'follow_up',
      expect.objectContaining({ metric_resolution: 'EXPLORATORY' }),
    ),
  );
});

it('opens typed cards without generating SQL or parsing Markdown commands', () => {
  const { send, onOpenSql } = setup();
  fireEvent.click(
    screen.getByRole('button', { name: 'Открыть SQL результата' }),
  );
  expect(onOpenSql).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByText('Сравнить изменения SQL'));
  expect(screen.getByText('SELECT old')).toBeVisible();
  expect(screen.getByText('SELECT next')).toBeVisible();
  fireEvent.click(
    screen.getByRole('button', { name: 'Продолжить исследование' }),
  );
  expect(screen.getByLabelText('Изменить запрос')).toBeChecked();
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

it('blocks questions when a selected result has lost its authorized context', () => {
  const send = jest.fn();
  render(
    <ResultDialogue
      session={{ ...session, cards: [] }}
      send={send}
      busy={false}
      preparingQuery={false}
      parentRow={null}
      onClearRow={jest.fn()}
    />,
  );
  fireEvent.change(screen.getByLabelText('Вопрос по выбранному результату'), {
    target: { value: 'Значение?' },
  });
  expect(screen.getByRole('button', { name: 'Получить ответ' })).toBeDisabled();
  expect(screen.getByRole('status')).toHaveTextContent(
    'Контекст выбранного результата недоступен',
  );
  expect(send).not.toHaveBeenCalled();
});

it('resets mention selection when switching between retained drafts', () => {
  setup();
  fireEvent.click(screen.getByLabelText('Изменить запрос'));
  fireEvent.change(screen.getByLabelText('Изменение запроса'), {
    target: { value: 'Сумма по месяцам' },
  });
  fireEvent.click(screen.getByLabelText('Спросить о результате'));
  fireEvent.change(screen.getByLabelText('Вопрос по выбранному результату'), {
    target: { value: 'Кто @bot', selectionStart: 8 },
  });
  expect(screen.getByRole('listbox')).toBeVisible();
  fireEvent.click(screen.getByLabelText('Изменить запрос'));
  expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Упомянуть объект @' }));
  fireEvent.click(screen.getByRole('button', { name: /Таблица · bot.orders/ }));
  expect(screen.getByLabelText('Изменение запроса')).toHaveValue(
    'Сумма по месяцам @bot.orders ',
  );
});
