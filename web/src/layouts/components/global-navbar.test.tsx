import { getBusinessDocumentCapabilities } from '@/services/business-document-service';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router';

import { DesktopNavbar } from './global-navbar';

const mockUseSystemConfig = jest.fn();
const mockUseFetchUserInfo = jest.fn();

jest.mock('@/hooks/use-system-request', () => ({
  useSystemConfig: () => mockUseSystemConfig(),
}));

jest.mock('@/hooks/use-user-setting-request', () => ({
  useFetchUserInfo: () => mockUseFetchUserInfo(),
}));

jest.mock('@/services/business-document-service', () => ({
  getBusinessDocumentCapabilities: jest.fn(),
}));

jest.mock('@/utils/css-support', () => ({
  supportsCssAnchor: false,
}));

jest.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, options?: { defaultValue?: string }) =>
      options?.defaultValue ?? key,
  }),
}));

const mockedGetBusinessDocumentCapabilities = jest.mocked(
  getBusinessDocumentCapabilities,
);

function renderNavbar(initialEntry = '/') {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[initialEntry]}>
        <DesktopNavbar />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('DesktopNavbar', () => {
  beforeEach(() => {
    mockedGetBusinessDocumentCapabilities.mockReset();
    mockUseFetchUserInfo.mockReturnValue({
      data: { id: 'user-1' },
      loading: false,
    });
  });

  it('shows only globally visible sections, including home', () => {
    mockUseSystemConfig.mockReturnValue({
      config: {
        registerEnabled: 1,
        visibleSections: ['chat', 'agent'],
      },
      loading: false,
    });

    renderNavbar();

    expect(screen.queryByTestId('nav-home')).not.toBeInTheDocument();
    expect(screen.getByTestId('nav-chat')).toBeInTheDocument();
    expect(screen.getByTestId('nav-agent')).toBeInTheDocument();
    expect(screen.queryByTestId('nav-search')).not.toBeInTheDocument();
    expect(screen.queryByTestId('nav-openmetadata')).not.toBeInTheDocument();
    expect(
      screen.queryByTestId('nav-business-documents'),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByTestId('nav-document-constructor'),
    ).not.toBeInTheDocument();
    expect(mockedGetBusinessDocumentCapabilities).not.toHaveBeenCalled();
  });

  it('shows the constructor as an active root item only after create is granted', async () => {
    mockUseSystemConfig.mockReturnValue({
      config: {
        registerEnabled: 1,
        visibleSections: ['business_documents'],
      },
      loading: false,
    });
    mockedGetBusinessDocumentCapabilities.mockResolvedValue({
      access_role: 'AUTHOR_CREATOR',
      capabilities: {
        read: true,
        create: true,
        edit_own: true,
        edit_all: false,
        delete: false,
        assign: false,
      },
    });

    renderNavbar('/document-constructor');

    expect(
      screen.queryByTestId('nav-document-constructor'),
    ).not.toBeInTheDocument();
    const constructorLink = await screen.findByTestId(
      'nav-document-constructor',
    );
    expect(constructorLink).toHaveAttribute('href', '/document-constructor');
    expect(constructorLink).toHaveAttribute('aria-current', 'page');
    expect(screen.getByTestId('nav-business-documents')).toBeInTheDocument();
  });

  it('keeps the constructor hidden when create is denied', async () => {
    mockUseSystemConfig.mockReturnValue({
      config: {
        registerEnabled: 1,
        visibleSections: ['business_documents'],
      },
      loading: false,
    });
    mockedGetBusinessDocumentCapabilities.mockResolvedValue({
      access_role: 'AUTHOR_EDITOR',
      capabilities: {
        read: true,
        create: false,
        edit_own: true,
        edit_all: false,
        delete: false,
        assign: false,
      },
    });

    renderNavbar();

    expect(await screen.findByTestId('nav-business-documents')).toBeVisible();
    expect(mockedGetBusinessDocumentCapabilities).toHaveBeenCalledTimes(1);
    expect(
      screen.queryByTestId('nav-document-constructor'),
    ).not.toBeInTheDocument();
  });
});
