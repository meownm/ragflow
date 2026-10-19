import * as service from '@/services/linked-document-service';
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { DocumentWorkbench } from './document-workbench';

jest.mock('@/utils/next-request', () => ({
  __esModule: true,
  default: { get: jest.fn(), post: jest.fn(), put: jest.fn() },
}));
jest.mock('@/services/source-workbench-service', () => ({
  sourceRequestError: (error: Error) => error.message,
}));

jest.mock('@/services/linked-document-service', () => ({
  ...jest.requireActual('@/services/linked-document-service'),
  listWorkspaceDocuments: jest.fn(),
  getDocumentOverview: jest.fn(),
  createWorkspaceDocument: jest.fn(),
  getWorkspaceDocument: jest.fn(),
  refreshWorkspaceDocument: jest.fn(),
  prepareDocumentChanges: jest.fn(),
  decideDocumentChange: jest.fn(),
  updateWorkspaceDocument: jest.fn(),
  cancelDocumentJob: jest.fn(),
  verifyDocumentSelection: jest.fn(),
  saveDocumentDependency: jest.fn(),
  getDocumentRevision: jest.fn(),
}));
jest.mock('./source-article-content', () => ({
  SourceArticleContent: ({ text, label }: { text: string; label: string }) => (
    <article aria-label={label}>
      <pre>{text}</pre>
    </article>
  ),
}));
jest.mock('./document-settings', () => ({
  policies: {
    working: 'Рабочий документ',
    approval: 'Изменения по согласованию',
    locked: 'Зафиксирован',
  },
  WorkspaceMembers: () => null,
  DocumentSettings: () => null,
}));

const workspace: import('@/services/source-workbench-service').SourceWorkspace =
  {
    id: 'workspace',
    role: 'owner',
    title: 'Проект',
    dataset_ids: [],
    selected_documents: [],
    search_queries: [],
    version: 1,
    created_at: '',
    updated_at: '',
  };
const sourceId = 'source';
const dependency: import('@/services/linked-document-service').Dependency = {
  id: 'dependency',
  source_id: sourceId,
  target_id: 'target',
  relation: 'requires',
  required: true,
  source_scope: [],
  target_scope: [],
  refresh_on: 'saved',
  maintenance: 'manual',
  blocks_approval: true,
  reviewed_revision_id: null,
  version: 1,
};
const initial: import('@/services/linked-document-service').WorkspaceDocument =
  {
    id: 'target',
    title: 'ТЗ',
    document_type: 'technical_specification',
    change_policy: 'approval',
    source_kind: 'local',
    current_revision_id: 'rev1',
    approved_revision_id: null,
    responsible_id: 'author',
    approver_id: 'author',
    version: 3,
    provenance_state: 'resolved',
    content: '# Хранение\n30 дней\n# Ручной раздел\nАвторский текст\n',
    sections: [
      { id: 'section', text: '# Хранение\n30 дней\n' },
      { id: 'manual', text: '# Ручной раздел\nАвторский текст\n' },
    ],
    source_ref: null,
    dependencies: [dependency],
    impacts: [],
    waiting: [],
    revisions: [
      {
        id: 'rev1',
        number: 1,
        parent_id: null,
        content_hash: '',
        author_id: 'author',
        created_at: '2026-10-18T10:00:00Z',
      },
    ],
    changes: [],
    jobs: [],
    capabilities: { edit: true, manage: true, approve: true },
  };
const source: import('@/services/linked-document-service').WorkspaceDocument = {
  ...initial,
  id: sourceId,
  title: 'Бизнес-требования',
  document_type: 'business_requirements',
  change_policy: 'locked',
  source_kind: 'indexed',
  content: '# Хранение\n90 дней\n',
  sections: [{ id: 'source-section', text: '# Хранение\n90 дней\n' }],
  dependencies: [],
};
const change: import('@/services/linked-document-service').ChangeSet = {
  id: 'change',
  base_revision_id: 'rev1',
  document_version: 3,
  status: 'ready',
  inputs: [
    {
      dependency_id: 'dependency',
      source_id: sourceId,
      source_revision_id: 'source-rev2',
      title: source.title,
      policy: dependency,
      sections: source.sections,
      previous_sections: null,
    },
  ],
  plan: {
    outcome: 'changes_required',
    summary: 'Срок хранения изменился с 30 до 90 дней',
    operations: [
      {
        op: 'replace',
        section_id: 'section',
        text: '# Хранение\n90 дней\n',
        source_ids: ['dependency'],
      },
    ],
    source_results: [
      {
        dependency_id: 'dependency',
        disposition: 'changed',
        explanation: 'Учесть новый срок',
      },
    ],
  },
  verification: { verdict: 'PASS', issues: [] },
};

beforeEach(() => {
  jest.clearAllMocks();
  jest
    .mocked(service.listWorkspaceDocuments)
    .mockResolvedValue([initial, source]);
  jest.mocked(service.getWorkspaceDocument).mockResolvedValue(initial);
  jest.mocked(service.getDocumentOverview).mockImplementation(async () => ({
    items: (await service.listWorkspaceDocuments(workspace.id)).map((item) => ({
      ...item,
      responsible_name: 'Автор',
      state: 'needs_review',
      action: 'text',
      mine: true,
      reasons: [],
      source_ids: [],
      dependent_ids: [],
    })),
    can_manage: true,
    readiness: {
      configured: false,
      documents_agreed: false,
      maintenance_complete: false,
      required_count: 0,
      blockers: [],
    },
  }));
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

const running = {
  ...initial,
  jobs: [
    {
      id: 'job',
      status: 'running' as const,
      stage: 'planning',
      result_id: null,
      error: null,
    },
  ],
};

function overviewData(
  item: typeof initial,
  state: string,
): import('@/services/linked-document-service').DocumentOverviewData {
  return {
    items: [
      {
        ...item,
        responsible_name: 'Автор',
        state,
        action: 'text',
        mine: true,
        reasons: [],
        source_ids: [],
        dependent_ids: [],
      },
    ],
    can_manage: true,
    readiness: {
      configured: false,
      documents_agreed: false,
      maintenance_complete: false,
      required_count: 0,
      blockers: [],
    },
  };
}

test('initial delayed overview cannot hide a newly created document', async () => {
  const first =
    deferred<
      import('@/services/linked-document-service').DocumentOverviewData
    >();
  const created = { ...initial, id: 'new', title: 'Новый документ' };
  jest
    .mocked(service.getDocumentOverview)
    .mockReturnValueOnce(first.promise)
    .mockResolvedValue(overviewData(created, 'draft'));
  jest.mocked(service.createWorkspaceDocument).mockResolvedValue(created);
  render(<DocumentWorkbench workspace={workspace} />);
  fireEvent.change(
    screen.getByRole('textbox', { name: 'Название нового документа' }),
    { target: { value: created.title } },
  );
  fireEvent.click(screen.getByRole('button', { name: 'Создать документ' }));
  expect(
    await screen.findByRole('button', { name: /^Новый документ:/ }),
  ).toBeVisible();
  await act(async () => {
    first.resolve(overviewData(initial, 'needs_review'));
  });
  expect(
    screen.getByRole('button', { name: /^Новый документ:/ }),
  ).toBeVisible();
  expect(
    screen.queryByRole('button', { name: /^ТЗ:/ }),
  ).not.toBeInTheDocument();
});

test('completed background proposal refreshes overview without another action', async () => {
  jest.useFakeTimers();
  try {
    jest
      .mocked(service.getDocumentOverview)
      .mockResolvedValueOnce(overviewData(initial, 'preparing'))
      .mockResolvedValueOnce(overviewData(initial, 'preparing'))
      .mockResolvedValue(overviewData(initial, 'awaiting_decision'));
    jest
      .mocked(service.getWorkspaceDocument)
      .mockResolvedValueOnce(running)
      .mockResolvedValue({
        ...initial,
        changes: [change],
        jobs: [
          {
            ...running.jobs[0],
            status: 'done',
            stage: 'done',
            result_id: change.id,
          },
        ],
      });
    render(<DocumentWorkbench workspace={workspace} />);
    await openTarget();
    expect(
      screen.getByText('Техническое задание · Подготовка предложений'),
    ).toBeVisible();
    await act(async () => {
      jest.advanceTimersByTime(2000);
    });
    expect(
      screen.getByText('Техническое задание · Ожидает согласования'),
    ).toBeVisible();
    expect(service.getDocumentOverview).toHaveBeenCalledTimes(3); // Initial, open, completion.
  } finally {
    jest.useRealTimers();
  }
});

test('returning from the source editor refreshes the active document before the next save', async () => {
  const updated = {
    ...initial,
    change_policy: 'working' as const,
    version: 4,
    content: 'Текст из обработки статей',
    sections: [{ id: 'section', text: 'Текст из обработки статей' }],
  };
  const { rerender } = render(<DocumentWorkbench workspace={workspace} />);
  await openTarget();
  rerender(<DocumentWorkbench workspace={workspace} visible={false} />);
  jest.mocked(service.getWorkspaceDocument).mockResolvedValue(updated);
  rerender(<DocumentWorkbench workspace={workspace} />);
  expect(
    await screen.findByRole('article', { name: 'Текст из обработки статей' }),
  ).toHaveTextContent(updated.content);
  fireEvent.click(screen.getByRole('button', { name: 'Редактировать текст' }));
  fireEvent.change(
    screen.getByRole('textbox', { name: 'Содержание документа' }),
    { target: { value: 'Новая ручная правка' } },
  );
  jest.mocked(service.updateWorkspaceDocument).mockResolvedValue({
    ...updated,
    version: 5,
    content: 'Новая ручная правка',
  });
  fireEvent.click(
    screen.getByRole('button', { name: 'Сохранить новую редакцию' }),
  );
  await waitFor(() =>
    expect(service.updateWorkspaceDocument).toHaveBeenCalledWith(
      'workspace',
      'target',
      { content: 'Новая ручная правка', expected_version: 4 },
    ),
  );
});

test('returning does not overwrite dirty manual text', async () => {
  const { rerender } = render(<DocumentWorkbench workspace={workspace} />);
  await openTarget();
  fireEvent.click(screen.getByRole('button', { name: 'Редактировать текст' }));
  fireEvent.change(
    screen.getByRole('textbox', { name: 'Содержание документа' }),
    { target: { value: 'Несохранённый текст' } },
  );
  rerender(<DocumentWorkbench workspace={workspace} visible={false} />);
  rerender(<DocumentWorkbench workspace={workspace} />);
  expect(
    screen.getByRole('textbox', { name: 'Содержание документа' }),
  ).toHaveValue('Несохранённый текст');
  expect(service.getWorkspaceDocument).toHaveBeenCalledTimes(1);
});

test('polling waits for its previous request and pauses while hidden', async () => {
  jest.useFakeTimers();
  try {
    jest.mocked(service.getWorkspaceDocument).mockResolvedValueOnce(running);
    const { rerender } = render(<DocumentWorkbench workspace={workspace} />);
    await openTarget();
    const response = deferred<typeof running>();
    jest.mocked(service.getWorkspaceDocument).mockReturnValue(response.promise);
    await act(async () => {
      jest.advanceTimersByTime(2000);
    });
    expect(service.getWorkspaceDocument).toHaveBeenCalledTimes(2);
    await act(async () => {
      jest.advanceTimersByTime(6000);
    });
    expect(service.getWorkspaceDocument).toHaveBeenCalledTimes(2);
    await act(async () => {
      response.resolve(running);
    });
    await act(async () => {
      jest.advanceTimersByTime(2000);
    });
    expect(service.getWorkspaceDocument).toHaveBeenCalledTimes(3);
    rerender(<DocumentWorkbench workspace={workspace} visible={false} />);
    await act(async () => {
      jest.advanceTimersByTime(6000);
    });
    expect(service.getWorkspaceDocument).toHaveBeenCalledTimes(3);
  } finally {
    jest.useRealTimers();
  }
});

test('an old poll cannot overwrite a completed foreground refresh', async () => {
  jest.mocked(service.getWorkspaceDocument).mockResolvedValueOnce(running);
  const { rerender } = render(<DocumentWorkbench workspace={workspace} />);
  await openTarget();
  const response = deferred<typeof running>();
  jest.mocked(service.getWorkspaceDocument).mockReturnValue(response.promise);
  rerender(<DocumentWorkbench workspace={workspace} visible={false} />);
  rerender(<DocumentWorkbench workspace={workspace} />);
  const updated = {
    ...initial,
    version: 4,
    sections: [{ id: 'section', text: 'Свежий текст' }],
    content: 'Свежий текст',
  };
  jest.mocked(service.refreshWorkspaceDocument).mockResolvedValue(updated);
  fireEvent.click(screen.getByRole('button', { name: 'Проверить источники' }));
  expect(
    await screen.findByRole('article', { name: 'Свежий текст' }),
  ).toHaveTextContent('Свежий текст');
  await act(async () => {
    response.resolve(running);
  });
  expect(
    screen.getByRole('article', { name: 'Свежий текст' }),
  ).toHaveTextContent('Свежий текст');
  expect(
    screen.queryByRole('article', { name: 'Хранение' }),
  ).not.toBeInTheDocument();
});

test.each(['blocked', 'applied', 'rejected', 'stale'] as const)(
  'a %s proposal does not offer a misleading document preview',
  async (status) => {
    const proposal = status === 'stale' ? change : { ...change, status };
    jest.mocked(service.getWorkspaceDocument).mockResolvedValue({
      ...initial,
      version: status === 'stale' ? 4 : 3,
      changes: [proposal],
    });
    render(<DocumentWorkbench workspace={workspace} />);
    await openTarget();
    fireEvent.click(screen.getByRole('button', { name: 'Изменения' }));
    if (status === 'applied' || status === 'rejected')
      fireEvent.click(
        screen.getByRole('button', { name: /Предложены правки/ }),
      );
    expect(
      screen.queryByRole('button', { name: 'Посмотреть правки в документе' }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByText(/Предварительный просмотр доступен только/),
    ).toBeInTheDocument();
  },
);

async function openTarget() {
  fireEvent.click(await screen.findByRole('button', { name: /^ТЗ/ }));
  await screen.findByRole('button', { name: 'Редактировать текст' });
}

test('source update shows a proposal in the matching section and applies only after confirmation', async () => {
  const ready = { ...initial, changes: [change] };
  jest.mocked(service.refreshWorkspaceDocument).mockResolvedValue(initial);
  jest
    .mocked(service.getWorkspaceDocument)
    .mockResolvedValueOnce(initial)
    .mockResolvedValueOnce(ready);
  jest.mocked(service.prepareDocumentChanges).mockResolvedValue({
    id: 'job',
    status: 'queued',
    stage: 'queued',
    result_id: null,
    error: null,
  });
  jest.mocked(service.decideDocumentChange).mockResolvedValue({
    ...ready,
    version: 4,
    current_revision_id: 'rev2',
    content: initial.content.replace('30 дней', '90 дней'),
    sections: [
      { id: 'section', text: '# Хранение\n90 дней\n' },
      initial.sections[1],
    ],
    changes: [{ ...change, status: 'applied' }],
  });
  render(<DocumentWorkbench workspace={workspace} />);
  await openTarget();
  fireEvent.click(screen.getByRole('button', { name: 'Проверить источники' }));
  await waitFor(() =>
    expect(service.refreshWorkspaceDocument).toHaveBeenCalledWith(
      'workspace',
      'target',
    ),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Изменения' }));
  await waitFor(() =>
    expect(
      screen.getByRole('button', { name: 'Подготовить предложения' }),
    ).toBeEnabled(),
  );
  fireEvent.click(
    screen.getByRole('button', { name: 'Подготовить предложения' }),
  );
  await screen.findByRole('button', { name: 'Подтвердить и применить' });
  expect(service.decideDocumentChange).not.toHaveBeenCalled();
  fireEvent.click(
    screen.getByRole('button', { name: 'Посмотреть правки в документе' }),
  );
  expect(
    await screen.findByRole('article', { name: 'Предложение: Хранение' }),
  ).toHaveTextContent('90 дней');
  expect(
    screen.getByRole('article', { name: 'Ручной раздел' }),
  ).toHaveTextContent('Авторский текст');
  fireEvent.click(screen.getByRole('button', { name: 'Изменения' }));
  fireEvent.click(
    screen.getByRole('button', { name: 'Подтвердить и применить' }),
  );
  await waitFor(() =>
    expect(service.decideDocumentChange).toHaveBeenCalledWith(
      'workspace',
      'target',
      'change',
      'accept',
      '',
    ),
  );
  expect(service.updateWorkspaceDocument).not.toHaveBeenCalled();
});

test('unsaved manual changes survive navigation attempts and report the busy guard', async () => {
  const onBusyChange = jest.fn();
  render(
    <DocumentWorkbench workspace={workspace} onBusyChange={onBusyChange} />,
  );
  await openTarget();
  fireEvent.click(screen.getByRole('button', { name: 'Редактировать текст' }));
  fireEvent.change(
    screen.getByRole('textbox', { name: 'Содержание документа' }),
    { target: { value: initial.content + 'Ручная правка' } },
  );
  fireEvent.click(screen.getByRole('button', { name: /^Бизнес-требования/ }));
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'Сохраните или отмените',
  );
  expect(
    screen.getByRole('textbox', { name: 'Содержание документа' }),
  ).toHaveValue(initial.content + 'Ручная правка');
  expect(service.getWorkspaceDocument).toHaveBeenCalledTimes(1);
  expect(onBusyChange).toHaveBeenLastCalledWith(true);
});

test('stale proposal cannot be confirmed and source settings are independent of content type', async () => {
  jest
    .mocked(service.getWorkspaceDocument)
    .mockImplementation(async (_workspace, id) =>
      id === sourceId ? source : { ...initial, version: 4, changes: [change] },
    );
  jest
    .mocked(service.saveDocumentDependency)
    .mockResolvedValue({ ...initial, version: 5 });
  render(<DocumentWorkbench workspace={workspace} />);
  await openTarget();
  fireEvent.click(screen.getByRole('button', { name: 'Изменения' }));
  expect(
    screen.getByRole('button', { name: 'Подтвердить и применить' }),
  ).toBeDisabled();
  expect(screen.getByRole('alert')).toHaveTextContent('Предложение устарело');
  fireEvent.click(screen.getByRole('button', { name: 'Источники и связи' }));
  fireEvent.change(
    screen.getByRole('combobox', { name: 'Документ-источник' }),
    { target: { value: sourceId } },
  );
  fireEvent.change(screen.getByRole('combobox', { name: 'Назначение связи' }), {
    target: { value: 'reference' },
  });
  fireEvent.change(
    screen.getByRole('combobox', { name: 'Когда учитывать изменения' }),
    { target: { value: 'approved' } },
  );
  await waitFor(() =>
    expect(
      screen.getByRole('button', { name: 'Сохранить связь' }),
    ).toBeEnabled(),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить связь' }));
  await waitFor(() =>
    expect(service.saveDocumentDependency).toHaveBeenCalledWith(
      'workspace',
      'target',
      expect.objectContaining({
        source_id: sourceId,
        relation: 'reference',
        required: false,
        blocks_approval: false,
        refresh_on: 'approved',
        expected_version: 4,
      }),
      undefined,
    ),
  );
});

test('switching to an external source clears the unsupported implementation trigger', async () => {
  const local = {
    ...source,
    id: 'local-source',
    title: 'Другое ТЗ',
    source_kind: 'local' as const,
  };
  jest
    .mocked(service.listWorkspaceDocuments)
    .mockResolvedValue([initial, source, local]);
  jest
    .mocked(service.getWorkspaceDocument)
    .mockImplementation(async (_workspace, id) =>
      id === local.id ? local : id === source.id ? source : initial,
    );
  jest
    .mocked(service.saveDocumentDependency)
    .mockResolvedValue({ ...initial, version: 4 });
  render(<DocumentWorkbench workspace={workspace} />);
  await openTarget();
  fireEvent.click(screen.getByRole('button', { name: 'Источники и связи' }));
  fireEvent.change(
    screen.getByRole('combobox', { name: 'Документ-источник' }),
    { target: { value: local.id } },
  );
  await screen.findByRole('option', { name: 'После подтверждения реализации' });
  fireEvent.change(
    screen.getByRole('combobox', { name: 'Когда учитывать изменения' }),
    { target: { value: 'implemented' } },
  );
  fireEvent.change(
    screen.getByRole('combobox', { name: 'Документ-источник' }),
    { target: { value: source.id } },
  );
  await waitFor(() =>
    expect(
      screen.getByRole('combobox', { name: 'Когда учитывать изменения' }),
    ).toHaveValue('saved'),
  );
  expect(
    screen.queryByRole('option', { name: 'После подтверждения реализации' }),
  ).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить связь' }));
  await waitFor(() =>
    expect(service.saveDocumentDependency).toHaveBeenCalledWith(
      'workspace',
      'target',
      expect.objectContaining({ source_id: source.id, refresh_on: 'saved' }),
      undefined,
    ),
  );
});

test('history compares immutable sections with the current document', async () => {
  jest.mocked(service.getDocumentRevision).mockResolvedValue({
    ...initial.revisions[0],
    sections: [
      { id: 'section', text: '# Хранение\nПрежние 7 дней\n' },
      initial.sections[1],
    ],
  });
  render(<DocumentWorkbench workspace={workspace} />);
  await openTarget();
  fireEvent.click(screen.getByRole('button', { name: 'История' }));
  fireEvent.click(screen.getByRole('button', { name: /^Редакция 1/ }));
  expect(
    await screen.findByRole('article', { name: 'Прежняя редакция' }),
  ).toHaveTextContent('Прежние 7 дней');
  expect(
    screen.getByRole('article', { name: 'Текущая редакция' }),
  ).toHaveTextContent('30 дней');
});

test('cancelling a background job preserves unsaved manual text', async () => {
  jest.mocked(service.getWorkspaceDocument).mockResolvedValue({
    ...initial,
    jobs: [
      {
        id: 'job',
        status: 'running',
        stage: 'planning',
        result_id: null,
        error: null,
      },
    ],
  });
  jest.mocked(service.cancelDocumentJob).mockResolvedValue({
    id: 'job',
    status: 'cancelled',
    stage: 'cancelled',
    result_id: null,
    error: null,
  });
  render(<DocumentWorkbench workspace={workspace} />);
  await openTarget();
  fireEvent.click(screen.getByRole('button', { name: 'Редактировать текст' }));
  fireEvent.change(
    screen.getByRole('textbox', { name: 'Содержание документа' }),
    { target: { value: initial.content + 'Не потерять' } },
  );
  fireEvent.click(screen.getByRole('button', { name: 'Отменить задание' }));
  await waitFor(() =>
    expect(service.cancelDocumentJob).toHaveBeenCalledWith(
      'workspace',
      'target',
      'job',
    ),
  );
  expect(
    screen.getByRole('textbox', { name: 'Содержание документа' }),
  ).toHaveValue(initial.content + 'Не потерять');
});

test('a selected independent group is verified before any application', async () => {
  const second = { ...dependency, id: 'second', source_id: 'other' };
  const grouped = {
    ...change,
    inputs: [
      ...change.inputs,
      {
        ...change.inputs[0],
        dependency_id: 'second',
        source_id: 'other',
        title: 'Другие требования',
        policy: second,
      },
    ],
    plan: {
      ...change.plan,
      operations: [
        ...change.plan.operations,
        {
          op: 'replace' as const,
          section_id: 'manual',
          text: '# Ручной раздел\nНовое правило',
          source_ids: ['second'],
        },
      ],
      source_results: [
        ...change.plan.source_results,
        {
          dependency_id: 'second',
          disposition: 'changed',
          explanation: 'Вторая правка',
        },
      ],
    },
  };
  jest
    .mocked(service.getWorkspaceDocument)
    .mockResolvedValue({ ...initial, changes: [grouped] });
  jest.mocked(service.verifyDocumentSelection).mockResolvedValue({
    id: 'subset',
    status: 'queued',
    stage: 'queued',
    result_id: null,
    error: null,
  });
  render(<DocumentWorkbench workspace={workspace} />);
  await openTarget();
  fireEvent.click(screen.getByRole('button', { name: 'Изменения' }));
  fireEvent.click(
    await screen.findByRole('checkbox', { name: 'Ручной раздел' }),
  );
  fireEvent.click(
    screen.getByRole('button', { name: 'Проверить выбранные правки' }),
  );
  await waitFor(() =>
    expect(service.verifyDocumentSelection).toHaveBeenCalledWith(
      'workspace',
      'target',
      'change',
      ['section'],
    ),
  );
  expect(service.decideDocumentChange).not.toHaveBeenCalled();
});

test('manual approval submission preserves canonical text until an explicit decision', async () => {
  const candidate = initial.content.replace('30 дней', '90 дней');
  const manual: import('@/services/linked-document-service').ChangeSet = {
    ...change,
    origin: 'manual',
    author_id: 'author',
    author_name: 'Автор',
    candidate_sections: [
      { ...initial.sections[0], text: '# Хранение\n90 дней\n' },
      initial.sections[1],
    ],
    verification: { verdict: 'HUMAN_REQUIRED', issues: [] },
    plan: {
      ...change.plan,
      operations: [{ ...change.plan.operations[0], source_ids: [] }],
      source_results: [],
    },
  };
  const submitted = { ...initial, changes: [manual] };
  jest.mocked(service.updateWorkspaceDocument).mockResolvedValue(submitted);
  jest.mocked(service.decideDocumentChange).mockResolvedValue({
    ...submitted,
    content: candidate,
    sections: manual.candidate_sections!,
    version: 4,
    changes: [{ ...manual, status: 'applied' }],
  });
  render(<DocumentWorkbench workspace={workspace} />);
  await openTarget();
  fireEvent.click(screen.getByRole('button', { name: 'Редактировать текст' }));
  fireEvent.change(
    screen.getByRole('textbox', { name: 'Содержание документа' }),
    { target: { value: candidate } },
  );
  fireEvent.click(
    screen.getByRole('button', { name: 'Отправить на согласование' }),
  );
  expect(
    await screen.findByRole('article', { name: 'Предложение: Хранение' }),
  ).toHaveTextContent('90 дней');
  expect(service.decideDocumentChange).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Изменения' }));
  expect(
    await screen.findByRole('heading', { name: 'Согласование ручной правки' }),
  ).toBeInTheDocument();
  expect(screen.getByText('Автор: Автор')).toBeInTheDocument();
  expect(
    screen.queryByRole('group', { name: 'Независимые группы правок' }),
  ).not.toBeInTheDocument();
  fireEvent.click(
    screen.getByRole('button', { name: 'Подтвердить и применить' }),
  );
  await waitFor(() =>
    expect(service.decideDocumentChange).toHaveBeenCalledWith(
      'workspace',
      'target',
      'change',
      'accept',
      '',
    ),
  );
});

test('cancelled API stage is displayed as cancelled', async () => {
  jest.mocked(service.getWorkspaceDocument).mockResolvedValue({
    ...initial,
    jobs: [
      {
        id: 'job',
        status: 'cancelled',
        stage: 'cancelled',
        result_id: null,
        error: null,
      },
    ],
  });
  render(<DocumentWorkbench workspace={workspace} />);
  await openTarget();
  expect(screen.getByRole('status')).toHaveTextContent('Отменено');
  expect(
    screen.queryByRole('button', { name: 'Отменить задание' }),
  ).not.toBeInTheDocument();
});
