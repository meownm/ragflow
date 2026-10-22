import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor } from '@testing-library/react';
import { useSystemConfig } from './use-system-request';

const mockGetConfig = jest.fn();
jest.mock('@/services/user-service', () => ({
  __esModule: true,
  default: { getSystemConfig: () => mockGetConfig() },
}));

const load = () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return renderHook(useSystemConfig, {
    wrapper: ({ children }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    ),
  });
};

it.each([
  { code: 403, data: { visibleSections: ['sql_projects'] } },
  { code: 0, data: {} },
  { code: 0, data: { visibleSections: 'all' } },
])(
  'does not expose a policy for an error or malformed response: %j',
  async (data) => {
    mockGetConfig.mockResolvedValue({ data });
    const { result } = load();
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.config).toBeUndefined();
  },
);

it('accepts empty policies and keeps administrator access separate from the menu', async () => {
  mockGetConfig.mockResolvedValue({
    data: {
      code: 0,
      data: { visibleSections: [], accessibleSections: ['sql_projects'] },
    },
  });
  const { result } = load();
  await waitFor(() => expect(result.current.loading).toBe(false));
  expect(result.current.config?.visibleSections).toEqual([]);
  expect(result.current.config?.accessibleSections).toEqual(['sql_projects']);
});
