import {
  deleteEvaUserCredential,
  listEvaUserCredentials,
  putEvaUserCredential,
} from '@/services/user-service';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { EvaCredentialProfile } from './eva-credential-profile';

jest.mock('@/hooks/common-hooks', () => ({
  useTranslate: () => ({ t: (key: string) => key }),
}));
jest.mock('@/hooks/use-user-setting-request', () => ({
  useFetchUserInfo: () => ({ data: { id: 'user-1' } }),
}));
jest.mock('@/services/user-service', () => ({
  listEvaUserCredentials: jest.fn(),
  putEvaUserCredential: jest.fn(),
  deleteEvaUserCredential: jest.fn(),
}));
jest.mock('@/components/ui/message', () => ({
  __esModule: true,
  default: { success: jest.fn() },
}));

const item = {
  scope: 'https://eva.example/api',
  configured: false,
  connector_id: 'connection-1',
  connectors: [{ id: 'connection-1', name: 'EVA' }],
};
function mount(
  url = '/user-setting/profile?eva=connection-1&returnTo=%2Fbusiness-documents%2Fdoc-1',
) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <MemoryRouter initialEntries={[url]}>
      <QueryClientProvider client={client}>
        <EvaCredentialProfile />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}
beforeEach(() => {
  jest.clearAllMocks();
  jest
    .mocked(listEvaUserCredentials)
    .mockResolvedValue({ data: { code: 0, data: { items: [item] } } } as never);
  jest.mocked(putEvaUserCredential).mockResolvedValue({
    data: { code: 0, data: { configured: true } },
  } as never);
  jest
    .mocked(deleteEvaUserCredential)
    .mockResolvedValue({ data: { code: 0 } } as never);
});

test('opens the requested token form and verifies before returning to requirements', async () => {
  mount();
  const input = await screen.findByTestId('eva-token-input');
  expect(screen.getByTestId('eva-token-save')).toHaveTextContent(
    'evaTokenVerifyAndSave',
  );
  fireEvent.change(input, { target: { value: 'candidate-token' } });
  fireEvent.click(screen.getByTestId('eva-token-save'));
  await waitFor(() =>
    expect(putEvaUserCredential).toHaveBeenCalledWith(
      'connection-1',
      'candidate-token',
    ),
  );
  expect(await screen.findByText('evaTokenVerified')).toBeVisible();
  expect(screen.getByRole('link', { name: 'evaTokenReturn' })).toHaveAttribute(
    'href',
    '/business-documents/doc-1',
  );
});

test('keeps the form open and shows rejection instead of a successful result', async () => {
  jest.mocked(putEvaUserCredential).mockResolvedValueOnce({
    data: { code: 102, message: 'EVA отклонила токен' },
  } as never);
  mount();
  fireEvent.change(await screen.findByTestId('eva-token-input'), {
    target: { value: 'rejected-token' },
  });
  fireEvent.click(screen.getByTestId('eva-token-save'));
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'EVA отклонила токен',
  );
  expect(screen.getByTestId('eva-token-input')).toBeVisible();
  expect(screen.queryByText('evaTokenVerified')).not.toBeInTheDocument();
});

test('verifies the requested connection when multiple spaces share one EVA origin', async () => {
  jest.mocked(listEvaUserCredentials).mockResolvedValue({
    data: {
      code: 0,
      data: {
        items: [
          {
            ...item,
            connectors: [
              ...item.connectors,
              { id: 'connection-2', name: 'Requirements' },
            ],
          },
        ],
      },
    },
  } as never);
  mount('/user-setting/profile?eva=connection-2');
  fireEvent.change(await screen.findByTestId('eva-token-input'), {
    target: { value: 'candidate-token' },
  });
  fireEvent.click(screen.getByTestId('eva-token-save'));
  await waitFor(() =>
    expect(putEvaUserCredential).toHaveBeenCalledWith(
      'connection-2',
      'candidate-token',
    ),
  );
});

test('does not offer an external return URL or open an inaccessible token form', async () => {
  mount(
    '/user-setting/profile?eva=other-tenant&returnTo=https%3A%2F%2Fother.example',
  );
  await screen.findByTestId('eva-token-edit-connection-1');
  expect(screen.queryByTestId('eva-token-input')).not.toBeInTheDocument();
  expect(
    screen.queryByRole('link', { name: 'evaTokenReturn' }),
  ).not.toBeInTheDocument();
});

test('shows expiration warning for a configured token', async () => {
  jest.mocked(listEvaUserCredentials).mockResolvedValue({
    data: {
      code: 0,
      data: {
        items: [
          {
            ...item,
            configured: true,
            expires_at: Date.now() + 86_400_000,
            expiry_checked_at: Date.now(),
          },
        ],
      },
    },
  } as never);
  mount('/user-setting/profile');
  expect(
    await screen.findByTestId('eva-token-expiry-warning'),
  ).toHaveTextContent('evaTokenExpiresDay');
});
