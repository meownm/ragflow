import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { Applications } from './applications';

const mockConfig = jest.fn();
jest.mock('@/components/ui/segmented', () => ({ Segmented: () => null }));
jest.mock('@/hooks/use-system-request', () => ({
  useSystemConfig: () => mockConfig(),
}));
jest.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
jest.mock('@/routes', () => ({
  Routes: {
    Chats: '/chats',
    Searches: '/searches',
    Agents: '/agents',
    Memories: '/memories',
  },
}));
jest.mock('@/components/svg-icon', () => ({ HomeIcon: () => null }));
jest.mock('./chat-list', () => ({ ChatList: () => <div>Chat data</div> }));
jest.mock('./search-list', () => ({
  SearchList: () => <div>Search data</div>,
}));
jest.mock('./agent-list', () => ({ Agents: () => <div>Agent data</div> }));
jest.mock('./memory-list', () => ({
  MemoryList: () => <div>Memory data</div>,
}));
jest.mock('./application-card', () => ({ SeeAllAppCard: () => null }));
jest.mock('@/components/empty/empty', () => ({ EmptyAppCard: () => null }));

it('only mounts allowed application data on Home', () => {
  mockConfig.mockReturnValue({
    config: { visibleSections: ['home', 'search'] },
  });
  render(
    <MemoryRouter>
      <Applications />
    </MemoryRouter>,
  );
  expect(screen.getByText('Search data')).toBeInTheDocument();
  expect(screen.queryByText('Chat data')).not.toBeInTheDocument();
  expect(screen.queryByText('Agent data')).not.toBeInTheDocument();
});

it('does not query application sections when only Home is allowed', () => {
  mockConfig.mockReturnValue({ config: { visibleSections: ['home'] } });
  const { container } = render(
    <MemoryRouter>
      <Applications />
    </MemoryRouter>,
  );
  expect(container).toBeEmptyDOMElement();
});
