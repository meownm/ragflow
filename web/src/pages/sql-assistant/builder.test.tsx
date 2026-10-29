import type {
  AssistantQuery,
  AssistantSession,
} from '@/interfaces/database/sql-assistant';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { deserialize, serialize } from 'v8';
import { AssistantBuilder } from './builder';

const cloneDescriptor = Object.getOwnPropertyDescriptor(
  globalThis,
  'structuredClone',
);
beforeAll(() =>
  Object.defineProperty(globalThis, 'structuredClone', {
    configurable: true,
    value: (value: unknown) => deserialize(serialize(value)),
  }),
);
afterAll(() => {
  if (cloneDescriptor)
    Object.defineProperty(globalThis, 'structuredClone', cloneDescriptor);
  else Reflect.deleteProperty(globalThis, 'structuredClone');
});

const session = {
  id: 'session-1',
  revision: 1,
  mode: 'EXPLORATORY',
  draft: null,
  structured_query: null,
  project: {
    id: 'session-1',
    title: 'Orders',
    state_version: 1,
    artifact_ids: { schema: 'schema-1', query: 'query-1' },
    artifacts: {
      query: {
        mode: 'manual',
        sql: 'SELECT count(*) AS total FROM public.orders LIMIT :row_limit',
        parameters: [{ name: 'row_limit', type: 'integer', value: 100 }],
      },
    },
    capabilities: { edit: true, execute: true },
    compilation: {
      result: {
        sql: 'SELECT count(*) AS total FROM public.orders LIMIT :row_limit',
        parameters: { row_limit: 100 },
      },
    },
  },
} as unknown as AssistantSession;

it('retains a manual candidate across surfaces without executing SQL', () => {
  const send = jest.fn();
  const { rerender } = render(
    <AssistantBuilder
      session={session}
      surface="sql"
      send={send}
      busy={false}
    />,
  );
  fireEvent.change(screen.getByLabelText('Исследовательский SQL'), {
    target: {
      value:
        'SELECT count(*) AS total FROM public.orders WHERE paid = true LIMIT :row_limit',
    },
  });
  rerender(
    <AssistantBuilder
      session={session}
      surface="builder"
      send={send}
      busy={false}
    />,
  );
  expect(screen.getByText(/Обратимое преобразование/)).toBeVisible();
  rerender(
    <AssistantBuilder
      session={session}
      surface="sql"
      send={send}
      busy={false}
    />,
  );
  expect(screen.getByLabelText('Исследовательский SQL')).toHaveValue(
    'SELECT count(*) AS total FROM public.orders WHERE paid = true LIMIT :row_limit',
  );
  expect(send).not.toHaveBeenCalled();
});

it('saves a candidate against its exact base and keeps it after a conflict', async () => {
  const send = jest.fn().mockRejectedValue(new Error('Версия изменилась'));
  render(
    <AssistantBuilder
      session={session}
      surface="sql"
      send={send}
      busy={false}
    />,
  );
  fireEvent.change(screen.getByLabelText('Исследовательский SQL'), {
    target: { value: 'SELECT 1 AS total LIMIT :row_limit' },
  });
  fireEvent.click(
    screen.getByRole('button', { name: 'Проверить и сохранить черновик' }),
  );
  await waitFor(() =>
    expect(screen.getByRole('alert')).toHaveTextContent('Версия изменилась'),
  );
  expect(send).toHaveBeenCalledWith(
    'save_draft',
    expect.objectContaining({
      base_query_artifact_id: 'query-1',
      query: expect.objectContaining({
        sql: 'SELECT 1 AS total LIMIT :row_limit',
        mode: 'manual',
      }),
    }),
  );
  expect(screen.getByLabelText('Исследовательский SQL')).toHaveValue(
    'SELECT 1 AS total LIMIT :row_limit',
  );
});

function structuredSession(): AssistantSession {
  const query: AssistantQuery = {
    base_entity_id: 'orders',
    aliases: { orders: 'o' },
    joins: [],
    select: [
      {
        id: 'total',
        column_id: 'orders.id',
        kind: 'count',
        alias: 'total',
        grain: null,
      },
    ],
    filters: [
      {
        id: 'paid',
        column_id: 'orders.status',
        operator: 'eq',
        parameter_name: 'paid',
        parameter_type: 'text',
        parameter_value: 'paid',
        description: 'Статус',
        confirmed: true,
      },
    ],
    order_by: [{ select_item_id: 'total', direction: 'DESC' }],
    row_limit: 100,
  };
  const result = structuredClone(session);
  result.structured_query = query;
  result.project.artifacts = {
    ...result.project.artifacts,
    query,
    schema: {
      accepted_schema: [{ entity_id: 'orders' }],
      schema_snapshot: {
        requirements: [
          {
            selected_table: {
              id: 'orders',
              technical_name: 'orders',
              columns: [
                { id: 'orders.status', name: 'status', data_type: 'TEXT' },
                { id: 'orders.id', name: 'id', data_type: 'INTEGER' },
              ],
            },
          },
        ],
      },
    },
  } as unknown as AssistantSession['project']['artifacts'];
  return result;
}

it('preserves nested WHERE and HAVING through surface changes and saves without executing', async () => {
  let counter = 0;
  Object.defineProperty(crypto, 'randomUUID', {
    configurable: true,
    value: () => `${++counter}0000000-0000-4000-8000-000000000000`,
  });
  const current = structuredSession();
  const send = jest.fn().mockResolvedValue({ kind: 'save_draft' });
  const props = { session: current, send, busy: false };
  const { rerender } = render(
    <AssistantBuilder {...props} surface="builder" />,
  );
  fireEvent.change(screen.getByLabelText('Связь группы WHERE 1'), {
    target: { value: 'OR' },
  });
  fireEvent.click(
    screen.getByRole('button', {
      name: 'Добавить группу WHERE 1',
    }),
  );
  fireEvent.change(screen.getByLabelText('Значение WHERE 1.2.1'), {
    target: { value: 'cancelled' },
  });
  fireEvent.click(
    screen.getByRole('button', {
      name: 'Добавить условие HAVING 1',
    }),
  );
  fireEvent.change(screen.getByLabelText('Условие HAVING 1.1'), {
    target: { value: 'gt' },
  });
  fireEvent.change(screen.getByLabelText('Значение HAVING 1.1'), {
    target: { value: '10' },
  });
  rerender(<AssistantBuilder {...props} surface="sql" />);
  expect(screen.getByLabelText('Исследовательский SQL')).toBeDisabled();
  fireEvent.click(
    screen.getByRole('button', { name: 'Проверить и сохранить черновик' }),
  );
  await waitFor(() => expect(send).toHaveBeenCalledTimes(1));
  rerender(<AssistantBuilder {...props} surface="builder" />);
  expect(screen.getByLabelText('Связь группы WHERE 1')).toHaveValue('OR');
  expect(screen.getByLabelText('Значение WHERE 1.2.1')).toHaveValue(
    'cancelled',
  );
  expect(send).toHaveBeenCalledWith(
    'save_draft',
    expect.objectContaining({
      query: expect.objectContaining({
        row_filter_tree: expect.objectContaining({
          operator: 'OR',
          children: [
            expect.anything(),
            expect.objectContaining({ operator: 'OR' }),
          ],
        }),
        aggregate_filters: [
          expect.objectContaining({
            select_item_id: 'total',
            operator: 'gt',
            parameter_type: 'integer',
            parameter_value: '10',
          }),
        ],
      }),
    }),
  );
});

it('locks approved formula and SQL but permits the explicitly mutable filter value', () => {
  const current = structuredSession();
  current.mode = 'AGREED';
  current.definition = {
    contract: { mutable_parameters: ['paid'] },
  } as AssistantSession['definition'];
  const { rerender } = render(
    <AssistantBuilder
      session={current}
      send={jest.fn()}
      busy={false}
      surface="builder"
    />,
  );
  expect(screen.getByLabelText('Поле WHERE 1.1')).toBeDisabled();
  expect(screen.getByLabelText('Условие WHERE 1.1')).toBeDisabled();
  expect(screen.getByLabelText('Значение WHERE 1.1')).not.toBeDisabled();
  expect(
    screen.getByRole('button', {
      name: 'Добавить группу WHERE 1',
    }),
  ).toBeDisabled();
  rerender(
    <AssistantBuilder
      session={current}
      send={jest.fn()}
      busy={false}
      surface="sql"
    />,
  );
  expect(screen.getByLabelText('Исследовательский SQL')).toBeDisabled();
});
