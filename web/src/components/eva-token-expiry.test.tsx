import { act, render, screen } from '@testing-library/react';
import { EvaTokenExpiryNotice } from './eva-token-expiry';

jest.mock('@/hooks/common-hooks', () => ({
  useTranslate: () => ({ t: (key: string) => key }),
}));

const day = 86_400_000;
const now = Date.UTC(2026, 9, 18, 12);

afterEach(() => jest.useRealTimers());

test.each([
  [7 * day + 1, undefined],
  [7 * day, 'week'],
  [day + 1, 'week'],
  [day, 'day'],
  [1, 'day'],
  [0, 'expired'],
  [-1, 'expired'],
])(
  'classifies the exact expiration boundary at %s ms remaining',
  (remaining, expected) => {
    jest.useFakeTimers().setSystemTime(now);
    render(<EvaTokenExpiryNotice expiry={{ expires_at: now + remaining }} />);
    if (expected) {
      const keys = {
        week: 'evaTokenExpiresWeek',
        day: 'evaTokenExpiresDay',
        expired: 'evaTokenExpired',
      };
      expect(screen.getByRole('alert')).toHaveTextContent(
        keys[expected as keyof typeof keys],
      );
    } else {
      expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    }
  },
);

test.each([null, undefined, NaN])(
  'does not warn without a known expiration: %s',
  (expiry) => {
    render(<EvaTokenExpiryNotice expiry={{ expires_at: expiry }} />);
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  },
);

test('updates both warnings and expiration while the page remains open', () => {
  jest.useFakeTimers().setSystemTime(now);
  const expiry = { expires_at: now + 7 * day + 30_000, expiry_checked_at: now };
  const view = render(<EvaTokenExpiryNotice expiry={expiry} showStatus />);
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  act(() => jest.advanceTimersByTime(30_000));
  expect(screen.getByRole('alert')).toHaveTextContent('evaTokenExpiresWeek');
  act(() => jest.advanceTimersByTime(6 * day));
  expect(screen.getByRole('alert')).toHaveTextContent('evaTokenExpiresDay');
  act(() => jest.advanceTimersByTime(day));
  expect(screen.getByRole('alert')).toHaveTextContent('evaTokenExpired');
  view.unmount();
  jest.useRealTimers();
});

test('distinguishes explicitly unlimited tokens from unknown expiration', () => {
  const view = render(<EvaTokenExpiryNotice expiry={{}} showStatus />);
  expect(screen.getByText('evaTokenExpiryUnknown')).toBeVisible();
  view.rerender(
    <EvaTokenExpiryNotice
      expiry={{ expires_at: null, expiry_checked_at: now }}
      showStatus
    />,
  );
  expect(screen.getByText('evaTokenUnlimited')).toBeVisible();
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});
