import { useAssistantDefinitions } from '@/hooks/use-sql-assistant-request';
import type {
  AssistantDefinition,
  AssistantSession,
} from '@/interfaces/database/sql-assistant';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { AssistantDefinitions, DefinitionStatus } from './definitions';

jest.mock('@/hooks/use-sql-assistant-request', () => ({
  useAssistantDefinitions: jest.fn(),
}));
jest.mock('./builder', () => ({ assistantTables: () => [] }));

const definition: AssistantDefinition = {
  definition_id: 'definition-1',
  version: 2,
  version_id: 'version-2',
  content_hash: 'hash',
  title: 'Число запросов',
  description: 'Количество непустых id',
  approved_by: 'admin',
  approved_at: 1793200000000,
  freshness: 'CURRENT',
  checks: ['formula', 'grain'],
  contract: {
    grain: [],
    metrics: [
      {
        select_item_id: 'total',
        unit: 'запросы',
        null_policy: 'EXCLUDE_NULLS_ZERO_IF_EMPTY',
        reaggregation: 'FORBIDDEN',
      },
    ],
    time_column_id: null,
    timezone: 'UTC',
    mutable_parameters: [],
  },
};
const session = {
  id: 'session-1',
  mode: 'EXPLORATORY',
  draft: null,
  project: {
    artifact_ids: { schema: 'schema-1', query: 'query-1' },
    capabilities: { edit: true },
    artifacts: {},
  },
  structured_query: {
    base_entity_id: 'queries',
    aliases: { queries: 'q' },
    select: [
      {
        id: 'total',
        kind: 'count',
        column_id: 'queries.id',
        alias: 'total',
        grain: null,
      },
    ],
    joins: [],
    filters: [],
    order_by: [{ select_item_id: 'total', direction: 'DESC' }],
    row_limit: 100,
  },
} as unknown as AssistantSession;

function registry(canPublish = false, freshness = 'CURRENT') {
  jest.mocked(useAssistantDefinitions).mockReturnValue({
    isSuccess: true,
    data: {
      can_publish: canPublish,
      definitions: [{ ...definition, freshness }],
    },
  } as ReturnType<typeof useAssistantDefinitions>);
}

it('chooses a pinned version without running or globally approving SQL', async () => {
  registry();
  const send = jest.fn().mockResolvedValue({ kind: 'use_definition' });
  render(
    <AssistantDefinitions
      actor="me"
      session={session}
      send={send}
      busy={false}
      localChanges={false}
    />,
  );
  expect(
    screen.queryByText('Администратору: утвердить текущую формулу'),
  ).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('Согласованное определение'), {
    target: { value: definition.definition_id },
  });
  fireEvent.click(
    screen.getByRole('button', { name: 'Создать черновик по определению' }),
  );
  await waitFor(() =>
    expect(send).toHaveBeenCalledWith('use_definition', {
      definition_id: definition.definition_id,
      version: 2,
    }),
  );
  expect(send).toHaveBeenCalledTimes(1);
});

it('blocks selection of stale definitions and preserves unsaved edits', () => {
  registry(false, 'SUPERSEDED');
  const { rerender } = render(
    <AssistantDefinitions
      actor="me"
      session={session}
      send={jest.fn()}
      busy={false}
      localChanges={false}
    />,
  );
  fireEvent.change(screen.getByLabelText('Согласованное определение'), {
    target: { value: definition.definition_id },
  });
  expect(
    screen.getByRole('button', { name: 'Создать черновик по определению' }),
  ).toBeDisabled();
  registry();
  rerender(
    <AssistantDefinitions
      actor="me"
      session={session}
      send={jest.fn()}
      busy={false}
      localChanges
    />,
  );
  expect(screen.getByLabelText('Согласованное определение')).toBeDisabled();
  expect(
    screen.getByText(
      'Сохраните или отмените введённые изменения перед сменой режима.',
    ),
  ).toBeVisible();
});

it('publishes an explicit administrator contract with units and count NULL semantics', async () => {
  registry(true);
  const send = jest.fn().mockResolvedValue({ kind: 'publish_definition' });
  render(
    <AssistantDefinitions
      actor="admin"
      session={session}
      send={send}
      busy={false}
      localChanges={false}
    />,
  );
  fireEvent.click(
    screen.getByText('Администратору: утвердить текущую формулу'),
  );
  fireEvent.change(screen.getByLabelText('Название определения'), {
    target: { value: 'Количество запросов' },
  });
  fireEvent.change(screen.getByLabelText('Смысл определения'), {
    target: { value: 'Считаем непустые id' },
  });
  fireEvent.change(screen.getByLabelText('Единица total'), {
    target: { value: 'запросы' },
  });
  fireEvent.click(
    screen.getByRole('button', { name: 'Утвердить определение' }),
  );
  await waitFor(() =>
    expect(send).toHaveBeenCalledWith(
      'publish_definition',
      expect.objectContaining({
        definition_id: null,
        expected_version: 0,
        title: 'Количество запросов',
        contract: expect.objectContaining({
          timezone: 'UTC',
          metrics: [
            expect.objectContaining({
              unit: 'запросы',
              null_policy: 'EXCLUDE_NULLS_ZERO_IF_EMPTY',
            }),
          ],
        }),
      }),
    ),
  );
});

it('shows historical validation separately from current definition freshness', () => {
  render(
    <DefinitionStatus
      value={{
        definition_status: 'DEFINITION_VALIDATED',
        definition,
        definition_freshness: 'SUPERSEDED',
      }}
    />,
  );
  expect(screen.getByText(/Число запросов/)).toHaveTextContent('версия 2');
  expect(screen.getByText(/Есть новая версия/)).toBeVisible();
});
