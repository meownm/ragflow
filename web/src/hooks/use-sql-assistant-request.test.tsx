import type { AssistantSession } from '@/interfaces/database/sql-assistant';
import {
  fetchAssistantResult,
  fetchAssistantSession,
  sendAssistantCommand,
} from '@/services/sql-assistant-service';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import {
  SqlAssistantKeys,
  useAssistantCommand,
  useAssistantResult,
  useAssistantSession,
} from './use-sql-assistant-request';

jest.mock('@/services/sql-assistant-service', () => ({
  fetchAssistantResult: jest.fn(),
  fetchAssistantSession: jest.fn(),
  sendAssistantCommand: jest.fn(),
}));

function session(viewRevision: number, revision = 1): AssistantSession {
  return {
    id: 'research',
    revision,
    selected_result_id: 'saved',
    cards: [
      {
        kind: 'RESULT',
        run_id: 'saved',
        context: { view_revision: viewRevision },
      },
    ],
  } as AssistantSession;
}

it('loads an asynchronously changed view without reloading immutable rows for an unrelated chat revision', async () => {
  const fetch = jest.mocked(fetchAssistantResult);
  fetch.mockResolvedValueOnce({ view: { chart: null } } as Awaited<
    ReturnType<typeof fetchAssistantResult>
  >);
  fetch.mockResolvedValueOnce({
    view: { chart: { kind: 'bar', x: 'status', y: 'total' } },
  } as Awaited<ReturnType<typeof fetchAssistantResult>>);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  const { result, rerender, unmount } = renderHook(
    ({ value }) => useAssistantResult('actor', value, 0),
    {
      wrapper,
      initialProps: { value: session(0) },
    },
  );
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  expect(fetch).toHaveBeenCalledTimes(1);
  rerender({ value: session(0, 2) });
  expect(fetch).toHaveBeenCalledTimes(1);
  rerender({ value: session(1, 3) });
  await waitFor(() =>
    expect(result.current.data?.view.chart?.kind).toBe('bar'),
  );
  expect(fetch).toHaveBeenCalledTimes(2);
  unmount();
  client.clear();
});

it('reconciles an accepted command after losing its response and starts observing its job', async () => {
  const before = {
    ...session(0),
    project: { operation_state: 'IDLE', runs: [] },
  } as unknown as AssistantSession;
  const accepted = {
    ...before,
    revision: 2,
    project: { operation_state: 'RUNNING', runs: [] },
  } as unknown as AssistantSession;
  jest
    .mocked(fetchAssistantSession)
    .mockResolvedValueOnce(before)
    .mockResolvedValue(accepted);
  jest
    .mocked(sendAssistantCommand)
    .mockRejectedValueOnce(new TypeError('Lost response'));
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: Infinity } },
  });
  client.setQueryData(
    SqlAssistantKeys.session('another-actor', before.id),
    before,
  );
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  const { result, unmount } = renderHook(
    () => {
      const saved = useAssistantSession('actor', before.id);
      const command = useAssistantCommand('actor', saved.data ?? before);
      return { saved, command };
    },
    { wrapper },
  );
  await waitFor(() => expect(result.current.saved.data?.revision).toBe(1));
  await act(async () => {
    await expect(
      result.current.command.mutateAsync({
        type: 'message',
        payload: { text: 'Only amount >= 10' },
        key: 'same-delivery',
        revision: 1,
        projectVersion: 1,
      }),
    ).rejects.toThrow('Lost response');
  });
  await waitFor(() =>
    expect(result.current.saved.data?.project.operation_state).toBe('RUNNING'),
  );
  expect(sendAssistantCommand).toHaveBeenCalledTimes(1);
  expect(
    client.getQueryData(SqlAssistantKeys.session('another-actor', before.id)),
  ).toBe(before);
  unmount();
  client.clear();
});
