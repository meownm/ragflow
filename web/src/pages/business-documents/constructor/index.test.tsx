import { getBusinessDocumentCapabilities } from '@/services/business-document-service';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen, within } from '@testing-library/react';
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
  getBusinessDocumentCapabilities: jest.fn(),
}));
const mockedGetBusinessDocumentCapabilities =
  getBusinessDocumentCapabilities as jest.Mock;

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
      <MemoryRouter initialEntries={['/document-constructor']}>
        <DocumentConstructorPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('DocumentConstructorPage', () => {
  beforeEach(() => {
    window.localStorage.clear();
    mockedGetBusinessDocumentCapabilities.mockReset();
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
  });

  it('builds a section with requirements and renders a live preview', async () => {
    jest.useFakeTimers();
    const rendered = renderPage();
    try {
      expect(
        await screen.findByText('Конструктор документов'),
      ).toBeInTheDocument();
      expect(
        screen.queryByTestId('sql-agent-workbench'),
      ).not.toBeInTheDocument();
      const structure = within(screen.getByLabelText('Структура документа'));
      expect(
        structure.getAllByTestId('document-constructor-section-row'),
      ).toHaveLength(createSqlQueryTemplate().sections.length);
      expect(
        structure.getByText('Исходная формулировка запроса целиком'),
      ).toBeInTheDocument();
      expect(
        structure.getByText('Итоговый SQL-запрос целиком'),
      ).toBeInTheDocument();
      fireEvent.click(structure.getByText('Раздел', { selector: 'button' }));
      const inspector = within(screen.getByLabelText('Настройки раздела'));
      fireEvent.change(inspector.getByLabelText('Название раздела'), {
        target: { value: 'Риски и ограничения' },
      });
      fireEvent.click(inspector.getByRole('button', { name: 'Добавить' }));
      fireEvent.change(inspector.getByLabelText('Требование 1'), {
        target: { value: 'Для каждого риска указать владельца и план реакции' },
      });
      fireEvent.click(
        within(structure.getByLabelText('Режим конструктора')).getByRole(
          'tab',
          {
            name: 'Предпросмотр',
          },
        ),
      );

      expect(
        structure.getByTestId('document-constructor-preview'),
      ).toHaveTextContent('Риски и ограничения');
      expect(
        structure.getByTestId('document-constructor-preview'),
      ).toHaveTextContent('Для каждого риска указать владельца и план реакции');

      act(() => jest.advanceTimersByTime(299));
      expect(window.localStorage.getItem(storageKey) || '').not.toContain(
        'Риски и ограничения',
      );
      act(() => jest.advanceTimersByTime(1));
      expect(window.localStorage.getItem(storageKey)).toContain(
        'Риски и ограничения',
      );
    } finally {
      rendered.unmount();
      jest.clearAllTimers();
      jest.useRealTimers();
    }
  });

  it('shows validation feedback and keeps JSON export disabled by validation', async () => {
    renderPage();

    await screen.findByText('Конструктор документов');
    fireEvent.change(
      within(screen.getByLabelText('Параметры шаблона')).getByLabelText(
        'Технический код шаблона',
      ),
      { target: { value: '!' } },
    );
    fireEvent.click(screen.getByTestId('document-constructor-export'));

    expect(screen.getByRole('alert')).toHaveTextContent(
      'Исправьте шаблон перед экспортом',
    );
  });

  it('fails closed when the server does not grant create capability', async () => {
    mockedGetBusinessDocumentCapabilities.mockResolvedValueOnce({
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
    mockedGetBusinessDocumentCapabilities.mockResolvedValueOnce({
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

    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/sql-projects']}>
          <DocumentConstructorPage surface="sql" />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(await screen.findByText('SQL-проекты')).toBeInTheDocument();
    expect(
      screen.queryByTestId('document-constructor-structure'),
    ).not.toBeInTheDocument();
    expect(
      await screen.findByTestId('open-sql-execution-registry'),
    ).toHaveAttribute('href', '/sql-sources');
  });
});
