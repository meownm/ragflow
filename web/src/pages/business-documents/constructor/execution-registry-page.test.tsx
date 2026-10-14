import {
  createBusinessDocumentSqlCatalogBinding,
  createBusinessDocumentSqlExecutionProfile,
  listBusinessDocumentSqlCatalogBindings,
  listBusinessDocumentSqlExecutionConnectors,
  listBusinessDocumentSqlExecutionProfiles,
  updateBusinessDocumentSqlCatalogBinding,
  updateBusinessDocumentSqlExecutionProfile,
} from '@/services/business-document-service';
import { useQuery } from '@tanstack/react-query';
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import ExecutionRegistryPage from './execution-registry-page';

jest.mock('@/hooks/use-user-setting-request', () => ({
  useFetchUserInfo: () => ({ data: { id: 'admin-1' }, loading: false }),
}));
jest.mock('@tanstack/react-query', () => ({
  useQuery: jest.fn(),
}));

jest.mock('@/services/business-document-service', () => ({
  createBusinessDocumentSqlCatalogBinding: jest.fn(),
  createBusinessDocumentSqlExecutionProfile: jest.fn(),
  listBusinessDocumentSqlCatalogBindings: jest.fn(),
  listBusinessDocumentSqlExecutionConnectors: jest.fn(),
  listBusinessDocumentSqlExecutionProfiles: jest.fn(),
  updateBusinessDocumentSqlCatalogBinding: jest.fn(),
  updateBusinessDocumentSqlExecutionProfile: jest.fn(),
}));

const connector = {
  id: 'connector-1',
  name: 'Warehouse PostgreSQL',
  source: 'postgresql' as const,
  database: 'analytics',
  available: true,
};

const profile = {
  id: 'profile-1',
  name: 'Warehouse RO',
  dialect: 'postgres' as const,
  allowed_schemas: ['dwh', 'ref'],
  statement_timeout_ms: 30000,
  max_rows: 1000,
  max_result_bytes: 5000000,
  enabled: true,
  available: true,
  policy_valid: true,
  connector_identity_matches: true,
  version: 3,
  policy_fingerprint: 'sha256:policy',
  target_database: 'analytics',
  connector_available: true,
  connector: {
    id: connector.id,
    name: connector.name,
    source: connector.source,
    database: connector.database,
  },
  created_by: 'admin-1',
  updated_by: 'admin-1',
};

const binding = {
  id: 'binding-1',
  catalog_service: 'warehouse',
  catalog_database: 'analytics',
  catalog_schema: 'dwh',
  execution_profile_id: profile.id,
  enabled: true,
  version: 2,
  created_by: 'admin-1',
  updated_by: 'admin-1',
};

const mockedListConnectors = jest.mocked(
  listBusinessDocumentSqlExecutionConnectors,
);
const mockedListProfiles = jest.mocked(
  listBusinessDocumentSqlExecutionProfiles,
);
const mockedListBindings = jest.mocked(listBusinessDocumentSqlCatalogBindings);
const mockedCreateProfile = jest.mocked(
  createBusinessDocumentSqlExecutionProfile,
);
const mockedUpdateProfile = jest.mocked(
  updateBusinessDocumentSqlExecutionProfile,
);
const mockedCreateBinding = jest.mocked(
  createBusinessDocumentSqlCatalogBinding,
);
const mockedUpdateBinding = jest.mocked(
  updateBusinessDocumentSqlCatalogBinding,
);

function renderRegistry() {
  return render(
    <MemoryRouter>
      <ExecutionRegistryPage />
    </MemoryRouter>,
  );
}

describe('ExecutionRegistryPage', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    jest.mocked(useQuery).mockReturnValue({
      data: { access_role: 'ADMIN' },
      isPending: false,
    } as never);
    mockedListConnectors.mockResolvedValue({
      schema_version: '1',
      items: [connector],
    });
    mockedListProfiles.mockResolvedValue({
      schema_version: '1',
      items: [profile],
    });
    mockedListBindings.mockResolvedValue({
      schema_version: '1',
      items: [binding],
    });
    mockedCreateProfile.mockResolvedValue(profile);
    mockedUpdateProfile.mockResolvedValue({ ...profile, version: 4 });
    mockedCreateBinding.mockResolvedValue(binding);
    mockedUpdateBinding.mockResolvedValue({ ...binding, version: 3 });
  });

  it('does not load registry data for a non-admin', () => {
    jest.mocked(useQuery).mockReturnValue({
      data: { access_role: 'AUTHOR_CREATOR' },
      isPending: false,
    } as never);
    renderRegistry();
    expect(screen.getByRole('alert')).toHaveTextContent(
      'только администратору',
    );
    expect(mockedListProfiles).not.toHaveBeenCalled();
  });

  it('creates complete profiles and exact catalog bindings', async () => {
    renderRegistry();

    expect(await screen.findByText('Warehouse RO')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Добавить профиль' }));

    fireEvent.change(screen.getByLabelText('Название'), {
      target: { value: 'Finance RO' },
    });
    fireEvent.change(screen.getByLabelText('Разрешённые схемы PostgreSQL'), {
      target: { value: 'finance, ref, finance' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Создать профиль' }));

    await waitFor(() => expect(mockedCreateProfile).toHaveBeenCalledTimes(1));
    expect(mockedCreateProfile).toHaveBeenCalledWith({
      schema_version: '1',
      name: 'Finance RO',
      connector_id: 'connector-1',
      dialect: 'postgres',
      allowed_schemas: ['finance', 'ref'],
      statement_timeout_ms: 30000,
      max_rows: 1000,
      max_result_bytes: 5000000,
      enabled: true,
    });

    fireEvent.click(screen.getByRole('button', { name: /Привязки схем/ }));
    expect(screen.getByText('warehouse/analytics/dwh')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Добавить привязку' }));
    fireEvent.change(screen.getByLabelText('Сервис'), {
      target: { value: 'warehouse' },
    });
    fireEvent.change(screen.getByLabelText('База данных'), {
      target: { value: 'analytics' },
    });
    fireEvent.change(screen.getByLabelText('Схема'), {
      target: { value: 'ref' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Привязать схему' }));

    await waitFor(() => expect(mockedCreateBinding).toHaveBeenCalledTimes(1));
    expect(mockedCreateBinding).toHaveBeenCalledWith({
      schema_version: '1',
      catalog_service: 'warehouse',
      catalog_database: 'analytics',
      catalog_schema: 'ref',
      execution_profile_id: 'profile-1',
      enabled: true,
    });
  });

  it('uses record versions when editing or disabling registry entries', async () => {
    renderRegistry();

    await screen.findByText('Warehouse RO');
    const profileList = screen.getByTestId('sql-execution-profile-list');
    fireEvent.click(
      within(profileList).getByRole('button', { name: 'Изменить' }),
    );
    fireEvent.change(screen.getByLabelText('Название'), {
      target: { value: 'Warehouse read only' },
    });
    fireEvent.change(screen.getByLabelText('Время выполнения, с'), {
      target: { value: '12.5' },
    });
    fireEvent.change(screen.getByLabelText('Размер результата, МБ'), {
      target: { value: '2.5' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить профиль' }));

    await waitFor(() => expect(mockedUpdateProfile).toHaveBeenCalledTimes(1));
    expect(mockedUpdateProfile).toHaveBeenCalledWith(
      'profile-1',
      expect.objectContaining({
        name: 'Warehouse read only',
        statement_timeout_ms: 12500,
        max_result_bytes: 2500000,
        expected_version: 3,
      }),
    );

    fireEvent.click(screen.getByRole('button', { name: /Привязки схем/ }));
    const bindingList = screen.getByTestId('sql-catalog-binding-list');
    fireEvent.click(
      within(bindingList).getByRole('button', { name: 'Отключить' }),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Отключить' }));

    await waitFor(() => expect(mockedUpdateBinding).toHaveBeenCalledTimes(1));
    expect(mockedUpdateBinding).toHaveBeenCalledWith('binding-1', {
      schema_version: '1',
      enabled: false,
      expected_version: 2,
    });
  });

  it('uses a minimal versioned command to disable a profile', async () => {
    renderRegistry();

    await screen.findByText('Warehouse RO');
    const profileList = screen.getByTestId('sql-execution-profile-list');
    fireEvent.click(
      within(profileList).getByRole('button', { name: 'Отключить' }),
    );
    expect(screen.getByText(/Активных привязок: 1/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Отключить' }));

    await waitFor(() => expect(mockedUpdateProfile).toHaveBeenCalledTimes(1));
    expect(mockedUpdateProfile).toHaveBeenCalledWith('profile-1', {
      schema_version: '1',
      enabled: false,
      expected_version: 3,
    });
  });

  it('explains unavailable profiles and blocks them for a new binding', async () => {
    const drifted = {
      ...profile,
      id: 'profile-drifted',
      name: 'Drifted target',
      available: false,
      connector_identity_matches: false,
    };
    const missing = {
      ...profile,
      id: 'profile-missing',
      name: 'Missing connector',
      available: false,
      connector_available: false,
      connector_identity_matches: false,
    };
    const corrupt = {
      ...profile,
      id: 'profile-corrupt',
      name: 'Corrupt policy',
      available: false,
      policy_valid: false,
    };
    mockedListProfiles.mockResolvedValue({
      schema_version: '1',
      items: [profile, drifted, missing, corrupt],
    });
    mockedListBindings.mockResolvedValue({
      schema_version: '1',
      items: [{ ...binding, execution_profile_id: missing.id }],
    });

    renderRegistry();

    expect(
      await screen.findByText(/База данных или роль подключения изменились/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Коннектор удалён, имеет другой тип/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/политика не прошла проверку контракта/),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /Привязки схем/ }));
    expect(screen.getByText('Профиль недоступен')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Добавить привязку' }));
    const profileSelect = screen.getByLabelText('Профиль выполнения');
    expect(
      within(profileSelect).getByRole('option', {
        name: /Drifted target.*недоступен/,
      }),
    ).toBeDisabled();
  });
});
