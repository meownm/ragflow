import { listDataset, listDatasetChoices } from '@/services/knowledge-service';
import memoryService from '@/services/memory-service';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { RetrievalNode } from './retrieval-node';

jest.mock('@/services/knowledge-service', () => ({
  __esModule: true,
  default: {},
  listDataset: jest.fn(),
  listDatasetChoices: jest.fn(),
}));
jest.mock('@/components/list-filter-bar/use-handle-filter-submit', () => ({}));
jest.mock('@/components/ui/message', () => ({ __esModule: true, default: {} }));
jest.mock('@/hooks/logic-hooks', () => ({}));
jest.mock('@/hooks/route-hook', () => ({}));
jest.mock('@/locales/config', () => ({ __esModule: true, default: {} }));
jest.mock('@/services/memory-service', () => ({
  __esModule: true,
  default: { getMemoryList: jest.fn() },
}));
jest.mock('../../hooks/use-get-begin-query', () => ({
  useGetVariableLabelOrTypeByValue: () => ({ getLabel: () => '' }),
}));
jest.mock('../../constant', () => ({
  NodeHandleId: { Start: 'start' },
  RetrievalFrom: { Memory: 'memory' },
}));
jest.mock('@xyflow/react', () => ({ Position: { Right: 'right' } }));
jest.mock('@/components/ragflow-avatar', () => ({ RAGFlowAvatar: () => null }));
jest.mock('./handle', () => ({
  CommonHandle: () => null,
  LeftEndHandle: () => null,
}));
jest.mock('./node-header', () => ({ __esModule: true, default: () => null }));
jest.mock('./toolbar', () => ({
  ToolBar: ({ children }: { children: import('react').ReactNode }) => (
    <div>{children}</div>
  ),
}));
jest.mock('./node-wrapper', () => ({
  NodeWrapper: ({ children }: { children: import('react').ReactNode }) => (
    <div>{children}</div>
  ),
}));
jest.mock('./card', () => ({
  LabelCard: ({ children }: { children: import('react').ReactNode }) => (
    <div>{children}</div>
  ),
}));
jest.mock('@/components/collapse', () => ({
  NodeCollapsible: ({
    items,
    children,
  }: {
    items: string[];
    children: (id: string) => import('react').ReactNode;
  }) => <div>{items.map(children)}</div>,
}));

it('renders an assigned dataset label when management access is denied', async () => {
  jest.clearAllMocks();
  jest.mocked(listDataset).mockResolvedValue({
    data: { code: 103, data: false },
  } as never);
  jest.mocked(listDatasetChoices).mockResolvedValue({
    data: {
      data: [{ id: 'assigned', name: 'Назначенный источник', chunk_count: 1 }],
    },
  } as never);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const props = {
    id: 'retrieval',
    data: { form: { dataset_ids: ['assigned'] } },
  } as import('@xyflow/react').NodeProps<
    import('@/interfaces/database/agent').BaseNode
  >;
  const view = render(
    <QueryClientProvider client={client}>
      <RetrievalNode {...props} />
    </QueryClientProvider>,
  );
  expect(await screen.findByText('Назначенный источник')).toBeInTheDocument();
  expect(listDatasetChoices).toHaveBeenCalled();
  expect(listDataset).not.toHaveBeenCalled();
  expect(memoryService.getMemoryList).not.toHaveBeenCalled();
  view.unmount();
  client.clear();
});

it('loads and renders memory labels in memory retrieval mode', async () => {
  jest.clearAllMocks();
  jest.mocked(memoryService.getMemoryList).mockResolvedValue({
    data: {
      data: { memory_list: [{ id: 'memory-1', name: 'Memory source' }] },
    },
  } as never);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const props = {
    id: 'retrieval',
    data: {
      form: { retrieval_from: 'memory', memory_ids: ['memory-1'] },
    },
  } as import('@xyflow/react').NodeProps<
    import('@/interfaces/database/agent').BaseNode
  >;
  const view = render(
    <QueryClientProvider client={client}>
      <RetrievalNode {...props} />
    </QueryClientProvider>,
  );
  expect(await screen.findByText('Memory source')).toBeInTheDocument();
  expect(memoryService.getMemoryList).toHaveBeenCalledTimes(1);
  view.unmount();
  client.clear();
});
