import { listUsers } from '@/services/admin-service';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import AdminUserManagement from './users';

jest.mock('react-i18next', () => ({
  ...jest.requireActual('react-i18next'),
  useTranslation: () => ({ t: (key: string) => key.split('.').pop() }),
}));
jest.mock('@/routes', () => ({
  Routes: { AdminUserManagement: '/admin/users' },
}));
jest.mock('@/services/admin-service', () => ({
  listUsers: jest.fn(),
  listRoles: jest.fn(),
}));
jest.mock('./forms/change-password-form', () => ({
  __esModule: true,
  default: () => ({
    id: 'password',
    form: { reset: jest.fn() },
    FormComponent: () => null,
  }),
}));
jest.mock('./forms/user-form', () => ({
  __esModule: true,
  default: () => ({
    id: 'user',
    form: { reset: jest.fn() },
    FormComponent: () => null,
  }),
}));
jest.mock('@/services/business-document-service', () => ({
  updateBusinessDocumentUserRole: jest.fn(),
}));
jest.mock('@/utils', () => ({ rsaPsw: (value: string) => value }));

function mount() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <AdminUserManagement />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => jest.clearAllMocks());

test('403 is an access state rather than an empty user list', async () => {
  jest
    .mocked(listUsers)
    .mockResolvedValue({ data: { code: 403, data: null } } as never);
  mount();
  expect(
    await screen.findByRole('region', { name: 'Нет доступа к пользователям' }),
  ).toBeInTheDocument();
  expect(screen.queryByRole('table')).toBeNull();
  expect(screen.getByRole('button', { name: 'newUser' })).toBeDisabled();
  expect(screen.queryByText('Пользователей пока нет')).toBeNull();
});

test('a failed load can recover without showing a false empty list', async () => {
  jest
    .mocked(listUsers)
    .mockRejectedValueOnce(new Error('offline'))
    .mockResolvedValue({ data: { code: 0, data: [] } } as never);
  mount();
  await screen.findByRole('region', {
    name: 'Не удалось загрузить пользователей',
  });
  fireEvent.click(screen.getByRole('button', { name: 'Повторить' }));
  expect(
    await screen.findByRole('region', { name: 'Пользователей пока нет' }),
  ).toBeInTheDocument();
});

test('filtering existing users produces a distinct no-results state', async () => {
  jest.mocked(listUsers).mockResolvedValue({
    data: {
      code: 0,
      data: [
        {
          id: 'u1',
          email: 'user@example.test',
          nickname: 'Аналитик',
          is_active: '1',
          is_superuser: false,
          business_document_role: 'AUTHOR',
        },
      ],
    },
  } as never);
  mount();
  await screen.findByText('user@example.test');
  fireEvent.change(screen.getByLabelText('Поиск по имени или почте'), {
    target: { value: 'нет такого пользователя' },
  });
  expect(
    await screen.findByRole('region', { name: 'Пользователи не найдены' }),
  ).toBeInTheDocument();
});
