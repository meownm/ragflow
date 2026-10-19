import { changeLanguageAsync } from '@/locales/config';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import Login from './index';

const mockRegister = jest.fn().mockResolvedValue(0);
const mockLogin = jest.fn();

beforeAll(() => {
  global.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
});

jest.mock('@/hooks/auth-hooks', () => ({
  useAuth: () => ({ isLogin: false }),
}));
jest.mock('@/hooks/use-login-request', () => ({
  useLogin: () => ({ login: mockLogin, loading: false }),
  useRegister: () => ({ register: mockRegister, loading: false }),
  useLoginChannels: () => ({ channels: [], loading: false }),
  useLoginWithChannel: () => ({ login: jest.fn(), loading: false }),
}));
jest.mock('@/hooks/use-system-request', () => ({
  SystemConfigKeys: { all: ['system'] },
  useSystemConfig: () => ({ config: { registerEnabled: 1 } }),
}));
jest.mock('@tanstack/react-query', () => ({
  useQueryClient: () => ({ invalidateQueries: jest.fn() }),
}));
jest.mock('react-router', () => ({ useNavigate: () => jest.fn() }));
jest.mock('@/utils', () => ({ rsaPsw: () => 'encrypted-test-password' }));
jest.mock('@/components/spotlight', () => () => null);
jest.mock('@/components/svg-icon', () => () => null);
jest.mock('./bg', () => ({ BgSvg: () => null }));
jest.mock('./card', () => {
  const React = jest.requireActual('react');
  return {
    __esModule: true,
    FlipFaceContext: React.createContext('front'),
    default: ({ children }: { children: React.ReactNode }) => children,
  };
});

test.each([
  ['ru', 'Введите никнейм'],
  ['en', 'Please input nickname'],
])(
  'registration validates a missing nickname in %s before sending a request',
  async (language, error) => {
    jest.clearAllMocks();
    await changeLanguageAsync(language);
    render(<Login />);
    fireEvent.click(screen.getByTestId('auth-toggle-register'));
    const nickname = await screen.findByTestId('auth-nickname');
    fireEvent.change(screen.getByTestId('auth-email'), {
      target: { value: 'localization@example.test' },
    });
    fireEvent.change(screen.getByTestId('auth-password'), {
      target: { value: 'Password123!' },
    });
    fireEvent.submit(screen.getByTestId('auth-form'));
    expect(await screen.findByText(error)).toBeInTheDocument();
    expect(screen.queryByText('nicknamePlaceholder')).not.toBeInTheDocument();
    expect(mockRegister).not.toHaveBeenCalled();
    expect(mockLogin).not.toHaveBeenCalled();

    fireEvent.change(nickname, { target: { value: 'Проверка' } });
    fireEvent.submit(screen.getByTestId('auth-form'));
    await waitFor(() =>
      expect(mockRegister).toHaveBeenCalledWith({
        nickname: 'Проверка',
        email: 'localization@example.test',
        password: 'encrypted-test-password',
      }),
    );
  },
);
