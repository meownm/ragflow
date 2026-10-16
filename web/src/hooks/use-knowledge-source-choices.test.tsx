import { useDisableDifferenceEmbeddingDataset } from '@/components/knowledge-base-item';
import { listDataset, listDatasetChoices } from '@/services/knowledge-service';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook, waitFor } from '@testing-library/react';
import { useFetchKnowledgeList } from './use-knowledge-request';

jest.mock('@/services/knowledge-service', () => ({
  __esModule: true,
  default: {},
  listDataset: jest.fn(),
  listDatasetChoices: jest.fn(),
}));
jest.mock('@/components/list-filter-bar/use-handle-filter-submit', () => ({}));
jest.mock('@/components/ui/message', () => ({ __esModule: true, default: {} }));
jest.mock('./logic-hooks', () => ({}));
jest.mock('./route-hook', () => ({}));
jest.mock('@/locales/config', () => ({ __esModule: true, default: {} }));
jest.mock('@/pages/agent/hooks/use-get-begin-query', () => ({}));
jest.mock('@/components/ragflow-avatar', () => ({}));
jest.mock('@/components/ragflow-form', () => ({}));
jest.mock('@/components/ui/multi-select', () => ({}));
jest.mock('react-hook-form', () => ({
  useFormContext: () => ({ control: {} }),
  useWatch: () => ['selected'],
}));

function setup() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  function Wrapper({ children }: { children?: import('react').ReactNode }) {
    return (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
  }
  return { client, wrapper: Wrapper };
}

beforeEach(() => {
  jest.clearAllMocks();
  jest.mocked(listDatasetChoices).mockResolvedValue({
    data: {
      data: [
        {
          id: 'selected',
          name: 'Assigned',
          chunk_count: 2,
          chunk_method: 'naive',
          embedding_model: 'same',
          nickname: 'Owner',
        },
        {
          id: 'empty',
          name: 'Empty',
          chunk_count: 0,
          chunk_method: 'naive',
          embedding_model: 'same',
        },
        {
          id: 'different',
          name: 'Different model',
          chunk_count: 1,
          chunk_method: 'naive',
          embedding_model: 'other',
        },
        {
          id: 'tags',
          name: 'Tags',
          chunk_count: 1,
          chunk_method: 'tag',
          embedding_model: 'same',
        },
      ],
    },
  } as never);
  jest.mocked(listDataset).mockResolvedValue({
    data: {
      data: [{ id: 'management', name: 'Management', chunk_count: 1 }],
    },
  } as never);
});

it('uses assigned source choices for the shared chat/search/agent picker', async () => {
  const { result } = renderHook(
    () => useDisableDifferenceEmbeddingDataset('kb_ids'),
    setup(),
  );
  await waitFor(() => expect(result.current.datasetOptions).toHaveLength(2));
  expect(listDataset).not.toHaveBeenCalled();
  expect(
    result.current.datasetOptions.map((x) => [x.value, x.disabled]),
  ).toEqual([
    ['selected', false],
    ['different', true],
  ]);
});

it('searches source labels and retains the selected label after filtering', async () => {
  const { result } = renderHook(
    () => useDisableDifferenceEmbeddingDataset('kb_ids'),
    setup(),
  );
  await waitFor(() => expect(result.current.datasetOptions).toHaveLength(2));
  jest
    .mocked(listDatasetChoices)
    .mockResolvedValue({ data: { data: [] } } as never);
  act(() => result.current.handleSearchChange('needle'));
  await waitFor(() =>
    expect(listDatasetChoices).toHaveBeenLastCalledWith({
      ext: { keywords: 'needle' },
    }),
  );
  await waitFor(() =>
    expect(result.current.datasetOptions.map((x) => x.value)).toEqual([
      'selected',
    ]),
  );
  expect(listDataset).not.toHaveBeenCalled();
});

it('keeps management and source caches separate', async () => {
  const { result } = renderHook(
    () => ({
      management: useFetchKnowledgeList(true, ''),
      sources: useFetchKnowledgeList(true, '', 'sources'),
    }),
    setup(),
  );
  await waitFor(() => expect(result.current.sources.list).toHaveLength(3));
  expect(result.current.management.list.map((x) => x.id)).toEqual([
    'management',
  ]);
  expect(result.current.sources.list.map((x) => x.id)).toEqual([
    'selected',
    'different',
    'tags',
  ]);
});
