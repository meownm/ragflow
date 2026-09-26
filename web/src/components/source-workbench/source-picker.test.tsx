import {
  getSourceWorkspace,
  saveSourceSelection,
  searchSourceWorkspace,
} from '@/services/source-workbench-service';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { SourcePicker } from './source-picker';

jest.mock('@/services/source-workbench-service', () => ({
  getSourceWorkspace: jest.fn(),
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

beforeEach(() => {
  jest.clearAllMocks();
  mockedGet.mockResolvedValue(workspace);
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
