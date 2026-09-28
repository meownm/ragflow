import { chatWithSourceWorkspace } from '@/services/source-workbench-service';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { SourceChat } from './source-chat';

jest.mock('@/services/source-workbench-service', () => ({
  chatWithSourceWorkspace: jest.fn(),
  sourceRequestError: (error: Error) => error.message,
}));

const workspace: import('@/services/source-workbench-service').SourceWorkspace =
  {
    id: 'workspace-1',
    title: 'Sources',
    dataset_ids: ['kb-1'],
    selected_documents: [{ dataset_id: 'kb-1', document_id: 'first' }],
    search_queries: [],
    version: 1,
    created_at: '',
    updated_at: '',
  };

test('keeps old answers after selection changes and starts a new question context', async () => {
  const mockedChat = jest.mocked(chatWithSourceWorkspace);
  mockedChat.mockReset();
  mockedChat
    .mockResolvedValueOnce({ answer: 'Первый ответ', sources: [], version: 1 })
    .mockResolvedValueOnce({ answer: 'Новый ответ', sources: [], version: 2 });
  const view = render(<SourceChat workspace={workspace} />);

  fireEvent.change(screen.getByLabelText('Вопрос по выбранным статьям'), {
    target: { value: 'Первый вопрос' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Спросить' }));
  await screen.findByText('Первый ответ');

  view.rerender(<SourceChat workspace={{ ...workspace, version: 2 }} />);
  expect(screen.getByText('Первый ответ')).toBeInTheDocument();
  expect(screen.getByText(/Прежние ответы сохранены/)).toBeInTheDocument();

  fireEvent.change(screen.getByLabelText('Вопрос по выбранным статьям'), {
    target: { value: 'Новый вопрос' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Спросить' }));
  await screen.findByText('Новый ответ');
  await waitFor(() => expect(mockedChat).toHaveBeenCalledTimes(2));
  expect(mockedChat.mock.calls[1][2]).toBe('');
});

test('accepts a multiline question and explains the conversation scope', async () => {
  const mockedChat = jest.mocked(chatWithSourceWorkspace);
  mockedChat.mockReset();
  mockedChat.mockResolvedValue({ answer: 'Ответ', sources: [], version: 1 });
  render(<SourceChat workspace={workspace} />);

  expect(screen.getByText('Вопросы по выбранным статьям')).toBeInTheDocument();
  expect(screen.getByText(/история видна до выхода из/)).toBeInTheDocument();
  const field = screen.getByRole('textbox', {
    name: 'Вопрос по выбранным статьям',
  });
  expect(field.tagName).toBe('TEXTAREA');
  fireEvent.change(field, { target: { value: 'Вопрос\nУточнение' } });
  fireEvent.click(screen.getByRole('button', { name: 'Спросить' }));

  await waitFor(() =>
    expect(mockedChat).toHaveBeenCalledWith(workspace, 'Вопрос\nУточнение', ''),
  );
});
