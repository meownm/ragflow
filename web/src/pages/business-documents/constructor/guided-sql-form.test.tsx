import {
  fetchBusinessDocumentSqlRevisionWorkspace,
  saveBusinessDocumentSqlManualQuery,
  saveBusinessDocumentSqlRevisionDraft,
  validateBusinessDocumentSqlManualQuery,
} from '@/services/business-document-service';
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { GuidedSqlForm } from './guided-sql-form';

jest.mock('@/services/business-document-service', () => ({
  fetchBusinessDocumentSqlRevisionWorkspace: jest.fn(),
  saveBusinessDocumentSqlManualQuery: jest.fn(),
  saveBusinessDocumentSqlRevisionDraft: jest.fn(),
  validateBusinessDocumentSqlManualQuery: jest.fn(),
}));

const validate = validateBusinessDocumentSqlManualQuery as jest.Mock;
const save = saveBusinessDocumentSqlManualQuery as jest.Mock;
const fetchWorkspace = fetchBusinessDocumentSqlRevisionWorkspace as jest.Mock;
const saveDraft = saveBusinessDocumentSqlRevisionDraft as jest.Mock;
const baseSql =
  'SELECT o.order_id AS order_id FROM dwh.order_fact AS o LIMIT :row_limit';
const joinedSql =
  'SELECT o.order_id AS order_id, c.customer_id AS customer_id FROM dwh.order_fact AS o JOIN dwh.customer_dim AS c ON c.customer_id = o.customer_id LIMIT :row_limit';
const checked = {
  schema_version: '1',
  status: 'READY',
  snapshot_fingerprint: 'sha256:schema',
  blocking_issues: [],
  sql: baseSql,
  parameters: { row_limit: 100 },
  guard: {
    status: 'PASS',
    tables: ['dwh.order_fact'],
    parameters: ['row_limit'],
  },
};
const project = {
  id: 'guided-project',
  state_version: 4,
  artifact_ids: { schema: 'schema-1' },
};

describe('GuidedSqlForm', () => {
  beforeEach(() => {
    window.localStorage.clear();
    window.sessionStorage.clear();
    jest.clearAllMocks();
    validate.mockResolvedValue(checked);
    save.mockResolvedValue({});
    fetchWorkspace.mockResolvedValue({ draft: null });
    saveDraft.mockResolvedValue({ version: 1 });
  });

  it('does not create a server draft merely by opening an unchanged project', async () => {
    jest.useFakeTimers();
    try {
      render(
        <GuidedSqlForm
          project={project as never}
          initialSql={baseSql}
          onSaved={jest.fn()}
        />,
      );
      await act(async () => {});
      await act(async () => {
        jest.advanceTimersByTime(2000);
      });
      expect(saveDraft).not.toHaveBeenCalled();
      expect(window.sessionStorage.length).toBe(0);
    } finally {
      jest.useRealTimers();
    }
  });

  it('does not load or expose an editing surface for a reader', () => {
    render(
      <GuidedSqlForm
        project={{ ...project, capabilities: { edit: false } } as never}
        initialSql={baseSql}
        onSaved={jest.fn()}
      />,
    );
    expect(
      screen.queryByRole('button', { name: 'Собрать SQL по шагам' }),
    ).not.toBeInTheDocument();
    expect(fetchWorkspace).not.toHaveBeenCalled();
    expect(saveDraft).not.toHaveBeenCalled();
  });

  it('starts from a physical table and column in the accepted schema', async () => {
    render(
      <GuidedSqlForm
        project={
          {
            ...project,
            artifacts: {
              schema: {
                accepted_schema: [{ entity_id: 'orders' }],
                schema_snapshot: {
                  requirements: [
                    {
                      selected_table: {
                        id: 'orders',
                        schema: 'dwh',
                        technical_name: 'order_fact',
                        columns: [{ name: 'order_id', selected: true }],
                      },
                    },
                  ],
                },
              },
            },
          } as never
        }
        onSaved={jest.fn()}
      />,
    );
    fireEvent.click(
      screen.getByRole('button', { name: 'Собрать SQL по шагам' }),
    );
    await screen.findByLabelText('SQL шага 1');
    expect(screen.getByLabelText('SQL шага 1')).toHaveValue(
      'SELECT t."order_id" AS "order_id" FROM "dwh"."order_fact" AS t LIMIT :row_limit',
    );
  });

  it('checks each complete query, invalidates later checks on edits, and saves only the verified final step', async () => {
    const onSaved = jest.fn().mockResolvedValue(undefined);
    render(<GuidedSqlForm project={project as never} onSaved={onSaved} />);
    fireEvent.click(
      screen.getByRole('button', { name: 'Собрать SQL по шагам' }),
    );
    await screen.findByLabelText('SQL шага 1');
    expect(
      screen.getByRole('button', { name: 'Добавить уточнение' }),
    ).toBeDisabled();
    fireEvent.change(screen.getByLabelText('SQL шага 1'), {
      target: { value: baseSql },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Проверить шаг' }));
    await waitFor(() =>
      expect(validate).toHaveBeenCalledWith('guided-project', 4, baseSql, [
        { name: 'row_limit', type: 'integer', value: 100 },
      ]),
    );
    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: 'Добавить уточнение' }),
      ).toBeEnabled(),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Добавить уточнение' }));
    expect(
      (screen.getByLabelText('SQL шага 2') as HTMLTextAreaElement).value,
    ).toBe(baseSql);
    fireEvent.change(screen.getByLabelText('SQL шага 2'), {
      target: { value: joinedSql },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Проверить шаг' }));
    await waitFor(() =>
      expect(validate).toHaveBeenCalledWith(
        'guided-project',
        4,
        joinedSql,
        expect.any(Array),
      ),
    );
    await waitFor(() =>
      expect(screen.getByText(/Шаг проверен/)).toBeInTheDocument(),
    );
    fireEvent.click(
      screen.getByRole('button', { name: /1\. Базовая выборка/ }),
    );
    fireEvent.change(screen.getByLabelText('SQL шага 1'), {
      target: { value: `${baseSql} ` },
    });
    fireEvent.click(screen.getByRole('button', { name: /2\. Соединения/ }));
    expect(screen.queryByText(/Шаг проверен/)).not.toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'Сохранить проверенный SQL' }),
    ).toBeDisabled();
    expect(
      screen.getByRole('button', { name: 'Проверить шаг' }),
    ).toBeDisabled();
    expect(save).not.toHaveBeenCalled();
  });

  it('saves a verified final draft after task alignment', async () => {
    const onSaved = jest.fn().mockResolvedValue(undefined);
    render(
      <GuidedSqlForm
        project={project as never}
        initialSql={baseSql}
        onSaved={onSaved}
      />,
    );
    fireEvent.click(
      screen.getByRole('button', { name: 'Редактировать финальный SQL' }),
    );
    await screen.findByLabelText('SQL шага 1');
    fireEvent.click(screen.getByRole('button', { name: 'Проверить шаг' }));
    await waitFor(() =>
      expect(screen.getByText(/Шаг проверен/)).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole('checkbox'));
    fireEvent.click(
      screen.getByRole('button', { name: 'Сохранить проверенный SQL' }),
    );
    await waitFor(() =>
      expect(save).toHaveBeenCalledWith(
        'guided-project',
        4,
        expect.stringMatching(/^manual-sql-/),
        baseSql,
        [{ name: 'row_limit', type: 'integer', value: 100 }],
      ),
    );
    expect(onSaved).toHaveBeenCalled();
  });

  it('shows unsaved work and saves it immediately with the server draft version', async () => {
    fetchWorkspace.mockResolvedValue({
      draft: {
        version: 3,
        base_query_artifact_id: null,
        steps: [
          {
            title: 'Базовая выборка',
            sql: baseSql,
            parameters: '[{"name":"row_limit","type":"integer","value":100}]',
          },
        ],
      },
    });
    saveDraft
      .mockResolvedValueOnce({ version: 4 })
      .mockResolvedValueOnce({ version: 5 });
    render(<GuidedSqlForm project={project as never} onSaved={jest.fn()} />);
    fireEvent.click(
      screen.getByRole('button', { name: 'Собрать SQL по шагам' }),
    );
    await screen.findByLabelText('SQL шага 1');
    fireEvent.change(screen.getByLabelText('SQL шага 1'), {
      target: { value: `${baseSql} ` },
    });
    expect(
      screen.getByText('Есть несохранённые изменения'),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить черновик' }));
    await waitFor(() =>
      expect(saveDraft).toHaveBeenCalledWith(
        'guided-project',
        expect.objectContaining({
          expected_draft_version: 3,
          steps: [expect.objectContaining({ sql: `${baseSql} ` })],
        }),
      ),
    );
    await screen.findByText('Черновик сохранён на сервере');
    fireEvent.change(screen.getByLabelText('SQL шага 1'), {
      target: { value: `${baseSql}  ` },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить черновик' }));
    await waitFor(() =>
      expect(saveDraft).toHaveBeenLastCalledWith(
        'guided-project',
        expect.objectContaining({
          expected_draft_version: 4,
          steps: [expect.objectContaining({ sql: `${baseSql}  ` })],
        }),
      ),
    );
  });

  it('serializes an edit made while the prior server save is running', async () => {
    fetchWorkspace.mockResolvedValue({
      draft: {
        version: 3,
        base_query_artifact_id: null,
        steps: [
          {
            title: 'Базовая выборка',
            sql: baseSql,
            parameters: '[{"name":"row_limit","type":"integer","value":100}]',
          },
        ],
      },
    });
    let finishFirst: (value: { version: number }) => void = () => undefined;
    saveDraft
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            finishFirst = resolve;
          }),
      )
      .mockResolvedValueOnce({ version: 5 });
    render(<GuidedSqlForm project={project as never} onSaved={jest.fn()} />);
    fireEvent.click(
      screen.getByRole('button', { name: 'Собрать SQL по шагам' }),
    );
    await screen.findByLabelText('SQL шага 1');
    fireEvent.change(screen.getByLabelText('SQL шага 1'), {
      target: { value: `${baseSql} ` },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить черновик' }));
    await waitFor(() => expect(saveDraft).toHaveBeenCalledTimes(1));
    fireEvent.change(screen.getByLabelText('SQL шага 1'), {
      target: { value: `${baseSql}  ` },
    });
    await new Promise((resolve) => setTimeout(resolve, 750));
    await act(async () => finishFirst({ version: 4 }));
    await waitFor(() =>
      expect(saveDraft).toHaveBeenLastCalledWith(
        'guided-project',
        expect.objectContaining({
          expected_draft_version: 4,
          steps: [expect.objectContaining({ sql: `${baseSql}  ` })],
        }),
      ),
    );
    await screen.findByText('Черновик сохранён на сервере');
  });

  it('allows editing a saved result while keeping a new revision blocked until result cleanup', async () => {
    const sql =
      'SELECT o.order_id AS order_id FROM dwh.order_fact AS o WHERE o.order_id = :wanted LIMIT :row_limit';
    render(
      <GuidedSqlForm
        project={
          {
            ...project,
            artifact_ids: { schema: 'schema-1', query: 'query-1' },
            artifacts: {
              query: {
                mode: 'manual',
                parameters: [
                  { name: 'wanted', type: 'text', value: 'A-1' },
                  { name: 'row_limit', type: 'integer', value: 100 },
                ],
              },
            },
          } as never
        }
        initialSql={sql}
        saveDisabledReason="Сначала удалите временные данные."
        onSaved={jest.fn()}
      />,
    );
    fireEvent.click(
      screen.getByRole('button', { name: 'Редактировать финальный SQL' }),
    );
    await screen.findByLabelText('SQL шага 1');
    expect(screen.getByLabelText('SQL шага 1')).toHaveValue(sql);
    expect(
      (screen.getByLabelText('Параметры JSON шага 1') as HTMLTextAreaElement)
        .value,
    ).toContain('wanted');
    fireEvent.change(screen.getByLabelText('SQL шага 1'), {
      target: { value: `${sql} ` },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Проверить шаг' }));
    await waitFor(() =>
      expect(validate).toHaveBeenCalledWith('guided-project', 4, `${sql} `, [
        { name: 'wanted', type: 'text', value: 'A-1' },
        { name: 'row_limit', type: 'integer', value: 100 },
      ]),
    );
    expect(
      screen.getByRole('button', { name: 'Сохранить проверенный SQL' }),
    ).toBeDisabled();
    expect(save).not.toHaveBeenCalled();
  });

  it('recovers an edit after switching projects before the debounce expires', async () => {
    const view = render(
      <GuidedSqlForm project={project as never} onSaved={jest.fn()} />,
    );
    fireEvent.click(
      screen.getByRole('button', { name: 'Собрать SQL по шагам' }),
    );
    await screen.findByLabelText('SQL шага 1');
    fireEvent.change(screen.getByLabelText('SQL шага 1'), {
      target: { value: baseSql },
    });
    view.rerender(
      <GuidedSqlForm
        project={{ ...project, id: 'second-project' } as never}
        onSaved={jest.fn()}
      />,
    );
    view.rerender(
      <GuidedSqlForm project={project as never} onSaved={jest.fn()} />,
    );
    fireEvent.click(
      screen.getByRole('button', { name: 'Собрать SQL по шагам' }),
    );
    expect(await screen.findByLabelText('SQL шага 1')).toHaveValue(baseSql);
  });

  it('keeps another project draft version when a previous project save finishes', async () => {
    let finishFirst: (value: { version: number }) => void = () => undefined;
    fetchWorkspace.mockImplementation((id: string) =>
      Promise.resolve({
        draft: {
          version: id === project.id ? 3 : 8,
          base_query_artifact_id: null,
          steps: [{ title: 'Base', sql: baseSql, parameters: '[]' }],
        },
      }),
    );
    saveDraft
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            finishFirst = resolve;
          }),
      )
      .mockResolvedValue({ version: 9 });
    const view = render(
      <GuidedSqlForm project={project as never} onSaved={jest.fn()} />,
    );
    fireEvent.click(
      screen.getByRole('button', { name: 'Собрать SQL по шагам' }),
    );
    await screen.findByLabelText('SQL шага 1');
    fireEvent.change(screen.getByLabelText('SQL шага 1'), {
      target: { value: baseSql + ' ' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить черновик' }));
    await waitFor(() => expect(saveDraft).toHaveBeenCalledTimes(1));
    view.rerender(
      <GuidedSqlForm
        project={{ ...project, id: 'second-project' } as never}
        onSaved={jest.fn()}
      />,
    );
    fireEvent.click(
      screen.getByRole('button', { name: 'Собрать SQL по шагам' }),
    );
    await screen.findByLabelText('SQL шага 1');
    await act(async () => finishFirst({ version: 4 }));
    fireEvent.change(screen.getByLabelText('SQL шага 1'), {
      target: { value: joinedSql },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить черновик' }));
    await waitFor(() =>
      expect(saveDraft).toHaveBeenLastCalledWith(
        'second-project',
        expect.objectContaining({ expected_draft_version: 8 }),
      ),
    );
  });

  it('requires a choice before restoring a buffered edit over a newer server draft', async () => {
    window.sessionStorage.setItem(
      'sql-pending:guided-project:schema-1:undefined',
      JSON.stringify({
        version: 2,
        steps: [{ title: 'Pending', sql: joinedSql, parameters: '[]' }],
      }),
    );
    fetchWorkspace.mockResolvedValue({
      draft: {
        version: 3,
        base_query_artifact_id: null,
        steps: [{ title: 'Server', sql: baseSql, parameters: '[]' }],
      },
    });
    render(<GuidedSqlForm project={project as never} onSaved={jest.fn()} />);
    fireEvent.click(
      screen.getByRole('button', { name: 'Собрать SQL по шагам' }),
    );
    await screen.findByRole('button', {
      name: 'Восстановить изменения вкладки',
    });
    expect(screen.getByLabelText('SQL шага 1')).toHaveValue(baseSql);
    expect(screen.getByLabelText('SQL шага 1')).toBeDisabled();
    expect(saveDraft).not.toHaveBeenCalled();
    fireEvent.click(
      screen.getByRole('button', { name: 'Восстановить изменения вкладки' }),
    );
    expect(screen.getByLabelText('SQL шага 1')).toHaveValue(joinedSql);
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить черновик' }));
    await waitFor(() =>
      expect(saveDraft).toHaveBeenCalledWith(
        project.id,
        expect.objectContaining({ expected_draft_version: 3 }),
      ),
    );
  });

  it('does not restore an edit reverted before autosave', async () => {
    fetchWorkspace.mockResolvedValue({
      draft: {
        version: 3,
        base_query_artifact_id: null,
        steps: [{ title: 'Base', sql: baseSql, parameters: '[]' }],
      },
    });
    const view = render(
      <GuidedSqlForm project={project as never} onSaved={jest.fn()} />,
    );
    fireEvent.click(
      screen.getByRole('button', { name: 'Собрать SQL по шагам' }),
    );
    await screen.findByLabelText('SQL шага 1');
    fireEvent.change(screen.getByLabelText('SQL шага 1'), {
      target: { value: joinedSql },
    });
    fireEvent.change(screen.getByLabelText('SQL шага 1'), {
      target: { value: baseSql },
    });
    expect(
      window.sessionStorage.getItem(
        'sql-pending:guided-project:schema-1:undefined',
      ),
    ).toBeNull();
    view.rerender(
      <GuidedSqlForm
        project={{ ...project, id: 'second' } as never}
        onSaved={jest.fn()}
      />,
    );
    view.rerender(
      <GuidedSqlForm project={project as never} onSaved={jest.fn()} />,
    );
    fireEvent.click(
      screen.getByRole('button', { name: 'Собрать SQL по шагам' }),
    );
    expect(await screen.findByLabelText('SQL шага 1')).toHaveValue(baseSql);
  });

  it('saves the reverted text after a different in-flight draft completes', async () => {
    fetchWorkspace.mockResolvedValue({
      draft: {
        version: 3,
        base_query_artifact_id: null,
        steps: [{ title: 'Base', sql: baseSql, parameters: '[]' }],
      },
    });
    let finishFirst: (value: { version: number }) => void = () => undefined;
    saveDraft
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            finishFirst = resolve;
          }),
      )
      .mockResolvedValue({ version: 5 });
    render(<GuidedSqlForm project={project as never} onSaved={jest.fn()} />);
    fireEvent.click(
      screen.getByRole('button', { name: 'Собрать SQL по шагам' }),
    );
    await screen.findByLabelText('SQL шага 1');
    fireEvent.change(screen.getByLabelText('SQL шага 1'), {
      target: { value: joinedSql },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить черновик' }));
    await waitFor(() => expect(saveDraft).toHaveBeenCalledTimes(1));
    fireEvent.change(screen.getByLabelText('SQL шага 1'), {
      target: { value: baseSql },
    });
    await act(async () => finishFirst({ version: 4 }));
    await waitFor(
      () =>
        expect(saveDraft).toHaveBeenLastCalledWith(
          project.id,
          expect.objectContaining({
            expected_draft_version: 4,
            steps: [expect.objectContaining({ sql: baseSql })],
          }),
        ),
      { timeout: 2000 },
    );
    await screen.findByText('Черновик сохранён на сервере');
    expect(
      window.sessionStorage.getItem(
        'sql-pending:guided-project:schema-1:undefined',
      ),
    ).toBeNull();
  });
});
