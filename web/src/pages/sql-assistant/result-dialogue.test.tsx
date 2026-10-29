import type { AssistantSession } from '@/interfaces/database/sql-assistant';
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { ResultDialogue } from './result-dialogue';

const session = {
  id: 'research',
  selected_result_id: 'saved-run',
  project: { capabilities: { edit: true } },
  messages: [{ id: 'first', text: 'Исходный вопрос', parent_result_id: null }],
} as unknown as AssistantSession;

function setup(
  overrides: Partial<AssistantSession> = {},
  send = jest.fn().mockResolvedValue({ kind: 'message' }),
  preparingQuery = false,
) {
  const props = {
    session: { ...session, ...overrides },
    send,
    busy: false,
    preparingQuery,
    parentRow: null,
    onClearRow: jest.fn(),
  };
  return { ...render(<ResultDialogue {...props} />), send, props };
}
const input = () => screen.getByLabelText('Сообщение SQL-помощнику');
const submit = () =>
  screen.getByRole('button', { name: 'Отправить сообщение' });

it('uses one composer and one command for both questions and changes', async () => {
  const { send } = setup();
  expect(screen.queryByRole('radio')).not.toBeInTheDocument();
  fireEvent.change(input(), { target: { value: 'Оставь только оплаченные' } });
  fireEvent.click(submit());
  await waitFor(() =>
    expect(send).toHaveBeenCalledWith('message', {
      run_id: 'saved-run',
      text: 'Оставь только оплаченные',
    }),
  );
  await waitFor(() => expect(input()).toHaveValue(''));
  expect(send).toHaveBeenCalledTimes(1);
});
it('retains the submitted draft after conflict or model failure', async () => {
  const { send } = setup(
    {},
    jest.fn().mockRejectedValue(new Error('conflict')),
  );
  fireEvent.change(input(), { target: { value: 'Покажи по месяцам' } });
  fireEvent.click(submit());
  await waitFor(() => expect(send).toHaveBeenCalledTimes(1));
  expect(input()).toHaveValue('Покажи по месяцам');
});
it('allows a reader to ask without exposing an edit mode', async () => {
  const { send } = setup({
    project: { capabilities: { edit: false } } as AssistantSession['project'],
  });
  fireEvent.change(input(), { target: { value: 'Первое значение' } });
  fireEvent.click(submit());
  await waitFor(() =>
    expect(send).toHaveBeenCalledWith('message', expect.anything()),
  );
  expect(screen.queryByRole('radio')).not.toBeInTheDocument();
});
it('answers a preparation question before any result exists', async () => {
  const { send } = setup({
    selected_result_id: null,
    conversation: {
      phase: 'WAITING',
      pending: { kind: 'REQUIREMENTS' },
      question: { text: 'Какой год?', options: ['2026'] },
    },
  });
  fireEvent.click(screen.getByRole('button', { name: '2026' }));
  await waitFor(() =>
    expect(send).toHaveBeenCalledWith('message', { text: '2026' }),
  );
});
it('stops the running operation through the same composer', async () => {
  const { send } = setup({}, undefined, true);
  expect(input()).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Остановить запрос' }));
  await waitFor(() => expect(send).toHaveBeenCalledWith('stop'));
});
it('Enter sends and Shift Enter keeps a multiline draft', async () => {
  const { send } = setup();
  fireEvent.change(input(), { target: { value: 'Сумма' } });
  fireEvent.keyDown(input(), { key: 'Enter', shiftKey: true });
  expect(send).not.toHaveBeenCalled();
  fireEvent.keyDown(input(), { key: 'Enter' });
  await waitFor(() =>
    expect(send).toHaveBeenCalledWith('message', {
      run_id: 'saved-run',
      text: 'Сумма',
    }),
  );
});
it('displays a restored exact answer and exposes its source cells', () => {
  setup({
    messages: [
      {
        id: 'answer',
        text: 'Какое значение?',
        created_at: 0,
        parent_result_id: 'saved-run',
        answer: {
          status: 'READY',
          kind: 'ANSWER',
          text: 'Значение: 0, пропуск: NULL.',
          scope: 'LIMITED',
          citations: [
            {
              row_index: 0,
              column_id: 'out_aa_1',
              column: 'Сумма',
              value: '0',
            },
          ],
        },
      },
    ],
  });
  expect(screen.getByText('Значение: 0, пропуск: NULL.')).toBeVisible();
  expect(screen.getByText(/По сохранённой части результата/)).toBeVisible();
  fireEvent.click(screen.getByText('Основания ответа'));
  expect(screen.getByText('Сумма, строка 1: 0')).toBeVisible();
});

it('removes the answer and citations when the source retention expires', () => {
  jest.useFakeTimers();
  try {
    setup({
      messages: [
        {
          id: 'answer',
          text: 'Значение?',
          created_at: 0,
          parent_result_id: 'saved-run',
          answer: {
            status: 'READY',
            kind: 'ANSWER',
            text: 'Секретное значение',
            expires_at: Date.now() + 1000,
            citations: [
              {
                row_index: 0,
                column_id: 'out_aa_1',
                column: 'Сумма',
                value: '123',
              },
            ],
          },
        },
      ],
    });
    expect(screen.getByText('Секретное значение')).toBeVisible();
    act(() => jest.advanceTimersByTime(1001));
    expect(screen.queryByText('Секретное значение')).not.toBeInTheDocument();
    expect(screen.queryByText('Основания ответа')).not.toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent('срок хранения');
  } finally {
    jest.useRealTimers();
  }
});
