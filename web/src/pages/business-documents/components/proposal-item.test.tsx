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
  const onAccept = jest.fn();
  render(
    <ProposalItem
      proposal={item}
      proposalDecisionsOpen
      canDecide
      canAccept={canCancel}
      canCancelAcceptance={canCancel}
      pending={false}
      onDecide={jest.fn()}
      onAccept={onAccept}
      onCancelAcceptance={onCancelAcceptance}
    />,
  );
  return { onCancelAcceptance, onAccept };
}

test.each([
  ['WAITING', 'Принятие записано, но применение ещё не запущено.'],
  ['PENDING', 'Применение ожидает запуска задачи.'],
  ['RUNNING', 'Изменения вносятся.'],
  ['RETRY', 'Запланирован автоматический повтор.'],
  ['DEAD', 'Применение завершилось ошибкой.'],
  ['PREPARED', 'Изменения подготовлены и ожидают подтверждения.'],
  ['PREVIEW_STALE', 'Подготовленные изменения больше не актуальны'],
  ['COMPLETED', 'применение этого предложения не подтверждено.'],
] as const)('explains unapplied proposal state %s', (status, expected) => {
  show({ ...proposal, application: { status } });
  expect(screen.getByTestId('proposal-application-p1')).toHaveTextContent(
    expected,
  );
});

test('shows the saved failure and permits retry or cancellation', () => {
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
  fireEvent.click(screen.getByRole('button', { name: 'Применить' }));
  expect(callbacks.onAccept).toHaveBeenCalledTimes(1);
});

test('disables cancellation and retry during a job or without edit permission', () => {
  show({ ...proposal, application: { status: 'RUNNING' } }, false);
  expect(
    screen.getByRole('button', { name: 'Отменить принятие' }),
  ).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Применить' })).toBeDisabled();
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
