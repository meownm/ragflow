import { listBusinessDocumentRevisions } from '@/services/business-document-service';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { ReviewHistory } from './review-history';
type BusinessDocumentRevision = import('../types').BusinessDocumentRevision;

jest.mock('@/services/business-document-service', () => ({
  listBusinessDocumentRevisions: jest.fn(),
}));
const fetchRevisions = jest.mocked(listBusinessDocumentRevisions);

test('keeps saved option/custom answers and unresolved past proposals read-only', () => {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <ReviewHistory
        documentId="doc"
        cycles={[
          {
            review_cycle: 1,
            questions: [
              {
                question_id: 'q1',
                text: 'Как долго?',
                status: 'ANSWERED',
                options: [{ option_id: '15', label: '15 минут' }],
                allow_custom_answer: true,
                answer: { selected_option_id: '15' },
              },
              {
                question_id: 'q2',
                text: 'Закрытый вопрос',
                status: 'OPEN',
                options: [],
                allow_custom_answer: true,
              },
              {
                question_id: 'q3',
                text: 'Какой канал?',
                status: 'ANSWERED',
                options: [],
                allow_custom_answer: true,
                answer: { custom_answer: 'Электронная почта' },
              },
            ],
            proposals: [
              {
                proposal_id: 'p1',
                text: 'Старое предложение',
                decision: 'PENDING',
              },
            ],
            comments: [],
          },
        ]}
      />
    </QueryClientProvider>,
  );
  expect(screen.getByText('Ответ: 15 минут')).toBeVisible();
  expect(screen.getByText('Ответ: Электронная почта')).toBeVisible();
  expect(screen.getByText('Не принято')).toBeVisible();
  expect(screen.getByText('Закрыт')).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Ответить' })).toBeNull();
  expect(screen.queryByRole('button', { name: 'Принять' })).toBeNull();
  expect(fetchRevisions).not.toHaveBeenCalled();
});

test('compares the actual revision tied to a decision, rather than the latest document', async () => {
  const revision = (
    number: number,
    text: string,
  ): BusinessDocumentRevision => ({
    revision_id: `r${number}`,
    revision_number: number,
    document_ast: {
      schema_version: '1',
      document_type: 'business_requirements',
      template_version: '1',
      sections: [],
    },
    section_texts: { '4.3': text },
    body_markdown: text,
    content_hash: `h${number}`,
  });
  fetchRevisions.mockResolvedValue([
    revision(1, 'До изменения'),
    { ...revision(2, 'После изменения'), source_event_ids: ['decision-1'] },
    revision(3, 'Более поздняя правка'),
  ]);
  render(
    <QueryClientProvider client={new QueryClient()}>
      <ReviewHistory
        documentId="doc"
        cycles={[
          {
            questions: [],
            proposals: [
              {
                proposal_id: 'p1',
                decision_event_id: 'decision-1',
                text: 'Принятое предложение',
                target_section_id: '4.3',
                decision: 'ACCEPTED',
                applied: true,
              },
            ],
            comments: [],
          },
        ]}
      />
    </QueryClientProvider>,
  );
  const item = screen.getByTestId('business-document-proposal');
  expect(await screen.findByText(/Внесено в редакцию 2/)).toBeVisible();
  fireEvent.click(within(item).getByText('Принятое предложение'));
  expect(within(item).getByTestId('revision-text-diff')).toHaveTextContent(
    'До изменения',
  );
  expect(within(item).getByTestId('revision-text-diff')).toHaveTextContent(
    'После изменения',
  );
  expect(within(item).getByTestId('revision-text-diff')).not.toHaveTextContent(
    'Более поздняя правка',
  );
});
