import type { AssistantResult } from '@/interfaces/database/sql-assistant';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { randomUUID } from 'crypto';
import { ResultBuilder } from './result-builder';
import { cellText } from './result-format';

beforeAll(() =>
  Object.defineProperty(globalThis.crypto, 'randomUUID', {
    configurable: true,
    value: randomUUID,
  }),
);
afterAll(() => {
  delete (globalThis.crypto as Partial<Crypto>).randomUUID;
});

const result = {
  run_id: 'run',
  view_revision: 0,
  columns: [
    { id: 'region', name: 'Регион' },
    { id: 'amount', name: 'Сумма' },
  ],
  rows: [{ index: 0, values: ['A', '0.1'] }],
  row_count: 200,
  metric_aggregation_allowed: true,
  view: {
    column_order: ['region', 'amount'],
    hidden: [],
    labels: {},
    formats: {},
    filters: [],
    sort: null,
    chart: null,
  },
} as unknown as AssistantResult;
function setup(save = jest.fn().mockResolvedValue(undefined)) {
  const onNewMetric = jest.fn();
  const ui = render(
    <ResultBuilder
      result={result}
      busy={false}
      onSave={save}
      onNewMetric={onNewMetric}
    />,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Настроить результат' }));
  return { ...ui, save, onNewMetric };
}

it('edits fields, compound filters, appearance and metrics in one explicit save', async () => {
  const { save } = setup();
  fireEvent.change(screen.getByLabelText('Название Сумма'), {
    target: { value: 'Выручка' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Раньше Выручка' }));
  fireEvent.click(screen.getByRole('button', { name: 'Фильтры и сортировка' }));
  fireEvent.click(screen.getByRole('button', { name: 'Добавить фильтр' }));
  fireEvent.change(screen.getByLabelText('Условие фильтра 1'), {
    target: { value: 'gt' },
  });
  fireEvent.change(screen.getByLabelText('Значение фильтра 1'), {
    target: { value: '10' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Добавить фильтр' }));
  fireEvent.change(screen.getByLabelText('Условие фильтра 2'), {
    target: { value: 'is_not_null' },
  });
  expect(screen.queryByLabelText('Значение фильтра 2')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Внешний вид' }));
  fireEvent.change(screen.getByLabelText('Отображение результата'), {
    target: { value: 'both' },
  });
  fireEvent.change(screen.getByLabelText('Тип графика'), {
    target: { value: 'line' },
  });
  fireEvent.click(screen.getByLabelText('Чередование строк'));
  fireEvent.click(screen.getByRole('button', { name: 'Метрики' }));
  fireEvent.click(screen.getByRole('button', { name: 'Добавить метрику' }));
  fireEvent.change(screen.getByLabelText('Тип метрики 1'), {
    target: { value: 'sum' },
  });
  expect(save).not.toHaveBeenCalled();
  fireEvent.click(
    screen.getByRole('button', { name: 'Сохранить представление' }),
  );
  await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
  const [view, revision] = save.mock.calls[0];
  expect(revision).toBe(0);
  expect(view.column_order).toEqual(['amount', 'region']);
  expect(view.filters).toHaveLength(2);
  expect(view.filters[1].value).toBeNull();
  expect(view.appearance).toMatchObject({ display: 'both', striped: true });
  expect(view.chart.kind).toBe('line');
  expect(view.metrics[0].kind).toBe('sum');
  await waitFor(() =>
    expect(screen.getByRole('status')).toHaveTextContent('сохранены'),
  );
});

it('keeps the draft on a save failure and blocks overwriting a newer revision', async () => {
  const { save, rerender } = setup(
    jest.fn().mockRejectedValue(new Error('conflict')),
  );
  fireEvent.change(screen.getByLabelText('Название Сумма'), {
    target: { value: 'Черновик' },
  });
  fireEvent.click(
    screen.getByRole('button', { name: 'Сохранить представление' }),
  );
  await waitFor(() =>
    expect(screen.getByRole('alert')).toHaveTextContent('не сохранены'),
  );
  expect(screen.getByLabelText('Название Сумма')).toHaveValue('Черновик');
  rerender(
    <ResultBuilder
      result={{ ...result, view_revision: 2 }}
      busy={false}
      onSave={save}
    />,
  );
  expect(
    screen.getByRole('button', { name: 'Сохранить представление' }),
  ).toBeDisabled();
  expect(screen.getByLabelText('Название Сумма')).toHaveValue('Черновик');
  fireEvent.click(
    screen.getByRole('button', { name: 'Загрузить сохранённую версию' }),
  );
  expect(screen.getByLabelText('Название Сумма')).toHaveValue('Сумма');
});

it('prevents hiding the last field and opens new calculations without executing SQL', () => {
  const { save, onNewMetric } = setup();
  fireEvent.click(screen.getByLabelText('Показывать Регион'));
  expect(screen.getByLabelText('Показывать Сумма')).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Метрики' }));
  fireEvent.click(
    screen.getByRole('button', {
      name: 'Настроить новый расчёт метрики в SQL-конструкторе',
    }),
  );
  expect(onNewMetric).toHaveBeenCalledTimes(1);
  expect(save).not.toHaveBeenCalled();
});

it('does not silently round a high-precision decimal string', () => {
  expect(cellText('12345678901234567890.00100', 'currency')).toBe(
    '12345678901234567890.00100',
  );
  expect(cellText(0)).toBe('0');
  expect(cellText(null)).toBe('NULL');
});
