import { render, screen } from '@testing-library/react';
import { SqlQueryComparison } from './sql-query-comparison';

const version = {
  id: 'query',
  revision: 1,
  sql: 'SELECT value AS value',
  blocks: [],
};

test('compares types, values and missing parameters independently of SQL text', () => {
  render(
    <SqlQueryComparison
      current={{
        ...version,
        parameters: [
          { name: 'zero', type: 'integer', value: 0 },
          { name: 'flag', type: 'boolean', value: false },
          { name: 'removed', type: 'text', value: '' },
        ],
      }}
      selected={{
        ...version,
        parameters: [
          { name: 'zero', type: 'text', value: '0' },
          { name: 'flag', type: 'boolean', value: false },
          { name: 'added', type: 'text', value: '' },
        ],
      }}
    />,
  );
  const table = screen.getByRole('table');
  expect(table.querySelectorAll('[data-change="CHANGED"]')).toHaveLength(1);
  expect(table.querySelectorAll('[data-change="SAME"]')).toHaveLength(1);
  expect(table.querySelectorAll('[data-change="REMOVED"]')).toHaveLength(1);
  expect(table.querySelectorAll('[data-change="ADDED"]')).toHaveLength(1);
  expect(screen.getByText(/Текст SQL совпадает/)).toBeInTheDocument();
});

test('shows server-preserved large integer digits instead of rounded browser numbers', () => {
  render(
    <SqlQueryComparison
      current={{
        ...version,
        parameters: [
          {
            name: 'id',
            type: 'integer',
            value: Number('9223372036854775807'),
            value_text: '9223372036854775807',
          },
        ],
      }}
      selected={{
        ...version,
        parameters: [
          {
            name: 'id',
            type: 'integer',
            value: Number('9223372036854775806'),
            value_text: '9223372036854775806',
          },
        ],
      }}
    />,
  );
  expect(screen.getByText('integer · 9223372036854775807')).toBeInTheDocument();
  expect(screen.getByText('integer · 9223372036854775806')).toBeInTheDocument();
  expect(screen.getByText('Изменено')).toBeInTheDocument();
});

test('reports absent historical types instead of claiming parameters match', () => {
  render(<SqlQueryComparison current={version} selected={version} />);
  expect(
    screen.getByText(/отсутствуют сохранённые типизированные параметры/),
  ).toBeInTheDocument();
  expect(screen.queryByRole('table')).not.toBeInTheDocument();
});
