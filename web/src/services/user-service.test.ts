import { putEvaUserCredential } from '@/services/user-service';
import api from '@/utils/api';
import request from '@/utils/next-request';

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

jest.mock('@/utils/next-request', () => ({
  __esModule: true,
  default: { get: jest.fn(), put: jest.fn(), delete: jest.fn() },
}));

const mockedPut = jest.mocked(request.put);

beforeEach(() => jest.clearAllMocks());

test('sends the EVA token as JSON and returns its expiration metadata', async () => {
  const response = {
    data: {
      code: 0,
      data: { expires_at: 1_800_000_000_000, expiry_checked_at: 123 },
    },
  };
  mockedPut.mockResolvedValueOnce(response);
  expect(await putEvaUserCredential('connector-1', 'personal-token')).toEqual(
    response,
  );

  expect(mockedPut).toHaveBeenCalledWith(
    api.evaUserCredential('connector-1'),
    { eva_api_token: 'personal-token' },
    { skipErrorNotification: true },
  );
});
