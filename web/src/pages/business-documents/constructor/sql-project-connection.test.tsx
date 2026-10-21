import {
  checkBusinessDocumentSqlProjectConnection,
  compileBusinessDocumentSqlProject,
  configureBusinessDocumentSqlProjectConnection,
  preflightBusinessDocumentSqlProject,
  runBusinessDocumentSqlProject,
} from '@/services/business-document-service';
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { PreQueryConnection } from './sql-project-connection';

type SqlAgentProject =
  import('@/pages/business-documents/types').SqlAgentProject;
type SqlExecutionBindingResponse =
  import('@/pages/business-documents/types').SqlExecutionBindingResponse;
type SqlProjectConnectionCheck =
  import('@/pages/business-documents/types').SqlProjectConnectionCheck;

jest.mock('@/services/business-document-service', () => ({
  checkBusinessDocumentSqlProjectConnection: jest.fn(),
  configureBusinessDocumentSqlProjectConnection: jest.fn(),
  compileBusinessDocumentSqlProject: jest.fn(),
  preflightBusinessDocumentSqlProject: jest.fn(),
  runBusinessDocumentSqlProject: jest.fn(),
}));
const probe = jest.mocked(checkBusinessDocumentSqlProjectConnection);
const save = jest.mocked(configureBusinessDocumentSqlProjectConnection);
const profiles = ['profile-1', 'profile-2'].map((id, index) => ({
  id,
  name: `QA ${index + 1}`,
  dialect: 'postgres' as const,
  version: index + 1,
  target_database: 'qa',
  statement_timeout_ms: 5000,
  max_rows: 100,
  max_result_bytes: 10000,
  policy_fingerprint: 'sha256:policy',
}));

function project(changes: Partial<SqlAgentProject> = {}): SqlAgentProject {
  return {
    schema_version: '1',
    id: 'pre-query',
    title: 'Продажи',
    source_request: 'Покажи продажи',
    locale: 'ru',
    stage: 'QUERY',
    operation_state: 'IDLE',
    state_version: 1,
    next_agent: 'QUERY',
    current_job: null,
    pending_proposal: null,
    artifact_ids: { requirements: 'req', schema: 'schema', query: null },
    artifacts: { requirements: {}, schema: {}, query: null },
    compilation: null,
    last_error: null,
    capabilities: {
      requirements_agent: true,
      schema_agent: true,
      query_agent: true,
      result_agent: false,
      python_agent: false,
      edit: true,
      execute: true,
    },
    ...changes,
  };
}
function health(
  profileId: string | null,
  changes: Partial<SqlProjectConnectionCheck> = {},
): SqlProjectConnectionCheck {
  const profile = profiles.find((item) => item.id === profileId);
  const binding: SqlExecutionBindingResponse = {
    schema_version: '1',
    status: profile ? 'BOUND' : 'NEEDS_SELECTION',
    reason: profile ? null : 'MULTIPLE_EXECUTION_PROFILES',
    snapshot_fingerprint: 'sha256:schema',
    catalog_scopes: [
      { service: 'qa', database: 'qa', schema: 'public', table_ids: ['sales'] },
    ],
    unresolved_catalog_scopes: [],
    candidates: profiles,
    selection: profile
      ? { decision: 'user', profile, bindings: [], relations: [] }
      : null,
  };
  return {
    schema_version: '1',
    project_id: 'pre-query',
    state_version: 1,
    status: profile ? 'AVAILABLE' : 'NEEDS_SELECTION',
    binding,
    query_checked: false,
    checked_at: profile ? 1792526400000 : null,
    duration_ms: profile ? 2 : null,
    ...changes,
  };
}
function check() {
  fireEvent.click(
    screen.getByRole('button', { name: 'Проверить подключение' }),
  );
}
async function selectProfileTwo() {
  probe.mockResolvedValueOnce(health(null));
  check();
  await screen.findByRole('option', { name: 'QA 2' });
  fireEvent.change(screen.getByRole('combobox', { name: 'Источник данных' }), {
    target: { value: 'profile-2' },
  });
  probe.mockResolvedValueOnce(health('profile-2'));
  check();
  await screen.findByText(/Подключение доступно: QA 2/);
}

beforeEach(() => {
  jest.resetAllMocks();
  probe.mockResolvedValue(health(null));
  save.mockResolvedValue(
    project({ execution_profile_id: 'profile-2', state_version: 2 }),
  );
});

test('discovers, checks and saves a profile before SQL exists, then restores the saved selection', async () => {
  const refresh = jest.fn().mockResolvedValue(undefined);
  const page = render(
    <PreQueryConnection project={project()} onRefresh={refresh} />,
  );
  await selectProfileTwo();
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить источник' }));
  await waitFor(() => expect(refresh).toHaveBeenCalledTimes(1));
  expect(save).toHaveBeenCalledWith(
    'pre-query',
    1,
    expect.any(String),
    'profile-2',
  );
  page.rerender(
    <PreQueryConnection
      project={project({ execution_profile_id: 'profile-2', state_version: 2 })}
      onRefresh={refresh}
    />,
  );
  expect(screen.getByRole('combobox', { name: 'Источник данных' })).toHaveValue(
    'profile-2',
  );
  expect(
    screen.queryByText(
      'Выбор ещё не сохранён. Сохраните источник перед запуском.',
    ),
  ).not.toBeInTheDocument();
  expect(compileBusinessDocumentSqlProject).not.toHaveBeenCalled();
  expect(preflightBusinessDocumentSqlProject).not.toHaveBeenCalled();
  expect(runBusinessDocumentSqlProject).not.toHaveBeenCalled();
});

test.each(['reader', 'archived'])(
  'has no source mutation or probe controls for %s projects',
  (kind) => {
    const initial = project();
    const saved =
      kind === 'reader'
        ? project({
            capabilities: {
              ...initial.capabilities,
              edit: false,
              execute: false,
            },
          })
        : project({ archived_at: 1 });
    render(<PreQueryConnection project={saved} onRefresh={jest.fn()} />);
    expect(
      screen.queryByRole('button', { name: 'Проверить подключение' }),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument();
    expect(probe).not.toHaveBeenCalled();
    expect(save).not.toHaveBeenCalled();
  },
);

test('an active agent locks source changes but permits the independent read-only probe', async () => {
  render(
    <PreQueryConnection
      project={project({ operation_state: 'RUNNING' })}
      onRefresh={jest.fn()}
    />,
  );
  expect(screen.getByRole('combobox')).toBeDisabled();
  expect(
    screen.getByRole('button', { name: 'Сохранить источник' }),
  ).toBeDisabled();
  check();
  await screen.findByText('Для проверки подключения выберите один источник.');
  expect(probe).toHaveBeenCalledTimes(1);
  expect(save).not.toHaveBeenCalled();
});

test('failed health checks require an explicit retry with the same unsaved selection', async () => {
  probe
    .mockRejectedValueOnce(new Error('Сеть недоступна'))
    .mockResolvedValueOnce(health('profile-2'));
  render(
    <PreQueryConnection
      project={project({ execution_profile_id: 'profile-2' })}
      onRefresh={jest.fn()}
    />,
  );
  check();
  await screen.findByRole('alert');
  expect(probe).toHaveBeenCalledTimes(1);
  expect(screen.getByRole('combobox')).toHaveValue('profile-2');
  check();
  await screen.findByText(/Подключение доступно: QA 2/);
  expect(probe.mock.calls.map((call) => call.slice(0, 2))).toEqual([
    ['pre-query', 'profile-2'],
    ['pre-query', 'profile-2'],
  ]);
  expect(save).not.toHaveBeenCalled();
});

test('changing selection cancels the old health request and ignores its late success', async () => {
  render(<PreQueryConnection project={project()} onRefresh={jest.fn()} />);
  check();
  await screen.findByRole('option', { name: 'QA 2' });
  fireEvent.change(screen.getByRole('combobox'), {
    target: { value: 'profile-1' },
  });
  let finish!: (result: SqlProjectConnectionCheck) => void;
  probe.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  check();
  const signal = probe.mock.calls.at(-1)![2]!;
  fireEvent.change(screen.getByRole('combobox'), {
    target: { value: 'profile-2' },
  });
  expect(signal.aborted).toBe(true);
  await act(async () => {
    finish(health('profile-1'));
  });
  expect(
    screen.queryByText(/Подключение доступно: QA 1/),
  ).not.toBeInTheDocument();
  expect(screen.getByRole('combobox')).toHaveValue('profile-2');
  expect(
    screen.getByRole('button', { name: 'Проверить подключение' }),
  ).toBeEnabled();
});

test.each(['version', 'schema', 'permission'])(
  'invalidates an in-flight check after a %s change',
  async (change) => {
    let finish!: (result: SqlProjectConnectionCheck) => void;
    probe.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    const saved = project({ execution_profile_id: 'profile-1' });
    const refresh = jest.fn();
    const page = render(
      <PreQueryConnection project={saved} onRefresh={refresh} />,
    );
    check();
    const signal = probe.mock.calls[0][2]!;
    const next =
      change === 'version'
        ? { ...saved, state_version: 2 }
        : change === 'schema'
          ? {
              ...saved,
              artifact_ids: { ...saved.artifact_ids, schema: 'new-schema' },
            }
          : {
              ...saved,
              capabilities: {
                ...saved.capabilities,
                edit: false,
                execute: false,
              },
            };
    page.rerender(<PreQueryConnection project={next} onRefresh={refresh} />);
    expect(signal.aborted).toBe(true);
    await act(async () => {
      finish(health('profile-1'));
    });
    expect(screen.queryByText(/Подключение доступно:/)).not.toBeInTheDocument();
  },
);

test('a save reply after unmount cannot refresh an old project', async () => {
  const refresh = jest.fn();
  const page = render(
    <PreQueryConnection project={project()} onRefresh={refresh} />,
  );
  await selectProfileTwo();
  let finish!: (result: SqlAgentProject) => void;
  save.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить источник' }));
  page.unmount();
  await act(async () => {
    finish(project({ state_version: 2, execution_profile_id: 'profile-2' }));
  });
  expect(refresh).not.toHaveBeenCalled();
});

test('a save retry after a lost reply reuses its command identity', async () => {
  const refresh = jest.fn().mockResolvedValue(undefined);
  render(<PreQueryConnection project={project()} onRefresh={refresh} />);
  await selectProfileTwo();
  save.mockRejectedValueOnce(new Error('Ответ потерян'));
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить источник' }));
  await screen.findByText('Ответ потерян');
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить источник' }));
  await waitFor(() => expect(refresh).toHaveBeenCalledTimes(1));
  expect(save.mock.calls).toHaveLength(2);
  expect(save.mock.calls[1]).toEqual(save.mock.calls[0]);
});

test('a mismatched server snapshot never becomes a successful connection check', async () => {
  probe.mockResolvedValueOnce(
    health('profile-1', { project_id: 'another-project' }),
  );
  render(<PreQueryConnection project={project()} onRefresh={jest.fn()} />);
  check();
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'Проект изменился',
  );
  expect(screen.queryByText(/Подключение доступно:/)).not.toBeInTheDocument();
});

test('an available response for another explicit profile cannot validate the selected source', async () => {
  probe.mockResolvedValueOnce(health('profile-2'));
  render(
    <PreQueryConnection
      project={project({ execution_profile_id: 'profile-1' })}
      onRefresh={jest.fn()}
    />,
  );
  check();
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'Проект изменился',
  );
  expect(screen.queryByText(/Подключение доступно:/)).not.toBeInTheDocument();
});

test('revoking edit and execute permissions clears an unsaved source choice', async () => {
  const refresh = jest.fn();
  const initial = project();
  const page = render(
    <PreQueryConnection project={initial} onRefresh={refresh} />,
  );
  check();
  await screen.findByRole('option', { name: 'QA 2' });
  fireEvent.change(screen.getByRole('combobox'), {
    target: { value: 'profile-2' },
  });
  expect(
    screen.getByText(
      'Выбор ещё не сохранён. Сохраните источник перед запуском.',
    ),
  ).toBeInTheDocument();
  page.rerender(
    <PreQueryConnection
      project={{
        ...initial,
        capabilities: { ...initial.capabilities, edit: false, execute: false },
      }}
      onRefresh={refresh}
    />,
  );
  expect(
    screen.queryByText(
      'Выбор ещё не сохранён. Сохраните источник перед запуском.',
    ),
  ).not.toBeInTheDocument();
  expect(save).not.toHaveBeenCalled();
});
