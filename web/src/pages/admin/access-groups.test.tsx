import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

import {
  createAccessGroup,
  deleteAccessGroup,
  getAccessGroupOptions,
  listAccessGroups,
  updateAccessGroup,
} from '@/services/admin-service';
import AdminAccessGroups from './access-groups';

jest.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key.split('.').pop() }),
}));
jest.mock('@/services/admin-service', () => ({
  createAccessGroup: jest.fn(),
  deleteAccessGroup: jest.fn(),
  getAccessGroupOptions: jest.fn(),
  listAccessGroups: jest.fn(),
  updateAccessGroup: jest.fn(),
}));
jest.mock('@/components/ui/message', () => ({
  __esModule: true,
  default: { success: jest.fn() },
}));

function mount() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <AdminAccessGroups />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  jest.clearAllMocks();
  jest
    .mocked(listAccessGroups)
    .mockResolvedValue({ data: { data: { items: [] } } } as never);
  jest.mocked(getAccessGroupOptions).mockResolvedValue({
    data: {
      data: {
        users: [
          {
            id: 'u1',
            email: 'user@example.com',
            nickname: 'User One',
            is_superuser: false,
          },
          {
            id: 'admin',
            email: 'admin@example.com',
            nickname: 'Administrator',
            is_superuser: true,
          },
        ],
        datasets: [{ id: 'kb1', name: 'Policies', tenant_id: 'admin' }],
        sections: ['dataset', 'business_documents'],
      },
    },
  } as never);
  jest
    .mocked(createAccessGroup)
    .mockResolvedValue({ data: { data: {} } } as never);
});

test('creates a group with selected users, sources and sections', async () => {
  mount();
  fireEvent.change(await screen.findByLabelText('name'), {
    target: { value: 'Legal' },
  });
  fireEvent.click(await screen.findByText('User One'));
  fireEvent.click(screen.getByText('Policies'));
  fireEvent.click(screen.getByText('dataset'));
  fireEvent.click(screen.getByRole('button', { name: 'save' }));

  await waitFor(() =>
    expect(createAccessGroup).toHaveBeenCalledWith({
      name: 'Legal',
      description: '',
      user_ids: ['u1'],
      dataset_ids: ['kb1'],
      sections: ['dataset'],
    }),
  );
  expect(deleteAccessGroup).not.toHaveBeenCalled();
  expect(updateAccessGroup).not.toHaveBeenCalled();
});

test('includes the administrator when creating a group', async () => {
  mount();
  fireEvent.change(await screen.findByLabelText('name'), {
    target: { value: 'Administrators' },
  });
  fireEvent.click(
    await screen.findByRole('checkbox', { name: 'Administrator' }),
  );
  fireEvent.click(screen.getByRole('button', { name: 'save' }));

  await waitFor(() =>
    expect(createAccessGroup).toHaveBeenCalledWith({
      name: 'Administrators',
      description: '',
      user_ids: ['admin'],
      dataset_ids: [],
      sections: [],
    }),
  );
});

test.each([false, true])(
  'toggles administrator membership when editing a group (initially selected: %s)',
  async (selected) => {
    const group = {
      id: 'g1',
      name: 'Legal',
      description: 'Policies access',
      user_ids: selected ? ['u1', 'admin'] : ['u1'],
      dataset_ids: ['kb1'],
      sections: ['dataset'],
    };
    jest.mocked(listAccessGroups).mockResolvedValue({
      data: { data: { items: [group] } },
    } as never);
    jest
      .mocked(updateAccessGroup)
      .mockResolvedValue({ data: { data: {} } } as never);
    mount();
    fireEvent.click(await screen.findByRole('button', { name: 'edit' }));

    const administrator = await screen.findByRole('checkbox', {
      name: 'Administrator',
    });
    expect(administrator).toHaveAttribute('aria-checked', String(selected));
    fireEvent.click(administrator);
    fireEvent.click(screen.getByRole('button', { name: 'save' }));

    const { id, ...input } = group;
    await waitFor(() =>
      expect(updateAccessGroup).toHaveBeenCalledWith(id, {
        ...input,
        user_ids: selected ? ['u1'] : ['u1', 'admin'],
      }),
    );
    expect(createAccessGroup).not.toHaveBeenCalled();
  },
);
