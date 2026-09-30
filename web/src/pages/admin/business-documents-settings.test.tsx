import message from '@/components/ui/message';
import {
  discoverBusinessDocumentsEvaSpaces,
  getBusinessDocumentsCatalogStatus,
  getBusinessDocumentsSettings,
  importBusinessDocumentsCatalog,
  setBusinessDocumentsSettings,
} from '@/services/admin-service';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import AdminBusinessDocumentsSettings from './business-documents-settings';

jest.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key.split('.').pop() }),
}));
jest.mock('@/services/admin-service', () => ({
  getBusinessDocumentsSettings: jest.fn(),
  setBusinessDocumentsSettings: jest.fn(),
  discoverBusinessDocumentsEvaSpaces: jest.fn(),
  getBusinessDocumentsCatalogStatus: jest.fn(),
  importBusinessDocumentsCatalog: jest.fn(),
}));
jest.mock('@/components/ui/message', () => ({
  __esModule: true,
  default: { success: jest.fn() },
}));
jest.mock('@/components/originui/select-with-search', () => ({
  SelectWithSearch: ({
    value,
    onChange,
    options,
  }: {
    value: string;
    onChange: (value: string) => void;
    options: { value: string; label: string }[];
  }) => (
    <select
      aria-label="spaces"
      value={value}
      onChange={(event) => onChange(event.target.value)}
    >
      {options.map((option) => (
        <option key={option.value} value={option.value}>
          {option.label}
        </option>
      ))}
    </select>
  ),
}));

const saved = {
  api_base_url: 'https://eva.example',
  web_base_url: 'https://eva.example',
  project_id: 'CmfProject:docs',
  verify_ssl: true,
  include_archived: false,
  token_configured: true,
};
const response = (connection = saved) => ({
  data: { code: 0, data: { eva_connection: connection } },
});
const catalogStatus = {
  source_id: 'BCM_Bank',
  source_version: 'v24',
  source_sha256: 'a'.repeat(64),
  filename: 'BCM_v24.json',
  storage: 'uploaded' as const,
  catalog_items: 70,
  active_items: 70,
  total_items: 71,
  imported_at: '2026-09-30T10:00:00+00:00',
  imported_by: 'admin-1',
};
function mount() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <AdminBusinessDocumentsSettings />
    </QueryClientProvider>,
  );
}
beforeEach(() => {
  jest.clearAllMocks();
  jest
    .mocked(getBusinessDocumentsSettings)
    .mockResolvedValue(response() as never);
  jest
    .mocked(setBusinessDocumentsSettings)
    .mockResolvedValue(response() as never);
  jest.mocked(getBusinessDocumentsCatalogStatus).mockResolvedValue({
    data: { code: 0, data: catalogStatus },
  } as never);
  jest.mocked(importBusinessDocumentsCatalog).mockResolvedValue({
    data: {
      code: 0,
      data: {
        ...catalogStatus,
        source_version: 'v25',
        created_items: 2,
        updated_items: 68,
        reactivated_items: 0,
        deactivated_items: 2,
      },
    },
  } as never);
});

test('uploads the BCM catalog and refreshes its status without changing documents', async () => {
  mount();
  expect(await screen.findByText('BCM_Bank · v24')).toBeInTheDocument();
  expect(screen.getByText('70')).toBeInTheDocument();
  const file = new File(['{"L1":[]}'], 'BCM_v25.json', {
    type: 'application/json',
  });

  fireEvent.change(screen.getByLabelText('catalogSelect'), {
    target: { files: [file] },
  });
  fireEvent.click(screen.getByTestId('business-documents-catalog-import'));

  await waitFor(() =>
    expect(importBusinessDocumentsCatalog).toHaveBeenCalledWith(file),
  );
  expect(await screen.findByText('BCM_Bank · v25')).toBeInTheDocument();
  expect(message.success).toHaveBeenCalledWith('catalogImported');
});

test('shows the import validation error when the status request also failed', async () => {
  jest.mocked(getBusinessDocumentsCatalogStatus).mockResolvedValue({
    data: { code: 500, message: 'Status unavailable' },
  } as never);
  jest.mocked(importBusinessDocumentsCatalog).mockResolvedValue({
    data: { code: 400, message: 'L1 must be a non-empty array' },
  } as never);
  mount();
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'Status unavailable',
  );
  const file = new File(['{}'], 'BCM_v25.json', {
    type: 'application/json',
  });

  fireEvent.change(screen.getByLabelText('catalogSelect'), {
    target: { files: [file] },
  });
  fireEvent.click(screen.getByTestId('business-documents-catalog-import'));

  expect(await screen.findByRole('alert')).toHaveTextContent(
    'L1 must be a non-empty array',
  );
});

test('saves a standalone connection without a data source or copying a secret to the form', async () => {
  jest.mocked(getBusinessDocumentsSettings).mockResolvedValue(
    response({
      ...saved,
      api_base_url: '',
      web_base_url: '',
      project_id: '',
      token_configured: false,
    }) as never,
  );
  mount();
  await waitFor(() =>
    expect(screen.getByLabelText('api_base_url')).toBeEnabled(),
  );
  expect(screen.getByLabelText('api_base_url')).toHaveAttribute(
    'placeholder',
    'https://eva.example.com',
  );
  fireEvent.change(screen.getByLabelText('api_base_url'), {
    target: { value: saved.api_base_url },
  });
  fireEvent.change(screen.getByLabelText('web_base_url'), {
    target: { value: saved.web_base_url },
  });
  fireEvent.change(screen.getByLabelText('evaSpace'), {
    target: { value: saved.project_id },
  });
  fireEvent.change(screen.getByLabelText('token'), {
    target: { value: 'secret' },
  });
  fireEvent.click(screen.getByTestId('business-documents-settings-save'));
  await waitFor(() =>
    expect(setBusinessDocumentsSettings).toHaveBeenCalledWith({
      api_base_url: saved.api_base_url,
      web_base_url: saved.web_base_url,
      project_id: saved.project_id,
      verify_ssl: true,
      include_archived: false,
      eva_api_token: 'secret',
      clear_token: false,
    }),
  );
  await waitFor(() => expect(screen.getByLabelText('token')).toHaveValue(''));
  expect(message.success).toHaveBeenCalledWith('saved');
});

test('keeps the stored token with a blank input and supports explicit removal', async () => {
  mount();
  await waitFor(() =>
    expect(screen.getByLabelText('api_base_url')).toHaveValue(
      saved.api_base_url,
    ),
  );
  expect(screen.getByLabelText('token')).toHaveValue('');
  fireEvent.click(screen.getByTestId('business-documents-settings-save'));
  await waitFor(() =>
    expect(setBusinessDocumentsSettings).toHaveBeenCalledWith(
      expect.objectContaining({ eva_api_token: '', clear_token: false }),
    ),
  );
  await waitFor(() =>
    expect(screen.getByLabelText('clearToken')).toBeEnabled(),
  );
  fireEvent.click(screen.getByLabelText('clearToken'));
  fireEvent.click(screen.getByTestId('business-documents-settings-save'));
  await waitFor(() =>
    expect(setBusinessDocumentsSettings).toHaveBeenLastCalledWith(
      expect.objectContaining({ eva_api_token: '', clear_token: true }),
    ),
  );
});

test('loads named spaces from draft settings without saving', async () => {
  jest.mocked(discoverBusinessDocumentsEvaSpaces).mockResolvedValue({
    data: {
      code: 0,
      data: { items: [{ id: 'CmfProject:other', name: 'Other space' }] },
    },
  } as never);
  mount();
  await waitFor(() =>
    expect(screen.getByLabelText('api_base_url')).toHaveValue(
      saved.api_base_url,
    ),
  );
  fireEvent.click(screen.getByRole('button', { name: 'loadSpaces' }));
  await screen.findByRole('option', { name: 'Other space' });
  fireEvent.change(screen.getByLabelText('spaces'), {
    target: { value: 'CmfProject:other' },
  });
  expect(screen.getByLabelText('evaSpace')).toHaveValue('CmfProject:other');
  expect(setBusinessDocumentsSettings).not.toHaveBeenCalled();
  fireEvent.click(screen.getByTestId('business-documents-settings-save'));
  await waitFor(() => expect(message.success).toHaveBeenCalled());
  expect(screen.queryByText('evaSpacesEmpty')).not.toBeInTheDocument();
});

test('shows a rejected save and retains entered settings', async () => {
  jest.mocked(setBusinessDocumentsSettings).mockResolvedValue({
    data: { code: 400, message: 'Invalid EVA URL' },
  } as never);
  mount();
  await waitFor(() =>
    expect(screen.getByLabelText('api_base_url')).toHaveValue(
      saved.api_base_url,
    ),
  );
  fireEvent.change(screen.getByLabelText('api_base_url'), {
    target: { value: 'invalid' },
  });
  fireEvent.click(screen.getByTestId('business-documents-settings-save'));
  expect(await screen.findByRole('alert')).toHaveTextContent('Invalid EVA URL');
  expect(screen.getByLabelText('api_base_url')).toHaveValue('invalid');
  expect(message.success).not.toHaveBeenCalled();
});

test('disconnects only the Documents connection', async () => {
  mount();
  await waitFor(() =>
    expect(screen.getByLabelText('api_base_url')).toHaveValue(
      saved.api_base_url,
    ),
  );
  fireEvent.click(screen.getByRole('button', { name: 'disconnect' }));
  await waitFor(() =>
    expect(setBusinessDocumentsSettings).toHaveBeenCalledWith(null),
  );
});
