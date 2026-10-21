import { render, screen } from '@testing-library/react';
import { ProposalCard } from './proposal-card';
type Proposal = import('./model').Proposal;

test('saved proposals without request links have distinct visible numbers', () => {
  const candidate: Proposal = {
    id: 'older',
    base_revision: 2,
    sections: {},
    status: 'ready',
    source: 'chat',
  };
  const actions = {
    onAction: jest.fn(),
    onDiscuss: jest.fn(),
    onDismiss: jest.fn(),
  };
  render(
    <>
      <ProposalCard
        proposal={candidate}
        local={{}}
        disabled={false}
        number={1}
        {...actions}
      />
      <ProposalCard
        proposal={{ ...candidate, id: 'corrected' }}
        local={{}}
        disabled={false}
        number={2}
        {...actions}
      />
    </>,
  );
  expect(
    screen.getByRole('heading', { name: 'Предложение 1 · редакция 2' }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole('heading', { name: 'Предложение 2 · редакция 2' }),
  ).toBeInTheDocument();
  expect(
    screen.getAllByText('Предложение без сохранённой связи с запросом'),
  ).toHaveLength(2);
});
