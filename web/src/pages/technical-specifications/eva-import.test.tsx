import {
  cancelEvaImport,
  getEvaImport,
  importEvaSpecification,
  searchEvaPages,
} from '@/services/technical-specification-service';
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { EvaImport } from './eva-import';
import { importPrefix, readImport, rememberImport } from './import-attempt';

type EvaPage = import('@/interfaces/database/eva-page').EvaPage;

jest.mock('@/services/technical-specification-service', () => ({
  searchEvaPages: jest.fn(),
  cancelEvaImport: jest.fn(),
  getEvaImport: jest.fn(),
  importEvaSpecification: jest.fn(),
}));
jest.mock('@/services/source-workbench-service', () => ({
  sourceRequestError: (_error: unknown, message: string) => message,
}));

const page: EvaPage = {
  id: 'page',
  name: 'Загрузка',
  project_id: 'project',
  code: 'TZ-1',
  version: 'v7',
  web_url: 'https://eva.test/project/Document/TZ-1',
  binding_available: true,
  linked_document: null,
};

beforeEach(() => {
  jest.clearAllMocks();
  localStorage.clear();
});

function search(query: string) {
  fireEvent.change(
    screen.getByLabelText('Название или ссылка на страницу Eva'),
    { target: { value: query } },
  );
  fireEvent.click(screen.getByRole('button', { name: 'Найти в Eva' }));
}

test.each([false, true])(
  'import requires an explicit selection and retains its identity and version (copy=%s)',
  async (copy) => {
    jest.mocked(searchEvaPages).mockResolvedValue([page]);
    jest.mocked(importEvaSpecification).mockReturnValue(new Promise(() => {}));
    render(
      <EvaImport
        actor="actor"
        workspace="workspace"
        busy={false}
        onImported={jest.fn()}
      />,
    );
    search('Загрузка');
    await screen.findByText('Загрузка');
    expect(
      screen.queryByRole('button', {
        name: 'Импортировать выбранную страницу',
      }),
    ).toBeNull();
    fireEvent.click(screen.getByRole('radio'));
    expect(screen.getByText('page · Версия v7')).toBeInTheDocument();
    if (copy) fireEvent.click(screen.getByRole('checkbox'));
    fireEvent.click(
      screen.getByRole('button', {
        name: copy ? 'Создать копию' : 'Импортировать выбранную страницу',
      }),
    );
    const pending = readImport(importPrefix('actor', 'workspace'))!;
    expect(importEvaSpecification).toHaveBeenCalledWith(
      'workspace',
      page.web_url,
      copy,
      page,
      pending.id,
    );
    expect(
      screen.getByRole('button', { name: 'Продолжить импорт' }),
    ).toBeDisabled();
  },
);

test('changing a query clears the selection and ignores a delayed previous response', async () => {
  let finish!: (pages: EvaPage[]) => void;
  jest
    .mocked(searchEvaPages)
    .mockReturnValueOnce(
      new Promise((resolve) => {
        finish = resolve;
      }),
    )
    .mockResolvedValueOnce([
      { ...page, id: 'second', name: 'Другая страница' },
    ]);
  render(
    <EvaImport
      actor="actor"
      workspace="workspace"
      busy={false}
      onImported={jest.fn()}
    />,
  );
  search('Первый');
  search('Второй');
  await screen.findByText('Другая страница');
  await act(async () => {
    finish([page]);
  });
  expect(screen.queryByText('Загрузка')).toBeNull();
  fireEvent.click(screen.getByRole('radio'));
  fireEvent.change(
    screen.getByLabelText('Название или ссылка на страницу Eva'),
    { target: { value: '' } },
  );
  expect(screen.queryByRole('radio')).toBeNull();
  expect(screen.getByRole('button', { name: 'Найти в Eva' })).toBeDisabled();
  expect(
    screen.queryByRole('button', { name: 'Импортировать выбранную страницу' }),
  ).toBeNull();
});

test('an unavailable binding permits only an explicit independent copy', async () => {
  jest
    .mocked(searchEvaPages)
    .mockResolvedValue([{ ...page, binding_available: false }]);
  render(
    <EvaImport
      actor="actor"
      workspace="workspace"
      busy={false}
      onImported={jest.fn()}
    />,
  );
  search('Загрузка');
  fireEvent.click(await screen.findByRole('radio'));
  expect(
    screen.getByRole('button', { name: 'Импортировать выбранную страницу' }),
  ).toBeDisabled();
  fireEvent.click(screen.getByRole('checkbox'));
  expect(screen.getByRole('button', { name: 'Создать копию' })).toBeEnabled();
});

test('a failed search can be retried and an empty result is explained', async () => {
  jest
    .mocked(searchEvaPages)
    .mockRejectedValueOnce(new Error('offline'))
    .mockResolvedValueOnce([]);
  render(
    <EvaImport
      actor="actor"
      workspace="workspace"
      busy={false}
      onImported={jest.fn()}
    />,
  );
  search('Загрузка');
  await screen.findByRole('alert');
  fireEvent.click(screen.getByRole('button', { name: 'Найти в Eva' }));
  await screen.findByText(
    'Страницы не найдены. Уточните название или вставьте ссылку.',
  );
  expect(screen.queryByRole('alert')).toBeNull();
});

test('lost response restores the exact request and opens a committed result without importing again', async () => {
  const prefix = importPrefix('actor', 'workspace');
  const attempt = { id: 'a'.repeat(32), page, copy: true };
  rememberImport(prefix, attempt);
  const document = {
    id: 'doc',
    workspace_id: 'workspace',
  } as import('./model').Specification;
  jest
    .mocked(getEvaImport)
    .mockResolvedValue({ id: attempt.id, state: 'completed', document });
  const onImported = jest.fn();
  render(
    <EvaImport
      actor="actor"
      workspace="workspace"
      busy={false}
      onImported={onImported}
    />,
  );
  expect(screen.getByText('Загрузка — независимая копия')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Продолжить импорт' }));
  await waitFor(() => expect(onImported).toHaveBeenCalledWith(document));
  expect(importEvaSpecification).not.toHaveBeenCalled();
  expect(readImport(prefix)).toBeNull();
});

test('cancel keeps a local receipt if cancellation failed', async () => {
  const attempt = { id: 'b'.repeat(32), page, copy: true };
  const prefix = importPrefix('actor', 'workspace');
  rememberImport(prefix, attempt);
  jest
    .mocked(cancelEvaImport)
    .mockRejectedValueOnce(new Error('offline'))
    .mockResolvedValueOnce({ id: attempt.id, state: 'canceled' });
  render(
    <EvaImport
      actor="actor"
      workspace="workspace"
      busy={false}
      onImported={jest.fn()}
    />,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Отменить импорт' }));
  await screen.findByRole('alert');
  expect(readImport(prefix)).toEqual(attempt);
  fireEvent.click(screen.getByRole('button', { name: 'Отменить импорт' }));
  await screen.findByRole('button', { name: 'Найти в Eva' });
  expect(cancelEvaImport).toHaveBeenLastCalledWith(
    'workspace',
    page,
    true,
    attempt.id,
  );
  expect(readImport(prefix)).toBeNull();
});

test('resuming a failed request retains the original version, copy mode and operation identity', async () => {
  const attempt = { id: 'c'.repeat(32), page, copy: true };
  rememberImport(importPrefix('actor', 'workspace'), attempt);
  const document = {
    id: 'copy',
    workspace_id: 'workspace',
  } as import('./model').Specification;
  jest
    .mocked(getEvaImport)
    .mockResolvedValue({ id: attempt.id, state: 'failed' });
  jest.mocked(importEvaSpecification).mockResolvedValue(document);
  const onImported = jest.fn();
  render(
    <EvaImport
      actor="actor"
      workspace="workspace"
      busy={false}
      onImported={onImported}
    />,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Продолжить импорт' }));
  await waitFor(() => expect(onImported).toHaveBeenCalledWith(document));
  expect(importEvaSpecification).toHaveBeenCalledWith(
    'workspace',
    page.web_url,
    true,
    page,
    attempt.id,
  );
});

test('cancel remains available during import and its result supersedes a late original response', async () => {
  jest.mocked(searchEvaPages).mockResolvedValue([page]);
  let finish!: (value: import('./model').Specification) => void;
  jest.mocked(importEvaSpecification).mockReturnValue(
    new Promise((resolve) => {
      finish = resolve;
    }),
  );
  const onImported = jest.fn();
  render(
    <EvaImport
      actor="actor"
      workspace="workspace"
      busy={false}
      onImported={onImported}
    />,
  );
  search('Загрузка');
  fireEvent.click(await screen.findByRole('radio'));
  fireEvent.click(
    screen.getByRole('button', { name: 'Импортировать выбранную страницу' }),
  );
  const attempt = readImport(importPrefix('actor', 'workspace'))!;
  jest
    .mocked(cancelEvaImport)
    .mockResolvedValue({ id: attempt.id, state: 'canceled' });
  expect(screen.getByRole('button', { name: 'Отменить импорт' })).toBeEnabled();
  fireEvent.click(screen.getByRole('button', { name: 'Отменить импорт' }));
  await screen.findByRole('button', { name: 'Найти в Eva' });
  await act(async () =>
    finish({
      id: 'old',
      workspace_id: 'workspace',
    } as import('./model').Specification),
  );
  expect(onImported).not.toHaveBeenCalled();
  expect(readImport(importPrefix('actor', 'workspace'))).toBeNull();
});

test('a terminal inaccessible result releases the import form without exposing or recreating the document', async () => {
  const attempt = { id: 'e'.repeat(32), page, copy: false };
  const prefix = importPrefix('actor', 'workspace');
  rememberImport(prefix, attempt);
  jest.mocked(getEvaImport).mockResolvedValue({
    id: attempt.id,
    state: 'completed',
    document_unavailable: true,
  });
  const onImported = jest.fn();
  render(
    <EvaImport
      actor="actor"
      workspace="workspace"
      busy={false}
      onImported={onImported}
    />,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Продолжить импорт' }));
  await screen.findByRole('button', { name: 'Найти в Eva' });
  expect(screen.getByRole('alert')).toHaveTextContent(
    'доступ к результату сейчас отсутствует',
  );
  expect(readImport(prefix)).toBeNull();
  expect(onImported).not.toHaveBeenCalled();
  expect(importEvaSpecification).not.toHaveBeenCalled();
});

test('cancel opens an already completed import and never discards a malformed receipt', async () => {
  const attempt = { id: 'd'.repeat(32), page, copy: true };
  const prefix = importPrefix('actor', 'workspace');
  rememberImport(prefix, attempt);
  const document = {
    id: 'copy',
    workspace_id: 'workspace',
  } as import('./model').Specification;
  jest
    .mocked(cancelEvaImport)
    .mockResolvedValueOnce({ id: attempt.id, state: 'completed' })
    .mockResolvedValueOnce({ id: attempt.id, state: 'completed', document });
  const onImported = jest.fn();
  render(
    <EvaImport
      actor="actor"
      workspace="workspace"
      busy={false}
      onImported={onImported}
    />,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Отменить импорт' }));
  await screen.findByRole('alert');
  expect(readImport(prefix)).toEqual(attempt);
  expect(onImported).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Отменить импорт' }));
  await waitFor(() => expect(onImported).toHaveBeenCalledWith(document));
  expect(readImport(prefix)).toBeNull();
});

test('unavailable local storage prevents dispatch and a late response never navigates after unmount', async () => {
  jest.mocked(searchEvaPages).mockResolvedValue([page]);
  const onImported = jest.fn();
  const view = render(
    <EvaImport
      actor="actor"
      workspace="workspace"
      busy={false}
      onImported={onImported}
    />,
  );
  search('Загрузка');
  fireEvent.click(await screen.findByRole('radio'));
  const write = jest
    .spyOn(Storage.prototype, 'setItem')
    .mockImplementation(() => {
      throw new Error('quota');
    });
  fireEvent.click(
    screen.getByRole('button', { name: 'Импортировать выбранную страницу' }),
  );
  expect(importEvaSpecification).not.toHaveBeenCalled();
  expect(screen.getByRole('alert')).toHaveTextContent('Импорт не отправлен');
  write.mockRestore();
  let finish!: (value: import('./model').Specification) => void;
  jest.mocked(importEvaSpecification).mockReturnValue(
    new Promise((resolve) => {
      finish = resolve;
    }),
  );
  fireEvent.click(
    screen.getByRole('button', { name: 'Импортировать выбранную страницу' }),
  );
  view.unmount();
  await act(async () =>
    finish({
      id: 'doc',
      workspace_id: 'workspace',
    } as import('./model').Specification),
  );
  expect(onImported).not.toHaveBeenCalled();
  expect(readImport(importPrefix('actor', 'workspace'))).toBeNull();
});
