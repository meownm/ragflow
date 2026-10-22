import { render, screen } from '@testing-library/react';
import { createElement } from 'react';
import { MemoryRouter, Route, Routes } from 'react-router';
import SectionAccess from './section-access';

const mockConfig = jest.fn();
const mockPage = jest.fn(() => <div>Restricted content</div>);
jest.mock('@/hooks/use-system-request', () => ({
  useSystemConfig: () => mockConfig(),
}));
jest.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (_key: string, options: { defaultValue: string }) =>
      options.defaultValue,
  }),
}));

const open = () =>
  render(
    <MemoryRouter>
      <SectionAccess section="sql_projects">
        {createElement(mockPage)}
      </SectionAccess>
    </MemoryRouter>,
  );

beforeEach(() => mockPage.mockClear());

it.each([
  { loading: true },
  { loading: false },
  { loading: false, config: { visibleSections: [] } },
  { loading: false, config: { visibleSections: ['document_constructor'] } },
  {
    loading: false,
    config: { visibleSections: ['sql_projects'], accessibleSections: [] },
  },
])(
  'does not mount a page without a successful access decision: %j',
  (value) => {
    mockConfig.mockReturnValue(value);
    open();
    expect(mockPage).not.toHaveBeenCalled();
    expect(screen.queryByText('Restricted content')).not.toBeInTheDocument();
  },
);

it('keeps administrator access independent of the menu', () => {
  mockConfig.mockReturnValue({
    loading: false,
    config: { visibleSections: [], accessibleSections: ['sql_projects'] },
  });
  open();
  expect(screen.getByText('Restricted content')).toBeInTheDocument();
});

it('unmounts a page after permission revocation', () => {
  mockConfig.mockReturnValue({
    loading: false,
    config: { visibleSections: ['sql_projects'] },
  });
  const view = open();
  expect(screen.getByText('Restricted content')).toBeInTheDocument();
  mockConfig.mockReturnValue({
    loading: false,
    config: { visibleSections: [] },
  });
  view.rerender(
    <MemoryRouter>
      <SectionAccess section="sql_projects">
        <div>Restricted content</div>
      </SectionAccess>
    </MemoryRouter>,
  );
  expect(screen.queryByText('Restricted content')).not.toBeInTheDocument();
  expect(screen.getByTestId('section-access-denied')).toBeInTheDocument();
});

it('opens an allowed landing page when Home is closed', () => {
  mockConfig.mockReturnValue({
    loading: false,
    config: { visibleSections: ['chat'] },
  });
  render(
    <MemoryRouter initialEntries={['/']}>
      <Routes>
        <Route
          path="/"
          element={
            <SectionAccess section="home">
              <div>Home data</div>
            </SectionAccess>
          }
        />
        <Route path="/chats" element={<div>Allowed landing page</div>} />
      </Routes>
    </MemoryRouter>,
  );
  expect(screen.getByText('Allowed landing page')).toBeInTheDocument();
  expect(screen.queryByText('Home data')).not.toBeInTheDocument();
});
