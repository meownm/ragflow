import { putEvaUserCredential } from '@/services/user-service';
import api from '@/utils/api';
import request from '@/utils/request';

jest.mock('@/utils/register-server', () => ({
  __esModule: true,
  default: jest.fn(() => ({})),
}));

jest.mock('@/utils/request', () => ({
  __esModule: true,
  default: {
    delete: jest.fn(),
    get: jest.fn(),
    put: jest.fn(),
  },
  post: jest.fn(),
}));

const mockedPut = jest.mocked(request.put);

beforeEach(() => jest.clearAllMocks());

test('sends the EVA token in the umi-request JSON body', () => {
  putEvaUserCredential('connector-1', 'personal-token');

  expect(mockedPut).toHaveBeenCalledWith(api.evaUserCredential('connector-1'), {
    data: { eva_api_token: 'personal-token' },
  });
});
