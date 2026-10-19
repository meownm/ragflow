import { getMaintenanceStatus } from '@/services/admin-service';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import MaintenanceStatus from './maintenance-status';

jest.mock('@/services/admin-service', () => ({
  getMaintenanceStatus: jest.fn(),
}));
jest.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, options?: { days?: number }) =>
      options ? `${key}:${options.days}` : key,
    i18n: { resolvedLanguage: 'ru' },
  }),
}));

function mount(data: object) {
  jest
    .mocked(getMaintenanceStatus)
    .mockResolvedValue({ data: { code: 0, data } } as never);
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MaintenanceStatus />
    </QueryClientProvider>,
  );
}

test('shows retention and the reason active indices were skipped', async () => {
  mount({
    configured: true,
    generated_at: new Date().toISOString(),
    logs: { retention_days: 60 },
    elastic: {
      store_bytes: 100,
      deleted_documents: 0,
      last_run: {
        finished_at: new Date().toISOString(),
        outcome: 'NO_WORK',
        indices: [{ index: 'ragflow_a', outcome: 'SKIPPED_WRITABLE' }],
      },
    },
  });
  expect(
    await screen.findByText('admin.maintenancePage.retention:60'),
  ).toBeInTheDocument();
  expect(
    screen.getByText('admin.maintenancePage.SKIPPED_WRITABLE'),
  ).toBeInTheDocument();
});

test('does not show missing configuration as successful', async () => {
  mount({ configured: false });
  expect(
    await screen.findByText('admin.maintenancePage.notConfigured'),
  ).toBeInTheDocument();
});

test('marks stale reports', async () => {
  mount({ configured: true, generated_at: '2020-01-01T00:00:00Z' });
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'admin.maintenancePage.stale',
  );
});

test('shows maintenance failure instead of success', async () => {
  mount({ configured: true, error: 'MAINTENANCE_FAILED' });
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'admin.maintenancePage.error',
  );
});
