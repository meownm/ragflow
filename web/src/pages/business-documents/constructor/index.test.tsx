import { listBusinessDocuments } from '@/services/business-document-service';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import DocumentConstructorPage from '.';
import { createDocumentConstructorStorageKey } from './draft-storage';
import { createSqlQueryTemplate } from './sql-query-template';

jest.mock('@/hooks/use-user-setting-request', () => ({
  useFetchUserInfo: () => ({ data: { id: 'user-1' }, loading: false }),
  useFetchTenantInfo: () => ({
    data: { tenant_id: 'tenant-1' },
    loading: false,
  }),
}));
jest.mock('@/services/business-document-service', () => ({
  listBusinessDocuments: jest.fn(),
}));
jest.mock('./execution-registry-dialog', () => ({
  ExecutionRegistryDialog: () => (
    <button data-testid="open-sql-execution-registry">Источники SQL</button>
  ),
}));

const mockedListBusinessDocuments = listBusinessDocuments as jest.Mock;

const storageKey = createDocumentConstructorStorageKey({
  userId: 'user-1',
  tenantId: 'tenant-1',
});

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <DocumentConstructorPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('DocumentConstructorPage', () => {
  beforeEach(() => {
    window.localStorage.clear();
    mockedListBusinessDocuments.mockReset();
    mockedListBusinessDocuments.mockResolvedValue({
      items: [],
      total: 0,
      page: 1,
      page_size: 20,
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
  });

  it('builds a section with requirements and renders a live preview', async () => {
    renderPage();

    expect(
      await screen.findByText('Конструктор документов'),
    ).toBeInTheDocument();
    expect(
      screen.getAllByTestId('document-constructor-section-row'),
    ).toHaveLength(createSqlQueryTemplate().sections.length);
    expect(
      screen.getByText('Исходная формулировка запроса целиком'),
    ).toBeInTheDocument();
    expect(screen.getByText('Итоговый SQL-запрос целиком')).toBeInTheDocument();
    expect(
      screen.getByRole('link', { name: 'Вернуться к документам' }),
    ).toHaveAttribute('href', '/business-documents');

    fireEvent.click(screen.getByRole('button', { name: 'Раздел' }));
    fireEvent.change(
      screen.getByRole('textbox', { name: 'Название раздела' }),
      { target: { value: 'Риски и ограничения' } },
    );
    fireEvent.click(screen.getByRole('button', { name: 'Добавить' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Требование 1' }), {
      target: { value: 'Для каждого риска указать владельца и план реакции' },
    });
    fireEvent.click(screen.getByRole('tab', { name: 'Предпросмотр' }));

    expect(
      screen.getByTestId('document-constructor-preview'),
    ).toHaveTextContent('Риски и ограничения');
    expect(
      screen.getByTestId('document-constructor-preview'),
    ).toHaveTextContent('Для каждого риска указать владельца и план реакции');

    await waitFor(() =>
      expect(window.localStorage.getItem(storageKey)).toContain(
        'Риски и ограничения',
      ),
    );
  });

  it('shows validation feedback and keeps JSON export disabled by validation', async () => {
    renderPage();

    await screen.findByText('Конструктор документов');
    fireEvent.change(
      screen.getByRole('textbox', { name: 'Технический код шаблона' }),
      { target: { value: '!' } },
    );
    fireEvent.click(screen.getByTestId('document-constructor-export'));

    expect(screen.getByRole('alert')).toHaveTextContent(
      'Исправьте шаблон перед экспортом',
    );
  });

  it('fails closed when the server does not grant create capability', async () => {
    mockedListBusinessDocuments.mockResolvedValueOnce({
      items: [],
      total: 0,
      page: 1,
      page_size: 20,
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

    renderPage();

    expect(
      await screen.findByTestId('document-constructor-access-denied'),
    ).toHaveTextContent('только пользователям с правом создания');
    expect(
      screen.queryByText('Конструктор документов'),
    ).not.toBeInTheDocument();
    expect(window.localStorage.getItem(storageKey)).toBeNull();
  });

  it('shows central SQL registry management only to an administrator', async () => {
    mockedListBusinessDocuments.mockResolvedValueOnce({
      items: [],
      total: 0,
      page: 1,
      page_size: 20,
      access_role: 'ADMIN',
      capabilities: {
        read: true,
        create: true,
        edit_own: true,
        edit_all: true,
        delete: true,
        assign: true,
      },
    });

    renderPage();

    expect(
      await screen.findByTestId('open-sql-execution-registry'),
    ).toBeInTheDocument();
  });
});
