import { render, screen, within } from '@testing-library/react';
import type { BusinessDocumentChangePreview } from '../types';
import { DocumentSectionChange } from './change-preview-panel';

const change: BusinessDocumentChangePreview['sections'][number] = {
  section_id: '4.3',
  title: 'Сценарии',
  before: 'Неизменённый сосед. Срок 5 дней.',
  after: 'Неизменённый сосед. Срок 10 дней. Новый контроль.',
  source_event_ids: ['event-1'],
  blocks: [
    {
      block_id: 'deadline',
      kind: 'replace',
      before: 'Срок 5 дней.',
      after: 'Срок 10 дней.',
    },
    {
      block_id: 'control',
      kind: 'insert',
      before: '',
      after: 'Новый контроль.',
    },
  ],
};

it('shows only changed blocks with exact before/after text and stable identity', () => {
  const { container } = render(<DocumentSectionChange change={change} />);
  expect(screen.getByText('Было → стало')).toBeInTheDocument();
  expect(screen.queryByText(/Неизменённый сосед/)).not.toBeInTheDocument();
  const block = container.querySelector('[data-block-id="deadline"]')!;
  const views = within(block as HTMLElement)
    .getAllByRole('generic')
    .filter((node) => node.tagName === 'PRE');
  expect(views.map((view) => view.textContent)).toEqual([
    'Срок 5 дней.',
    'Срок 10 дней.',
  ]);
  expect(block.querySelector('del')?.textContent).toBe('5');
  expect(block.querySelector('ins')?.textContent).toBe('10');
  expect(
    container.querySelector('[data-block-id="control"]'),
  ).toHaveTextContent('Новый контроль.');
});

it('shows deletions and supports saved section previews', () => {
  const { rerender } = render(
    <DocumentSectionChange
      change={{
        ...change,
        blocks: [
          {
            block_id: 'removed',
            kind: 'delete',
            before: 'Удаляемое правило.',
            after: '',
          },
        ],
      }}
    />,
  );
  expect(screen.getByText('Удаляемое правило.')).toBeInTheDocument();
  expect(screen.getByText('Требования отсутствуют')).toBeInTheDocument();
  rerender(<DocumentSectionChange change={{ ...change, blocks: undefined }} />);
  expect(screen.getAllByTestId('revision-text-diff')).toHaveLength(1);
});
