import { TooltipProvider } from '@/components/ui/tooltip';
import { useFetchUserInfo } from '@/hooks/use-user-setting-request';
import { render, screen } from '@testing-library/react';
import { SettingForm } from './setting-form';

jest.mock('@/hooks/common-hooks', () => ({
  useTranslate: () => ({ t: (key: string) => key }),
}));
jest.mock('@/hooks/use-agent-request', () => {
  const data = {
    title: 'Shared agent',
    user_id: 'owner',
    avatar: '',
    description: '',
    permission: 'team',
  };
  return { useFetchAgent: () => ({ data }) };
});
jest.mock('@/hooks/use-user-setting-request', () => ({
  useFetchUserInfo: jest.fn(),
}));
jest.mock('@/components/avatar-upload', () => ({
  AvatarUpload: jest
    .requireActual<typeof import('react')>('react')
    .forwardRef(() => null),
}));

const originalResizeObserver = globalThis.ResizeObserver;
beforeAll(() => {
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
});
afterAll(() => {
  globalThis.ResizeObserver = originalResizeObserver;
});

it.each([
  ['owner', false],
  ['member', true],
])('sets title read-only for actor %s: %s', (id, readOnly) => {
  (useFetchUserInfo as jest.Mock).mockReturnValue({ data: { id } });
  render(
    <TooltipProvider>
      <SettingForm submit={jest.fn()} />
    </TooltipProvider>,
  );
  expect(screen.getByRole('textbox', { name: 'title' })).toHaveProperty(
    'readOnly',
    readOnly,
  );
});
