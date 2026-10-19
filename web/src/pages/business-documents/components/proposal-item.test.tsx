import { fireEvent, render, screen } from '@testing-library/react';
import type { BusinessDocumentProposal } from '../types';
import { ProposalItem } from './proposal-item';

const proposal: BusinessDocumentProposal = {
  proposal_id: 'p1',
  text: 'Уточнить требования',
  decision: 'ACCEPTED',
  applied: false,
};

function show(item = proposal, canCancel = true) {
  const onCancelAcceptance = jest.fn();
  const onDecide = jest.fn();
  render(
    <ProposalItem
      proposal={item}
      proposalDecisionsOpen
      canDecide
      canCancelAcceptance={canCancel}
      pending={false}
      onDecide={onDecide}
      onCancelAcceptance={onCancelAcceptance}
    />,
  );
  return { onCancelAcceptance, onDecide };
}

test.each([
  ['WAITING', 'Предложение принято. Текст документа не изменён.'],
  ['PENDING', 'Применение ожидает запуска задачи.'],
  ['RUNNING', 'Изменения вносятся.'],
  ['RETRY', 'Запланирован автоматический повтор.'],
  ['DEAD', 'Подготовка изменений завершилась ошибкой.'],
  ['PREPARED', 'Изменения подготовлены.'],
  ['PREVIEW_STALE', 'Подготовленные изменения больше не актуальны'],
  ['COMPLETED', 'применение этого предложения не подтверждено.'],
] as const)('explains unapplied proposal state %s', (status, expected) => {
  show({ ...proposal, application: { status } });
  expect(screen.getByTestId('proposal-application-p1')).toHaveTextContent(
    expected,
  );
});

test('shows the saved failure and permits cancellation without immediate application', () => {
  const callbacks = show({
    ...proposal,
    application: {
      status: 'DEAD',
      error: { code: 'MODEL_UNAVAILABLE', message: 'Модель недоступна' },
    },
  });
  expect(screen.getByText('Модель недоступна')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Отменить принятие' }));
  expect(callbacks.onCancelAcceptance).toHaveBeenCalledTimes(1);
  expect(screen.queryByRole('button', { name: 'Применить' })).toBeNull();
  expect(callbacks.onDecide).not.toHaveBeenCalled();
});

test('disables cancellation during a job or without edit permission', () => {
  show({ ...proposal, application: { status: 'RUNNING' } }, false);
  expect(
    screen.getByRole('button', { name: 'Отменить принятие' }),
  ).toBeDisabled();
  expect(screen.queryByRole('button', { name: 'Применить' })).toBeNull();
});

test.each(['PENDING', 'REJECTED', 'APPLIED'] as const)(
  'does not offer acceptance cancellation for %s',
  (state) => {
    show({
      ...proposal,
      decision: state === 'APPLIED' ? 'ACCEPTED' : state,
      applied: state === 'APPLIED',
    });
    expect(
      screen.queryByRole('button', { name: 'Отменить принятие' }),
    ).toBeNull();
    expect(screen.queryByTestId('proposal-application-p1')).toBeNull();
  },
);

test('accepts a pending proposal as a decision', () => {
  const callbacks = show({ ...proposal, decision: 'PENDING' });
  fireEvent.click(screen.getByRole('button', { name: 'Принять' }));
  expect(callbacks.onDecide).toHaveBeenCalledWith({
    proposal_id: 'p1',
    decision: 'ACCEPTED',
  });
});
