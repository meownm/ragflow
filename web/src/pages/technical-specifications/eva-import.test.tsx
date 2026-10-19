import { searchEvaPages } from '@/services/technical-specification-service';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { EvaImport } from './eva-import';

type EvaPage = import('@/interfaces/database/eva-page').EvaPage;

jest.mock('@/services/technical-specification-service', () => ({
  searchEvaPages: jest.fn(),
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

beforeEach(() => jest.clearAllMocks());

function search(query: string) {
  fireEvent.change(
    screen.getByLabelText('Название или ссылка на страницу Eva'),
    { target: { value: query } },
  );
  fireEvent.click(screen.getByRole('button', { name: 'Найти в Eva' }));
}

test('import requires an explicit selection and retains its identity and version', async () => {
  jest.mocked(searchEvaPages).mockResolvedValue([page]);
  const onImport = jest.fn().mockResolvedValue(undefined);
  render(<EvaImport workspace="workspace" busy={false} onImport={onImport} />);
  search('Загрузка');
  await screen.findByText('Загрузка');
  expect(
    screen.queryByRole('button', { name: 'Импортировать выбранную страницу' }),
  ).toBeNull();
  fireEvent.click(screen.getByRole('radio'));
  expect(screen.getByText('page · Версия v7')).toBeInTheDocument();
  fireEvent.click(
    screen.getByRole('button', { name: 'Импортировать выбранную страницу' }),
  );
  expect(onImport).toHaveBeenCalledWith(page, false);
  fireEvent.click(screen.getByRole('checkbox'));
  fireEvent.click(screen.getByRole('button', { name: 'Создать копию' }));
  expect(onImport).toHaveBeenLastCalledWith(page, true);
});

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
  render(<EvaImport workspace="workspace" busy={false} onImport={jest.fn()} />);
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
  render(<EvaImport workspace="workspace" busy={false} onImport={jest.fn()} />);
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
  render(<EvaImport workspace="workspace" busy={false} onImport={jest.fn()} />);
  search('Загрузка');
  await screen.findByRole('alert');
  fireEvent.click(screen.getByRole('button', { name: 'Найти в Eva' }));
  await screen.findByText(
    'Страницы не найдены. Уточните название или вставьте ссылку.',
  );
  expect(screen.queryByRole('alert')).toBeNull();
});
