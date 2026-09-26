import {
  getSourceWorkspaceDraft,
  listSourceWorkspaceDrafts,
  processSourceWorkspaceStream,
  saveSourceWorkspaceDraft,
  updateSourceWorkspaceDraft,
} from '@/services/source-workbench-service';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { SourceProcessor } from './source-processor';

jest.mock('@/services/source-workbench-service', () => ({
  getSourceWorkspaceDraft: jest.fn(),
  listSourceWorkspaceDrafts: jest.fn(),
  processSourceWorkspaceStream: jest.fn(),
  saveSourceWorkspaceDraft: jest.fn(),
  updateSourceWorkspaceDraft: jest.fn(),
  sourceRequestError: (error: Error) => error.message,
}));

const workspace: import('@/services/source-workbench-service').SourceWorkspace =
  {
    id: 'workspace-1',
    title: 'Sources',
    dataset_ids: ['kb-1'],
    selected_documents: [
      { dataset_id: 'kb-1', document_id: 'doc-1' },
      { dataset_id: 'kb-1', document_id: 'doc-2' },
    ],
    search_queries: [],
    version: 3,
    created_at: '',
    updated_at: '',
  };

const mockedStream = jest.mocked(processSourceWorkspaceStream);
const mockedGetDraft = jest.mocked(getSourceWorkspaceDraft);
const mockedListDrafts = jest.mocked(listSourceWorkspaceDrafts);
const mockedSaveDraft = jest.mocked(saveSourceWorkspaceDraft);
const mockedUpdateDraft = jest.mocked(updateSourceWorkspaceDraft);

beforeEach(() => {
  mockedStream.mockReset();
  mockedGetDraft.mockReset();
  mockedListDrafts.mockReset();
  mockedListDrafts.mockResolvedValue([]);
  mockedSaveDraft.mockReset();
  mockedUpdateDraft.mockReset();
});

test('shows streamed text before completion and commits the final result', async () => {
  let release: (() => void) | undefined;
  mockedStream.mockImplementation(async (_workspace, _input, onEvent) => {
    onEvent({
      event: 'status',
      stage: 'generate',
      message: 'Generating',
      current: 0,
      total: 1,
    });
    onEvent({ event: 'delta', text: 'Live text' });
    await new Promise<void>((resolve) => {
      release = resolve;
    });
    onEvent({
      event: 'done',
      text: 'Final text',
      processed: 2,
      total: 2,
      version: 3,
    });
  });
  render(<SourceProcessor workspace={workspace} />);
  fireEvent.change(screen.getByLabelText('Промпт'), {
    target: { value: 'Create' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Обработать' }));
  await waitFor(() =>
    expect(screen.getByLabelText('Потоковый ответ')).toHaveValue('Live text'),
  );
  expect(
    screen.queryByLabelText('Результат обработки'),
  ).not.toBeInTheDocument();
  release?.();
  await waitFor(() =>
    expect(screen.getByLabelText('Результат обработки')).toHaveValue(
      'Final text',
    ),
  );
  expect(mockedStream.mock.calls[0][1]).toEqual({
    prompt: 'Create',
    draft: '',
    mode: 'all',
  });
});

test('keeps the last completed article if a later stage fails', async () => {
  mockedStream.mockImplementation(async (_workspace, _input, onEvent) => {
    onEvent({
      event: 'step_done',
      text: 'After first',
      article: 1,
      processed: 1,
      total: 2,
      strategy: 'full_text',
      version: 3,
    });
    throw new Error('Model failed');
  });
  render(<SourceProcessor workspace={workspace} />);
  fireEvent.click(screen.getByLabelText('По одной, последовательно'));
  fireEvent.change(screen.getByLabelText('Промпт'), {
    target: { value: 'Revise' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Обработать' }));
  await waitFor(() =>
    expect(screen.getByRole('alert')).toHaveTextContent('Model failed'),
  );
  expect(screen.getByLabelText('Результат обработки')).toHaveValue(
    'After first',
  );
  expect(screen.getByText('Завершено статей: 1 из 2')).toBeInTheDocument();
  expect(mockedStream.mock.calls[0][1].mode).toBe('sequential');
});

test('stop aborts the active stream', async () => {
  const onBusyChange = jest.fn();
  mockedStream.mockImplementation((_workspace, _input, onEvent, signal) => {
    onEvent({ event: 'delta', text: 'Незаконченный текст' });
    return new Promise<void>((_resolve, reject) => {
      signal.addEventListener('abort', () =>
        reject(new DOMException('Stopped', 'AbortError')),
      );
    });
  });
  render(<SourceProcessor workspace={workspace} onBusyChange={onBusyChange} />);
  fireEvent.change(screen.getByLabelText('Промпт'), {
    target: { value: 'Create' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Обработать' }));
  expect(onBusyChange).toHaveBeenCalledWith(true);
  fireEvent.click(screen.getByRole('button', { name: 'Остановить' }));
  await waitFor(() =>
    expect(screen.getByText('Остановлено пользователем')).toBeInTheDocument(),
  );
  expect(
    screen.getByText('Для этого запуска завершённого результата нет.'),
  ).toBeInTheDocument();
  expect(screen.getByText('Незавершённый ответ модели')).toBeInTheDocument();
  expect(screen.getByLabelText('Потоковый ответ')).toHaveValue(
    'Незаконченный текст',
  );
  expect(
    screen.queryByRole('button', { name: 'Сохранить черновик' }),
  ).not.toBeInTheDocument();
  await waitFor(() => expect(onBusyChange).toHaveBeenCalledWith(false));
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});

test('cannot start while the selection is being saved', () => {
  render(<SourceProcessor workspace={workspace} selectionBusy />);
  fireEvent.change(screen.getByLabelText('Промпт'), {
    target: { value: 'Create' },
  });
  expect(screen.getByRole('button', { name: 'Обработать' })).toBeDisabled();
  expect(mockedStream).not.toHaveBeenCalled();
});

test('saves a reviewed complete result with the current source selection', async () => {
  mockedStream.mockImplementation(async (_workspace, _input, onEvent) => {
    onEvent({
      event: 'done',
      text: 'Generated',
      processed: 2,
      total: 2,
      version: 3,
    });
  });
  mockedSaveDraft.mockResolvedValue({
    id: 'draft-1',
    workspace_id: workspace.id,
    content: 'Reviewed result',
    prompt: 'Create',
    mode: 'all',
    source_version: 3,
    sources: workspace.selected_documents,
    version: 1,
    created_at: '',
    updated_at: new Date().toISOString(),
  });
  render(<SourceProcessor workspace={workspace} />);
  fireEvent.change(screen.getByLabelText('Промпт'), {
    target: { value: 'Create' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Обработать' }));
  await screen.findByDisplayValue('Generated');
  fireEvent.change(screen.getByLabelText('Промпт'), {
    target: { value: 'Another instruction' },
  });
  fireEvent.click(screen.getByLabelText('По одной, последовательно'));
  fireEvent.change(screen.getByLabelText('Результат обработки'), {
    target: { value: 'Reviewed result' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить черновик' }));
  await waitFor(() =>
    expect(mockedSaveDraft).toHaveBeenCalledWith(workspace, {
      content: 'Reviewed result',
      prompt: 'Create',
      mode: 'all',
    }),
  );
  expect(await screen.findByText(/Черновик сохранён/)).toBeInTheDocument();
});

test('opens and edits a saved draft before another processing run', async () => {
  const saved = {
    id: 'draft-1',
    workspace_id: workspace.id,
    content: 'Saved text',
    prompt: 'Improve',
    mode: 'sequential' as const,
    source_version: 3,
    sources: workspace.selected_documents,
    version: 1,
    created_at: '',
    updated_at: new Date().toISOString(),
  };
  mockedListDrafts.mockResolvedValue([saved]);
  mockedGetDraft.mockResolvedValue(saved);
  mockedUpdateDraft.mockResolvedValue({
    ...saved,
    content: 'Edited text',
    version: 2,
  });
  render(<SourceProcessor workspace={workspace} />);
  fireEvent.click(
    await screen.findByRole('button', { name: /Открыть черновик/ }),
  );
  await waitFor(() =>
    expect(mockedGetDraft).toHaveBeenCalledWith(workspace.id, saved.id),
  );
  expect(await screen.findByDisplayValue('Saved text')).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('Результат обработки'), {
    target: { value: 'Edited text' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить правки' }));
  await waitFor(() =>
    expect(mockedUpdateDraft).toHaveBeenCalledWith(saved, 'Edited text'),
  );
  fireEvent.click(
    screen.getByRole('button', { name: 'Передать текст в обработку' }),
  );
  expect(
    screen.getByLabelText('Исходный текст для правок (необязательно)'),
  ).toHaveValue('Edited text');
});
