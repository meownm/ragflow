import api from '@/utils/api';
import request from '@/utils/request';
import { listDataset, listDatasetChoices } from './knowledge-service';

jest.mock('@/utils/request', () => ({
  __esModule: true,
  default: { get: jest.fn().mockResolvedValue({ data: { data: [] } }) },
}));

it('uses a distinct read endpoint and preserves source search parameters', async () => {
  const params = { ext: { keywords: 'Assigned' } };
  await listDatasetChoices(params);
  expect(request.get).toHaveBeenLastCalledWith(api.datasetChoices, { params });
  expect(api.datasetChoices).toBe('/api/v1/datasets/choices');
  await listDataset(params);
  expect(request.get).toHaveBeenLastCalledWith(api.kbList, { params });
  expect(api.kbList).toBe('/api/v1/datasets');
});
