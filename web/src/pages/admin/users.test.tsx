import { deleteUser, listUsers } from '@/services/admin-service';
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
  deleteUser: jest.fn(),
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

const clients: QueryClient[] = [];
function mount() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  clients.push(client);
  render(
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <AdminUserManagement />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => jest.clearAllMocks());
afterEach(() => clients.splice(0).forEach((client) => client.clear()));

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

function pagedUsers() {
  return Array.from({ length: 11 }, (_, index) => ({
    id: `u${index}`,
    email:
      index === 0
        ? 'unique-first@example.test'
        : `person-${index}@example.test`,
    nickname: `Аналитик ${index}`,
    is_active: '1',
    is_superuser: false,
    business_document_role: 'AUTHOR_CREATOR',
  }));
}

test('searching from the last page shows a matching user on the remaining page', async () => {
  jest.mocked(listUsers).mockResolvedValue({
    data: { code: 0, data: pagedUsers() },
  } as never);
  mount();
  await screen.findByText('unique-first@example.test');
  fireEvent.click(screen.getByText('2', { selector: 'a' }));
  await screen.findByText('person-10@example.test');
  fireEvent.change(screen.getByLabelText('Поиск по имени или почте'), {
    target: { value: 'unique-first@example.test' },
  });
  expect(await screen.findByText('unique-first@example.test')).toBeVisible();
  expect(screen.queryByText('Пользователи не найдены')).toBeNull();
});

test('deleting the only user on the last page returns to a populated page', async () => {
  const users = pagedUsers();
  jest
    .mocked(listUsers)
    .mockResolvedValueOnce({ data: { code: 0, data: users } } as never)
    .mockResolvedValue({
      data: { code: 0, data: users.slice(0, 10) },
    } as never);
  jest
    .mocked(deleteUser)
    .mockResolvedValue({ data: { code: 0, data: true } } as never);
  mount();
  await screen.findByText('unique-first@example.test');
  fireEvent.click(screen.getByText('2', { selector: 'a' }));
  fireEvent.click(
    await screen.findByRole('button', {
      name: 'Удалить пользователя person-10@example.test',
    }),
  );
  fireEvent.click(screen.getByRole('button', { name: /^delete$/ }));
  expect(await screen.findByText('unique-first@example.test')).toBeVisible();
  expect(screen.queryByText('Пользователи не найдены')).toBeNull();
});
