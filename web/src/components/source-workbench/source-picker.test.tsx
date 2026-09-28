import {
  getSourceWorkspace,
  previewSourceWorkspaceArticle,
  saveSourceSelection,
  searchSourceWorkspace,
} from '@/services/source-workbench-service';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { SourcePicker } from './source-picker';

jest.mock('react-markdown', () => ({
  __esModule: true,
  default: ({ children }: { children?: string }) => <>{children}</>,
}));
jest.mock('remark-gfm', () => ({ __esModule: true, default: () => undefined }));
jest.mock('rehype-raw', () => ({ __esModule: true, default: () => undefined }));

jest.mock('@/services/source-workbench-service', () => ({
  getSourceWorkspace: jest.fn(),
  previewSourceWorkspaceArticle: jest.fn(),
  saveSourceSelection: jest.fn(),
  searchSourceWorkspace: jest.fn(),
  sourceRequestError: (error: Error) => error.message,
}));

const article = (
  id: string,
  title: string,
): import('@/services/source-workbench-service').SourceCandidate => ({
  dataset_id: 'kb-1',
  document_id: id,
  title,
  path: [title],
  source_type: 'dataset',
  source_url: null,
  content_hash: '',
  excerpts: [],
});

const workspace: import('@/services/source-workbench-service').SourceWorkspace =
  {
    id: 'workspace-1',
    title: 'Sources',
    dataset_ids: ['kb-1'],
    selected_documents: [],
    search_queries: [],
    version: 1,
    created_at: '',
    updated_at: '',
  };

const mockedSearch = jest.mocked(searchSourceWorkspace);
const mockedGet = jest.mocked(getSourceWorkspace);
const mockedSave = jest.mocked(saveSourceSelection);
const mockedPreview = jest.mocked(previewSourceWorkspaceArticle);

beforeEach(() => {
  jest.clearAllMocks();
  mockedGet.mockResolvedValue(workspace);
  mockedPreview.mockResolvedValue({
    ...article('first', 'Первая статья'),
    text: '# Первая статья\n\nПолный текст',
    revision: 'hash:1:2',
  });
});

test('opens full article from search results and selected sources', async () => {
  mockedSearch.mockResolvedValue({
    query: 'Тема',
    page: 1,
    has_more: false,
    candidates: [article('first', 'Первая статья')],
  });
  render(
    <SourcePicker
      workspace={{
        ...workspace,
        selected_documents: [{ dataset_id: 'kb-1', document_id: 'first' }],
        selected_sources: [article('first', 'Первая статья')],
      }}
      onChange={jest.fn()}
    />,
  );
  fireEvent.click(
    screen.getByRole('button', {
      name: 'Читать выбранную статью Первая статья',
    }),
  );
  expect(
    await screen.findByRole('article', {
      name: 'Содержимое статьи Первая статья',
    }),
  ).toHaveTextContent('Полный текст');
  fireEvent.click(screen.getByRole('button', { name: 'Close' }));

  fireEvent.change(screen.getByLabelText('Поиск статей'), {
    target: { value: 'Тема' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Найти' }));
  await screen.findByRole('button', { name: 'Читать статью Первая статья' });
  fireEvent.click(
    screen.getByRole('button', { name: 'Читать статью Первая статья' }),
  );
  await waitFor(() => expect(mockedPreview).toHaveBeenCalledTimes(2));
  expect(mockedPreview.mock.calls[1][1]).toMatchObject({
    dataset_id: 'kb-1',
    document_id: 'first',
  });
});

test('shows saved selection without an empty search results panel on reopen', () => {
  render(
    <SourcePicker
      workspace={{
        ...workspace,
        selected_documents: [{ dataset_id: 'kb-1', document_id: 'first' }],
        selected_sources: [article('first', 'Первая статья')],
      }}
      onChange={jest.fn()}
    />,
  );
  expect(screen.getByText('1. Первая статья')).toBeInTheDocument();
  expect(screen.queryByText('Найденные статьи')).not.toBeInTheDocument();
});

test('shows only the current query and its empty result', async () => {
  mockedSearch
    .mockResolvedValueOnce({
      query: 'Первая тема',
      page: 1,
      has_more: false,
      candidates: [article('first', 'Первая статья')],
    })
    .mockResolvedValueOnce({
      query: 'Другая тема',
      page: 1,
      has_more: false,
      candidates: [],
    });
  render(<SourcePicker workspace={workspace} onChange={jest.fn()} />);

  fireEvent.change(screen.getByLabelText('Поиск статей'), {
    target: { value: 'Первая тема' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Найти' }));
  await screen.findByText('Первая статья');

  fireEvent.change(screen.getByLabelText('Поиск статей'), {
    target: { value: 'Другая тема' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Найти' }));
  await screen.findByText(
    'По этому запросу статьи не найдены. Уточните формулировку.',
  );
  expect(screen.queryByText('Первая статья')).not.toBeInTheDocument();
  expect(
    screen.getByText(/Результаты запроса «Другая тема»: 0/),
  ).toBeInTheDocument();
});

test('accepts a multiline search request', async () => {
  mockedSearch.mockResolvedValue({
    query: 'Тема\nУточняющее условие',
    page: 1,
    has_more: false,
    candidates: [],
  });
  render(<SourcePicker workspace={workspace} onChange={jest.fn()} />);

  const field = screen.getByRole('textbox', { name: 'Поиск статей' });
  expect(field.tagName).toBe('TEXTAREA');
  fireEvent.change(field, { target: { value: 'Тема\nУточняющее условие' } });
  fireEvent.click(screen.getByRole('button', { name: 'Найти' }));

  await waitFor(() =>
    expect(mockedSearch).toHaveBeenCalledWith(
      workspace.id,
      'Тема\nУточняющее условие',
      1,
    ),
  );
});

test('reorders selected articles and locks changes during processing', async () => {
  const selected: import('@/services/source-workbench-service').SourceWorkspace =
    {
      ...workspace,
      selected_documents: [
        { dataset_id: 'kb-1', document_id: 'first' },
        { dataset_id: 'kb-1', document_id: 'second' },
      ],
      selected_sources: [
        article('first', 'Первая'),
        article('second', 'Вторая'),
      ],
    };
  mockedSave.mockResolvedValue({ ...selected, version: 2 });
  const onChange = jest.fn();
  const view = render(
    <SourcePicker workspace={selected} onChange={onChange} />,
  );

  fireEvent.click(
    screen.getByRole('button', { name: 'Поднять статью Вторая' }),
  );
  await waitFor(() => expect(mockedSave).toHaveBeenCalledTimes(1));
  expect(mockedSave.mock.calls[0][1].map((item) => item.document_id)).toEqual([
    'second',
    'first',
  ]);

  view.rerender(
    <SourcePicker workspace={selected} onChange={onChange} selectionLocked />,
  );
  expect(
    screen.getByRole('button', { name: 'Поднять статью Вторая' }),
  ).toBeDisabled();
  expect(
    screen.getByRole('button', { name: 'Обновить выбранные статьи' }),
  ).toBeDisabled();
});
