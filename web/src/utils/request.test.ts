type Interceptor = (
  response: Response,
  options: { responseType?: string },
) => Promise<Response>;

jest.mock('umi-request', () => ({
  extend: jest.fn(() => ({
    interceptors: {
      request: { use: jest.fn() },
      response: { use: jest.fn() },
    },
  })),
}));
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
jest.mock('./common-util', () => ({
  convertTheKeysOfTheObjectToSnake: (value: unknown) => value,
  isFormData: () => false,
}));
jest.mock('./telemetry', () => ({ getTelemetryHeaders: () => ({}) }));
jest.mock('./llm-util', () => ({ addTenantParams: (value: unknown) => value }));
jest.mock('./llm-cache', () => ({ setCachedLlmList: jest.fn() }));

let intercept: Interceptor;
let session: Pick<typeof import('./authorization-util').default, 'removeAll'>;
let redirect: typeof import('./authorization-util').redirectToLogin;
let notify: typeof import('./notification').default.error;

beforeEach(() => {
  jest.resetModules();
  jest.requireActual('./request');
  const { extend } = jest.requireMock('umi-request');
  intercept = (extend as jest.Mock).mock.results[0].value.interceptors.response
    .use.mock.calls[0][0];
  const auth = jest.requireMock('@/utils/authorization-util');
  session = auth.default;
  redirect = auth.redirectToLogin;
  notify = jest.requireMock('@/utils/notification').default.error;
});

function reply(code = 401, status = 200, malformed = false): Response {
  return {
    status,
    url: 'https://qa.invalid/api/v1/users/me',
    clone: () => ({
      json: async () => {
        if (malformed) throw new SyntaxError('invalid JSON');
        return { code, message: 'Session expired' };
      },
    }),
  } as unknown as Response;
}

test('an envelope 401 clears credentials and redirects exactly once', async () => {
  const response = reply();
  expect(await intercept(response, {})).toBe(response);
  expect(session.removeAll).toHaveBeenCalledTimes(1);
  expect(redirect).toHaveBeenCalledTimes(1);
  expect(notify).toHaveBeenCalledTimes(1);
});

test('concurrent expired responses share one session cleanup and redirect', async () => {
  await Promise.all([intercept(reply(), {}), intercept(reply(), {})]);
  expect(session.removeAll).toHaveBeenCalledTimes(1);
  expect(redirect).toHaveBeenCalledTimes(1);
  expect(notify).toHaveBeenCalledTimes(1);
});

test.each([false, true])(
  'an HTTP 401 redirects once even with malformed JSON: %s',
  async (malformed) => {
    const response = reply(401, 401, malformed);
    expect(await intercept(response, {})).toBe(response);
    expect(session.removeAll).toHaveBeenCalledTimes(1);
    expect(redirect).toHaveBeenCalledTimes(1);
    expect(notify).toHaveBeenCalledTimes(1);
  },
);

test('HTTP then envelope expiry cannot restart a pending redirect', async () => {
  await intercept(reply(401, 401), {});
  await intercept(reply(), {});
  expect(session.removeAll).toHaveBeenCalledTimes(1);
  expect(redirect).toHaveBeenCalledTimes(1);
});

test('HTTP expiry is handled for binary downloads', async () => {
  await intercept(reply(401, 401), { responseType: 'blob' });
  expect(session.removeAll).toHaveBeenCalledTimes(1);
  expect(redirect).toHaveBeenCalledTimes(1);
});

test.each([0, 100, 102])(
  'ordinary envelope code %i preserves the session',
  async (code) => {
    await intercept(reply(code), {});
    expect(session.removeAll).not.toHaveBeenCalled();
    expect(redirect).not.toHaveBeenCalled();
  },
);
