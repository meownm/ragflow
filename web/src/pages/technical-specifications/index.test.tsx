import {
  exportSpecification,
  getSpecification,
  getSpecificationDraft,
  listSpecifications,
  saveSpecification,
  saveSpecificationDraft,
  specificationAction,
  specificationHistory,
  specificationRevision,
} from '@/services/technical-specification-service';
import { downloadFileFromBlob } from '@/utils/file-util';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { createMemoryRouter, Link, RouterProvider } from 'react-router';
import TechnicalSpecifications, { SpecificationKeys } from './index';
import { draftKey, readDraft, writeDraft } from './model';
type SavedDraft = import('./model').SavedDraft;
type Specification = import('./model').Specification;
type Revision = import('./model').Revision;

jest.mock('@/hooks/use-user-setting-request', () => ({
  useFetchUserInfo: () => ({ data: { id: 'actor' } }),
}));
jest.mock('@/pages/business-documents/components/document-markdown', () => ({
  DocumentMarkdown: () => null,
}));
jest.mock('@/services/source-workbench-service', () => ({
  listSourceWorkspaces: async () => [{ id: 'project', title: 'Проект' }],
  getSourceWorkspace: async () => ({
    id: 'project',
    title: 'Проект',
    role: 'owner',
  }),
  sourceRequestError: (_error: unknown, fallback: string) => fallback,
}));
jest.mock('@/services/technical-specification-service', () => ({
  exportSpecification: jest.fn(),
  getSpecification: jest.fn(),
  listSpecifications: jest.fn(),
  saveSpecification: jest.fn(),
  specificationAction: jest.fn(),
  getSpecificationDraft: jest.fn(),
  saveSpecificationDraft: jest.fn(),
  specificationHistory: jest.fn(),
  specificationRevision: jest.fn(),
}));

jest.mock('@/utils/file-util', () => ({ downloadFileFromBlob: jest.fn() }));

const specification: Specification = {
  id: 'doc',
  workspace_id: 'project',
  title: 'Тестовое ТЗ',
  version: 1,
  revision: 1,
  sections: Object.fromEntries(
    Array.from({ length: 9 }, (_, index) => [
      String(index + 1),
      'Текст раздела',
    ]),
  ),
  messages: [],
  comments: [],
  proposals: [],
  people: {},
  eva: null,
};
const local: SavedDraft = {
  version: 1,
  draft: {
    message_id: 'local',
    text: 'Мой вопрос',
    context: {
      document_id: 'doc',
      revision: 1,
      scope: 'selection',
      anchor: { section: '1', quote: 'Текст', prefix: '', suffix: ' раздела' },
    },
  },
};
const remote: SavedDraft = {
  version: 2,
  draft: {
    ...local.draft,
    message_id: 'remote',
    text: 'Другой вопрос',
    context: { ...local.draft.context, scope: '2', anchor: null },
  },
};
const key = draftKey('actor', 'project', 'doc');
let router: ReturnType<(typeof import('react-router'))['createMemoryRouter']>;

async function openChat() {
  jest.mocked(getSpecification).mockResolvedValue(specification);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  router = createMemoryRouter(
    [
      {
        path: '/technical-specifications',
        element: (
          <>
            <Link to="/other">Общая навигация</Link>
            <TechnicalSpecifications />
          </>
        ),
      },
      { path: '/other', element: <p>Другой экран</p> },
    ],
    {
      initialEntries: [
        '/other',
        '/technical-specifications?workspace=project&document=doc',
      ],
      initialIndex: 1,
    },
  );
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  fireEvent.click(await screen.findByRole('button', { name: 'Чат' }));
  return client;
}

beforeEach(() => {
  localStorage.clear();
  jest.clearAllMocks();
  jest.useFakeTimers();
  jest.mocked(listSpecifications).mockResolvedValue([specification]);
  writeDraft(key, local);
  jest.mocked(getSpecificationDraft).mockResolvedValue(remote);
  jest
    .mocked(saveSpecificationDraft)
    .mockImplementation(async (_workspace, _id, value) => ({
      ...value,
      version: value.version + 1,
    }));
});

test('Eva updates the linked page with one action and reports the exported revision', async () => {
  const client = await openChat();
  const linked: Specification = {
    ...specification,
    version: 2,
    eva: {
      page_url: 'https://eva.test/page',
      remote_version: 'v1',
      document_name: 'Страница ТЗ',
    },
  };
  await act(async () => {
    client.setQueryData(
      SpecificationKeys.document('actor', 'project', 'doc'),
      linked,
    );
  });
  jest.mocked(specificationAction).mockResolvedValue({
    ...linked,
    version: 4,
    eva_export: { status: 'published', revision: 1 },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Eva' }));
  const update = await screen.findByRole('button', { name: 'Обновить в Eva' });
  await act(async () => {
    fireEvent.click(update);
  });
  await waitFor(() =>
    expect(specificationAction).toHaveBeenCalledWith(
      'project',
      'doc',
      'publish-eva',
      { version: 2 },
    ),
  );
  await screen.findByText('Страница Eva обновлена');
  expect(screen.getByText('В Eva выгружена редакция 1.')).toBeInTheDocument();
});

test('an unbound specification can select an existing Eva page without replacing local text', async () => {
  await openChat();
  jest.mocked(specificationAction).mockResolvedValue({
    ...specification,
    version: 2,
    eva: { page_url: 'https://eva.test/page', remote_version: 'v1' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Eva' }));
  fireEvent.change(screen.getByLabelText('Ссылка на страницу Eva'), {
    target: { value: 'https://eva.test/page' },
  });
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'Связать страницу' }));
  });
  await waitFor(() =>
    expect(specificationAction).toHaveBeenCalledWith(
      'project',
      'doc',
      'connect-eva',
      { version: 1, url: 'https://eva.test/page' },
    ),
  );
  await screen.findByText('Страница Eva связана. Текст ТЗ сохранён');
  expect(
    screen.getByRole('button', { name: 'Обновить в Eva' }),
  ).toBeInTheDocument();
});
afterEach(() => {
  router?.dispose();
  jest.useRealTimers();
  jest.restoreAllMocks();
});

test('export downloads only the server-authorized saved revision', async () => {
  jest.mocked(exportSpecification).mockResolvedValue({
    filename: 'ТЗ-r1.md',
    mime_type: 'text/markdown;charset=utf-8',
    content: '## 1. Назначение и цель\n\nТекст',
    revision: 1,
  });
  await openChat();
  fireEvent.click(screen.getByRole('button', { name: 'Выгрузить .md' }));
  await waitFor(() =>
    expect(downloadFileFromBlob).toHaveBeenCalledWith(
      expect.any(Blob),
      'ТЗ-r1.md',
    ),
  );
  expect(exportSpecification).toHaveBeenCalledWith('project', 'doc', 1);
  fireEvent.click(screen.getByRole('button', { name: 'Документ' }));
  fireEvent.change(screen.getByLabelText('Раздел 1'), {
    target: { value: 'Несохранённый текст' },
  });
  expect(screen.getByRole('button', { name: 'Выгрузить .md' })).toBeDisabled();
});

test('a failed export creates no file and preserves the chat draft', async () => {
  jest.mocked(exportSpecification).mockRejectedValue(new Error('conflict'));
  await openChat();
  fireEvent.click(screen.getByRole('button', { name: 'Выгрузить .md' }));
  await screen.findByText(
    'Не удалось выгрузить ТЗ. Проверьте доступ и актуальность редакции',
  );
  expect(downloadFileFromBlob).not.toHaveBeenCalled();
  expect(
    screen.getByRole('textbox', { name: 'Сообщение помощнику' }),
  ).toHaveValue(local.draft.text);
});

test('global links and browser Back cannot discard manual edits after a storage failure', async () => {
  await openChat();
  fireEvent.click(screen.getByRole('button', { name: 'Документ' }));
  jest.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
    throw new Error('quota');
  });
  fireEvent.change(screen.getByLabelText('Раздел 1'), {
    target: { value: 'Важная правка' },
  });
  fireEvent.click(screen.getByRole('link', { name: 'Общая навигация' }));
  expect(screen.getByLabelText('Раздел 1')).toHaveValue('Важная правка');
  await act(async () => {
    await router.navigate(-1);
  });
  expect(screen.getByLabelText('Раздел 1')).toHaveValue('Важная правка');
  expect(router.state.location.pathname).toBe('/technical-specifications');
});

test('chat with full context stays open until a server save confirms the exact draft', async () => {
  jest.mocked(getSpecificationDraft).mockResolvedValue(local);
  await openChat();
  const storage = jest
    .spyOn(Storage.prototype, 'setItem')
    .mockImplementation(() => {
      throw new Error('quota');
    });
  fireEvent.change(screen.getByLabelText('Сообщение помощнику'), {
    target: { value: 'Важный вопрос' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Технические задания' }));
  expect(screen.getByLabelText('Сообщение помощнику')).toHaveValue(
    'Важный вопрос',
  );
  fireEvent.click(screen.getByRole('link', { name: 'Общая навигация' }));
  expect(router.state.location.pathname).toBe('/technical-specifications');
  await act(async () => {
    jest.advanceTimersByTime(700);
  });
  expect(saveSpecificationDraft).toHaveBeenLastCalledWith(
    'project',
    'doc',
    expect.objectContaining({
      draft: expect.objectContaining({
        text: 'Важный вопрос',
        context: local.draft.context,
      }),
    }),
  );
  expect(screen.getByText('Черновик сохранён')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('link', { name: 'Общая навигация' }));
  expect(await screen.findByText('Другой экран')).toBeInTheDocument();
  storage.mockRestore();
});

test('a saved older chat snapshot cannot release a newer unsaved draft', async () => {
  jest.mocked(getSpecificationDraft).mockResolvedValue(local);
  let finish!: (value: SavedDraft) => void;
  jest.mocked(saveSpecificationDraft).mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  await openChat();
  jest.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
    throw new Error('quota');
  });
  fireEvent.change(screen.getByLabelText('Сообщение помощнику'), {
    target: { value: 'Первый вопрос' },
  });
  await act(async () => {
    jest.advanceTimersByTime(700);
  });
  const sent = jest.mocked(saveSpecificationDraft).mock.calls[0][2];
  fireEvent.change(screen.getByLabelText('Сообщение помощнику'), {
    target: { value: 'Второй вопрос' },
  });
  await act(async () => {
    finish({ ...sent, version: 2 });
  });
  fireEvent.click(screen.getByRole('link', { name: 'Общая навигация' }));
  expect(screen.getByLabelText('Сообщение помощнику')).toHaveValue(
    'Второй вопрос',
  );
});

test('unsaved comment drafts block all exits and recover when local storage returns', async () => {
  jest.mocked(getSpecificationDraft).mockResolvedValue(local);
  await openChat();
  fireEvent.click(screen.getByRole('button', { name: /Комментарии/ }));
  const storage = jest
    .spyOn(Storage.prototype, 'setItem')
    .mockImplementation(() => {
      throw new Error('quota');
    });
  fireEvent.change(screen.getByLabelText('Текст комментария'), {
    target: { value: 'Не терять комментарий' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Технические задания' }));
  expect(screen.getByLabelText('Текст комментария')).toHaveValue(
    'Не терять комментарий',
  );
  fireEvent.click(screen.getByRole('link', { name: 'Общая навигация' }));
  const unload = new Event('beforeunload', { cancelable: true });
  act(() => {
    window.dispatchEvent(unload);
  });
  expect(unload.defaultPrevented).toBe(true);
  storage.mockRestore();
  fireEvent.click(screen.getByRole('link', { name: 'Общая навигация' }));
  expect(await screen.findByText('Другой экран')).toBeInTheDocument();
  expect(localStorage.getItem(key + ':comments')).toContain(
    'Не терять комментарий',
  );
});

test('history ignores an older delayed response and restores only the displayed revision', async () => {
  jest.mocked(getSpecificationDraft).mockResolvedValue(local);
  const client = await openChat();
  const current = { ...specification, revision: 3, version: 3 };
  await act(async () => {
    client.setQueryData(
      SpecificationKeys.document('actor', 'project', 'doc'),
      current,
    );
  });
  const previous = (number: number): Revision => ({
    number,
    title: `Название ${number}`,
    sections: { ...specification.sections, '5': `Сценарий ${number}` },
    author_id: 'actor',
    reason: 'edit',
    created_at: '2026-10-20T00:00:00Z',
  });
  jest
    .mocked(specificationHistory)
    .mockResolvedValue([previous(2), previous(1)]);
  let finish!: (value: Revision) => void;
  jest
    .mocked(specificationRevision)
    .mockReturnValueOnce(
      new Promise((resolve) => {
        finish = resolve;
      }),
    )
    .mockResolvedValueOnce(previous(2));
  fireEvent.click(screen.getByRole('button', { name: 'История' }));
  expect(screen.getByLabelText('История редакций')).toHaveFocus();
  fireEvent.click(await screen.findByRole('button', { name: /Редакция 1 ·/ }));
  fireEvent.click(screen.getByRole('button', { name: /Редакция 2 ·/ }));
  await screen.findByText('Полный текст редакции 2');
  await act(async () => {
    finish(previous(1));
  });
  expect(screen.queryByText('Полный текст редакции 1')).toBeNull();
  jest.mocked(specificationAction).mockResolvedValue({
    ...current,
    version: 4,
    revision: 4,
    sections: previous(2).sections!,
  });
  fireEvent.click(
    screen.getByRole('button', { name: 'Восстановить как новую редакцию' }),
  );
  await waitFor(() =>
    expect(specificationAction).toHaveBeenCalledWith(
      'project',
      'doc',
      'restore',
      { version: 3, revision: 2 },
    ),
  );
});

test('conflicting tabs require an explicit choice and preserve the full other draft', async () => {
  await openChat();
  await screen.findByText(
    'На сервере другой черновик. Выберите вариант для продолжения.',
  );
  expect(
    (screen.getByLabelText('Сообщение помощнику') as HTMLTextAreaElement).value,
  ).toBe(local.draft.text);
  await act(async () => {
    jest.advanceTimersByTime(1000);
  });
  expect(saveSpecificationDraft).not.toHaveBeenCalled();
  fireEvent.click(
    screen.getByRole('button', { name: 'Продолжить с серверным' }),
  );
  expect(readDraft(key + ':previous')).toEqual(local);
  expect(readDraft(key)).toEqual(remote);
  expect(
    (screen.getByLabelText('Сообщение помощнику') as HTMLTextAreaElement).value,
  ).toBe(remote.draft.text);
  fireEvent.click(
    screen.getByRole('button', { name: 'Использовать этот вариант' }),
  );
  expect(readDraft(key)?.draft.context).toEqual(local.draft.context);
  expect(
    (screen.getByLabelText('Сообщение помощнику') as HTMLTextAreaElement).value,
  ).toBe(local.draft.text);
});

test('keeping the local draft saves against the reviewed server version', async () => {
  await openChat();
  fireEvent.click(
    await screen.findByRole('button', { name: 'Продолжить с моим' }),
  );
  expect(readDraft(key + ':previous')).toEqual(remote);
  await act(async () => {
    jest.advanceTimersByTime(700);
  });
  await waitFor(() =>
    expect(saveSpecificationDraft).toHaveBeenCalledWith('project', 'doc', {
      version: 2,
      draft: local.draft,
    }),
  );
});

test('a failed save checks the current server draft before another attempt', async () => {
  jest
    .mocked(getSpecificationDraft)
    .mockResolvedValueOnce(local)
    .mockResolvedValue(remote);
  jest
    .mocked(saveSpecificationDraft)
    .mockRejectedValueOnce(new Error('connection lost'));
  await openChat();
  await act(async () => {
    jest.advanceTimersByTime(700);
  });
  fireEvent.click(
    await screen.findByRole('button', {
      name: 'Повторить синхронизацию черновика',
    }),
  );
  await screen.findByText(
    'На сервере другой черновик. Выберите вариант для продолжения.',
  );
  await act(async () => {
    jest.advanceTimersByTime(700);
  });
  expect(saveSpecificationDraft).toHaveBeenCalledTimes(1);
  expect(readDraft(key)?.draft).toEqual(local.draft);
});

test('storage and network failures never report a saved local copy', async () => {
  jest.mocked(getSpecificationDraft).mockRejectedValue(new Error('offline'));
  jest.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
    throw new Error('quota');
  });
  await openChat();
  await screen.findByText(
    /Копия на устройстве не сохранена. Не закрывайте страницу/,
  );
  expect(
    (screen.getByLabelText('Сообщение помощнику') as HTMLTextAreaElement).value,
  ).toBe(local.draft.text);
});

test('server refresh on return updates the editor after a cached document', async () => {
  const client = await openChat();
  const latest = {
    ...specification,
    version: 2,
    revision: 2,
    sections: { ...specification.sections, '1': 'Вторая редакция' },
  };
  jest.mocked(getSpecification).mockResolvedValue(latest);
  fireEvent.click(screen.getByRole('button', { name: 'Технические задания' }));
  fireEvent.click(await screen.findByRole('button', { name: /Тестовое ТЗ/ }));
  await waitFor(() =>
    expect(
      client.getQueryData(
        SpecificationKeys.document('actor', 'project', 'doc'),
      ),
    ).toEqual(latest),
  );
  await waitFor(() =>
    expect(
      (screen.getByLabelText('Раздел 1') as HTMLTextAreaElement).value,
    ).toBe('Вторая редакция'),
  );
});

test('manual edits survive list navigation and reopening', async () => {
  await openChat();
  fireEvent.click(screen.getByRole('button', { name: 'Документ' }));
  fireEvent.change(screen.getByLabelText('Раздел 1'), {
    target: { value: 'Важная несохранённая правка' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Технические задания' }));
  fireEvent.click(await screen.findByRole('button', { name: /Тестовое ТЗ/ }));
  await waitFor(() =>
    expect(
      (screen.getByLabelText('Раздел 1') as HTMLTextAreaElement).value,
    ).toBe('Важная несохранённая правка'),
  );
});

test('navigation is stopped when manual edits cannot be stored', async () => {
  await openChat();
  fireEvent.click(screen.getByRole('button', { name: 'Документ' }));
  jest.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
    throw new Error('quota');
  });
  fireEvent.change(screen.getByLabelText('Раздел 1'), {
    target: { value: 'Несохранённый текст' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Технические задания' }));
  expect((screen.getByLabelText('Раздел 1') as HTMLTextAreaElement).value).toBe(
    'Несохранённый текст',
  );
  expect(
    screen.getByText(/Сохраните ТЗ перед переходом: копия ручных правок/),
  ).toBeInTheDocument();
});

test('manual overlaps require a choice while independent remote changes survive', async () => {
  const client = await openChat();
  fireEvent.click(screen.getByRole('button', { name: 'Документ' }));
  fireEvent.change(screen.getByLabelText('Раздел 1'), {
    target: { value: 'Моя правка' },
  });
  const latest = {
    ...specification,
    version: 2,
    revision: 2,
    sections: {
      ...specification.sections,
      '1': 'Правка коллеги',
      '9': 'Внешний журнал',
    },
  };
  await act(async () => {
    client.setQueryData(
      SpecificationKeys.document('actor', 'project', 'doc'),
      latest,
    );
  });
  await screen.findByRole('region', { name: 'Конфликт ручных правок' });
  expect((screen.getByLabelText('Раздел 1') as HTMLTextAreaElement).value).toBe(
    'Моя правка',
  );
  expect((screen.getByLabelText('Раздел 9') as HTMLTextAreaElement).value).toBe(
    'Внешний журнал',
  );
  expect(screen.getByRole('button', { name: 'Сохранить ТЗ' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Оставить мой текст' }));
  jest
    .mocked(saveSpecification)
    .mockImplementation(async (_workspace, value) => ({
      ...value,
      version: 3,
      revision: 3,
    }));
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить ТЗ' }));
  await waitFor(() =>
    expect(saveSpecification).toHaveBeenCalledWith(
      'project',
      expect.objectContaining({
        version: 2,
        sections: expect.objectContaining({
          '1': 'Моя правка',
          '9': 'Внешний журнал',
        }),
      }),
    ),
  );
});

test('semantic contradictions show details and offer Eva resolution', async () => {
  const consoleError = jest
    .spyOn(console, 'error')
    .mockImplementation(() => {});
  const client = await openChat();
  const proposal = {
    id: 'proposal',
    base_revision: 1,
    status: 'ready' as const,
    source: 'eva' as const,
    sections: { ...specification.sections, '4': '35 файлов' },
  };
  await act(async () => {
    client.setQueryData(SpecificationKeys.document('actor', 'project', 'doc'), {
      ...specification,
      version: 2,
      proposals: [proposal],
    });
  });
  jest.mocked(specificationAction).mockResolvedValue({
    ...specification,
    version: 3,
    proposals: [
      {
        ...proposal,
        status: 'needs_resolution',
        issues: ['Разделы 4 и 5: разные лимиты'],
      },
    ],
  });
  fireEvent.click(
    await screen.findByRole('button', { name: 'Применить изменения' }),
  );
  await screen.findByText('Разделы 4 и 5: разные лимиты');
  expect(
    screen.getByRole('button', { name: 'Сохранить мои значения' }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole('button', { name: 'Использовать значения Eva' }),
  ).toBeInTheDocument();
  expect(
    screen.queryByRole('button', { name: 'Применить изменения' }),
  ).not.toBeInTheDocument();
  expect(consoleError).not.toHaveBeenCalled();
});
