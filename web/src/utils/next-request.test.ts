import message from '@/components/ui/message';
import { redirectToLogin } from '@/utils/authorization-util';
import notification from '@/utils/notification';
import request from './next-request';
type RequestConfig = import('axios').AxiosRequestConfig;

jest.mock('@/utils/notification', () => ({
  __esModule: true,
  default: { error: jest.fn() },
}));
jest.mock('@/components/ui/message', () => ({
  __esModule: true,
  default: { error: jest.fn() },
}));
jest.mock('@/locales/config', () => ({
  __esModule: true,
  default: { t: (key: string) => key },
}));
jest.mock('@/utils/authorization-util', () => ({
  __esModule: true,
  default: { removeAll: jest.fn() },
  getAuthorization: () => '',
  redirectToLogin: jest.fn(),
}));
jest.mock('./telemetry', () => ({ getTelemetryHeaders: () => ({}) }));
jest.mock('./llm-util', () => ({ addTenantParams: (value: unknown) => value }));

beforeEach(() => jest.clearAllMocks());
const config = (code: number, skip = false) =>
  ({
    skipErrorNotification: skip,
    adapter: async (requestConfig) => ({
      config: requestConfig,
      data: { code, message: 'Failure' },
      headers: {},
      status: 200,
      statusText: 'OK',
    }),
  }) as RequestConfig;

test.each([100, 102])(
  'keeps envelope error %i in the requesting form without a duplicate toast',
  async (code) => {
    const response = await request.get(
      '/api/v1/users/me/eva-credentials',
      config(code, true),
    );
    expect(response.data.code).toBe(code);
    expect(notification.error).not.toHaveBeenCalled();
    expect(message.error).not.toHaveBeenCalled();
    expect(redirectToLogin).not.toHaveBeenCalled();
  },
);
test('continues notifying ordinary callers', async () => {
  await request.get('/api/v1/example', config(102));
  expect(notification.error).toHaveBeenCalledTimes(1);
});
test('still handles a RAGFlow session expiry even when contextual errors are requested', async () => {
  await request.get('/api/v1/example', config(401, true));
  expect(notification.error).toHaveBeenCalledTimes(1);
  expect(redirectToLogin).toHaveBeenCalledTimes(1);
});
