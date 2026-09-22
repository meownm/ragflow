import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react';

import { listAuditEvents } from '@/services/admin-service';

import AdminBusinessDocumentsQuality from './business-documents-quality';

jest.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key.split('.').pop() }),
}));
jest.mock('@/services/admin-service', () => ({
  listAuditEvents: jest.fn(),
}));

function response(enabled = true) {
  return {
    data: {
      code: 0,
      data: {
        observability: {
          enabled,
          grafana_url: enabled ? 'http://localhost:3001/' : '',
          loki_datasource_uid: 'loki',
          tempo_datasource_uid: 'tempo',
        },
      },
    },
  };
}

function mount() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <AdminBusinessDocumentsQuality />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  jest.clearAllMocks();
  jest.mocked(listAuditEvents).mockResolvedValue(response() as never);
});

test('embeds the provisioned quality dashboard and offers a direct link', async () => {
  mount();

  const frame = await screen.findByTitle('frameTitle');
  const expectedUrl =
    'http://localhost:3001/d/business-documents-ai-quality/business-documents-ai-quality?orgId=1&refresh=30s&kiosk';
  expect(frame).toHaveAttribute('src', expectedUrl);
  expect(screen.getByRole('link', { name: /openGrafana/ })).toHaveAttribute(
    'href',
    expectedUrl,
  );
  expect(frame).toHaveClass('opacity-0');
  fireEvent.load(frame);
  expect(frame).toHaveClass('opacity-100');
});

test('shows an explicit unavailable state when observability is disabled', async () => {
  jest.mocked(listAuditEvents).mockResolvedValue(response(false) as never);

  mount();

  expect(await screen.findByText('unavailableDescription')).toBeInTheDocument();
  expect(
    screen.queryByTestId('business-documents-quality-frame'),
  ).not.toBeInTheDocument();
});

test('shows a configuration error instead of an empty frame', async () => {
  jest.mocked(listAuditEvents).mockRejectedValue(new Error('offline'));

  mount();

  expect(await screen.findByRole('alert')).toHaveTextContent('loadError');
});
