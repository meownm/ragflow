import { fireEvent, render, screen, within } from '@testing-library/react';
import { ChatHistory } from './chat-history';
type Proposal = import('./model').Proposal;
type Specification = import('./model').Specification;

jest.mock('@/pages/business-documents/components/document-markdown', () => ({
  DocumentMarkdown: ({ children }: { children: string }) => <p>{children}</p>,
}));

const document: Specification = {
  id: 'doc',
  workspace_id: 'project',
  title: 'ТЗ',
  version: 1,
  revision: 2,
  sections: {},
  comments: [],
  proposals: [],
  people: {},
  eva: null,
  messages: [
    { id: 'first', role: 'user', author_id: 'actor', text: 'Первый запрос' },
    {
      id: 'answer-first',
      role: 'assistant',
      reply_to: 'first',
      text: 'Первый ответ',
    },
    {
      id: 'second',
      role: 'user',
      author_id: 'actor',
      text: 'Исправить предложение',
    },
    {
      id: 'answer-second',
      role: 'assistant',
      reply_to: 'second',
      text: 'Исправленный ответ',
    },
  ],
};
const proposal = (id: string, message_id?: string): Proposal => ({
  id,
  message_id,
  base_revision: 2,
  sections: {},
  source: 'chat',
  status: 'ready',
});

test('proposals follow their saved request links rather than the newest answer', () => {
  render(
    <ChatHistory
      document={document}
      actor="actor"
      proposals={[
        proposal('first-proposal', 'first'),
        proposal('second-proposal', 'second'),
        proposal('unlinked'),
      ]}
      disabled={false}
      onRetry={jest.fn()}
      renderProposal={(value) => <section key={value.id}>{value.id}</section>}
    />,
  );
  const first = screen.getByText('Первый ответ').closest('article')!;
  const second = screen.getByText('Исправленный ответ').closest('article')!;
  expect(within(first).getByText('first-proposal')).toBeInTheDocument();
  expect(within(second).getByText('second-proposal')).toBeInTheDocument();
  expect(within(second).queryByText('first-proposal')).not.toBeInTheDocument();
  expect(screen.getByText('unlinked').closest('article')).toBeNull();
});

test('retry keeps the saved message and respects the editing boundary', () => {
  const onRetry = jest.fn();
  const draft = { document_id: 'doc', revision: 2, scope: 'all', anchor: null };
  const failed = {
    ...document,
    messages: [
      {
        id: 'failed',
        role: 'user',
        author_id: 'actor',
        text: 'Сохранённый вопрос',
        status: 'failed' as const,
        context: draft,
      },
    ],
  };
  const view = render(
    <ChatHistory
      document={failed}
      actor="actor"
      proposals={[]}
      disabled
      onRetry={onRetry}
      renderProposal={() => null}
    />,
  );
  expect(
    screen.getByRole('button', { name: 'Повторить сообщение' }),
  ).toBeDisabled();
  view.rerender(
    <ChatHistory
      document={failed}
      actor="actor"
      proposals={[]}
      disabled={false}
      onRetry={onRetry}
      renderProposal={() => null}
    />,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Повторить сообщение' }));
  expect(onRetry).toHaveBeenCalledWith({
    message_id: 'failed',
    text: 'Сохранённый вопрос',
    context: draft,
  });
});
