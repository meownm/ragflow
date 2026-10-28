import message from '@/components/ui/message';
import { listUsers } from './admin-service';

let mockFailure: Error;
jest.mock('axios', () => {
  const actual = jest.requireActual('axios');
  return {
    ...actual,
    __esModule: true,
    default: {
      ...actual.default,
      create: (config: object) =>
        actual.default.create({
          ...config,
          adapter: async () => {
            throw mockFailure;
          },
        }),
    },
  };
});

jest.mock('@/components/ui/message', () => ({
  __esModule: true,
  default: { error: jest.fn() },
}));
jest.mock('@/utils/simple-history-util', () => ({
  history: { push: jest.fn() },
}));
jest.mock('@/utils/authorization-util', () => ({
  __esModule: true,
  default: { removeAll: jest.fn() },
  getAuthorization: () => 'qa-token',
}));
jest.mock('@/utils/telemetry', () => ({ getTelemetryHeaders: () => ({}) }));
jest.mock('@/utils/request', () => ({ ResultCode: {}, RetcodeMessage: {} }));
jest.mock('@/routes', () => ({ Routes: { Admin: '/admin' } }));
jest.mock('@/locales/config', () => ({
  __esModule: true,
  default: { t: (key: string) => key },
}));

test.each(['Network Error', 'timeout of 300000ms exceeded', 'canceled'])(
  'preserves an Axios failure without a response: %s',
  async (text) => {
    mockFailure = new Error(text);
    await expect(listUsers()).rejects.toBe(mockFailure);
    expect(message.error).toHaveBeenCalledWith({
      description: 'message.networkAnomalyDescription',
      message: 'message.networkAnomaly',
    });
  },
);
