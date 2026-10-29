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
  send = jest.fn().mockResolvedValue({ kind: 'answer_result' }),
) {
  const props = {
    session: { ...session, ...overrides },
    send,
    busy: false,
    preparingQuery: false,
    parentRow: null,
    onClearRow: jest.fn(),
  };
  return { ...render(<ResultDialogue {...props} />), send, props };
}

it('asks about the selected run by default, without a SQL change command', async () => {
  const { send } = setup();
  fireEvent.change(screen.getByLabelText('Вопрос по выбранному результату'), {
    target: { value: 'Кто первый?' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Получить ответ' }));
  await waitFor(() =>
    expect(send).toHaveBeenCalledWith('answer_result', {
      run_id: 'saved-run',
      text: 'Кто первый?',
    }),
  );
  await waitFor(() =>
    expect(
      screen.getByLabelText('Вопрос по выбранному результату'),
    ).toHaveValue(''),
  );
  expect(send).toHaveBeenCalledTimes(1);
});

it('requires an explicit mode and submission for a SQL revision and preserves both drafts', async () => {
  const { send } = setup();
  fireEvent.change(screen.getByLabelText('Вопрос по выбранному результату'), {
    target: { value: 'Сколько у первого?' },
  });
  fireEvent.click(screen.getByLabelText('Изменить запрос'));
  fireEvent.change(screen.getByLabelText('Изменение запроса'), {
    target: { value: 'Теперь по месяцам' },
  });
  fireEvent.click(screen.getByLabelText('Спросить о результате'));
  expect(screen.getByLabelText('Вопрос по выбранному результату')).toHaveValue(
    'Сколько у первого?',
  );
  expect(send).not.toHaveBeenCalled();
  fireEvent.click(screen.getByLabelText('Изменить запрос'));
  fireEvent.click(
    screen.getByRole('button', { name: 'Предложить изменение SQL' }),
  );
  await waitFor(() =>
    expect(send).toHaveBeenCalledWith('follow_up', {
      run_id: 'saved-run',
      text: 'Теперь по месяцам',
    }),
  );
});

it('preserves unsent text after a conflict or inference failure', async () => {
  const { send } = setup(
    {},
    jest.fn().mockRejectedValue(new Error('Conflict')),
  );
  fireEvent.change(screen.getByLabelText('Вопрос по выбранному результату'), {
    target: { value: 'Какое значение?' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Получить ответ' }));
  await waitFor(() => expect(send).toHaveBeenCalledTimes(1));
  expect(screen.getByLabelText('Вопрос по выбранному результату')).toHaveValue(
    'Какое значение?',
  );
});

it('lets a reader ask but does not offer SQL editing', async () => {
  const { send } = setup({
    project: { capabilities: { edit: false } } as AssistantSession['project'],
  });
  expect(screen.queryByLabelText('Изменить запрос')).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('Вопрос по выбранному результату'), {
    target: { value: 'Первое значение' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Получить ответ' }));
  await waitFor(() =>
    expect(send).toHaveBeenCalledWith('answer_result', expect.anything()),
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
  expect(screen.getByText(/Выдача ограничена/)).toBeVisible();
  fireEvent.click(screen.getByText('Основания ответа'));
  expect(screen.getByText('Строка 1 · Сумма: 0')).toBeVisible();
});

it('moves a new calculation to a draft without automatically generating SQL', () => {
  const { send } = setup({
    messages: [
      {
        id: 'answer',
        text: 'Посчитай по месяцам',
        created_at: 0,
        parent_result_id: 'saved-run',
        answer: {
          status: 'READY',
          kind: 'NEEDS_QUERY',
          text: 'Нужен новый расчёт',
        },
      },
    ],
  });
  fireEvent.click(
    screen.getByRole('button', { name: 'Подготовить изменение запроса' }),
  );
  expect(screen.getByLabelText('Изменение запроса')).toHaveValue(
    'Посчитай по месяцам',
  );
  expect(send).not.toHaveBeenCalled();
});

it('disables submission until a result is selected', () => {
  setup({ selected_result_id: null });
  fireEvent.change(screen.getByLabelText('Вопрос по выбранному результату'), {
    target: { value: 'Первое значение' },
  });
  expect(screen.getByRole('button', { name: 'Получить ответ' })).toBeDisabled();
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
