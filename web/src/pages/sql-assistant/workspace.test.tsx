import type { AssistantSession } from '@/interfaces/database/sql-assistant';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { AssistantWorkspace } from './workspace';

const mockMutateAsync = jest.fn();
jest.mock('@/hooks/use-sql-assistant-request', () => ({
  assistantCommandId: () => 'delivery-key',
  useAssistantCommand: () => ({
    mutateAsync: mockMutateAsync,
    isPending: false,
  }),
  useAssistantDialogueOptions: () => ({
    data: { references: [], metrics: [] },
  }),
  useAssistantResult: () => ({
    data: { sql: 'SELECT id', parameters: {}, expires_at: Date.now() + 60_000 },
  }),
}));
jest.mock('./result', () => ({ AssistantResultPanel: () => null }));
jest.mock('./builder', () => ({ AssistantBuilder: () => null }));
jest.mock('./definitions', () => ({ AssistantDefinitions: () => null }));
jest.mock(
  '@/pages/business-documents/constructor/sql-project-connection',
  () => ({
    PreQueryConnection: () => null,
  }),
);

function session(run: string, revision: number): AssistantSession {
  return {
    id: 'session',
    revision,
    selected_result_id: run,
    messages: [],
    project: {
      title: 'Заказы',
      state_version: revision,
      operation_state: 'IDLE',
      capabilities: { edit: false },
      runs: [],
    },
    cards: [
      {
        kind: 'RESULT',
        run_id: run,
        expires_at: Date.now() + 60_000,
        context: { run_id: run, view_revision: revision },
      },
    ],
  } as unknown as AssistantSession;
}

it('replays the exact delivery after response loss and a selected-result change', async () => {
  const before = session('old-run', 1);
  const after = session('new-run', 9);
  mockMutateAsync
    .mockRejectedValueOnce(new Error('response lost after commit'))
    .mockResolvedValueOnce({ session: after, effect: { kind: 'message' } });
  const props = {
    actor: 'actor',
    session: before,
    refresh: async () => undefined,
  };
  const view = render(<AssistantWorkspace {...props} />);
  const input = screen.getByLabelText('Сообщение SQL-помощнику');
  const question = 'Оставь amount не меньше 10';
  fireEvent.change(input, { target: { value: question } });
  fireEvent.click(screen.getByRole('button', { name: 'Отправить сообщение' }));
  await screen.findByRole('button', { name: 'Повторить отправку' });
  expect(input).toHaveValue(question);
  const wire = mockMutateAsync.mock.calls[0][0];
  view.rerender(<AssistantWorkspace {...props} session={after} />);
  fireEvent.click(screen.getByRole('button', { name: 'Повторить отправку' }));
  await waitFor(() => expect(input).toHaveValue(''));
  expect(mockMutateAsync).toHaveBeenCalledTimes(2);
  expect(mockMutateAsync.mock.calls[1][0]).toEqual(wire);
  expect(wire.payload.run_id).toBe('old-run');
  expect(wire.revision).toBe(1);
  fireEvent.change(input, { target: { value: question } });
  view.rerender(<AssistantWorkspace {...props} session={after} />);
  expect(input).toHaveValue(question);
});
