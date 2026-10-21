import { act, render, screen } from '@testing-library/react';
import {
  earliestResultDeadline,
  isExpiredResultError,
  resultHasExpired,
  useSqlResultDeadline,
} from './sql-result-deadline';

const start = 1_800_000_000_000;
type Lifetime = ReturnType<typeof useSqlResultDeadline>;
function Harness({
  source = 'a',
  deadline,
  expired = () => {},
  capture = () => {},
}: {
  source?: string;
  deadline?: number;
  expired?: () => void;
  capture?: (value: Lifetime) => void;
}) {
  const lifetime = useSqlResultDeadline(source, deadline, expired);
  capture(lifetime);
  return lifetime.expired ? <p>expired</p> : <p>private result</p>;
}

describe('absolute result lifetime', () => {
  beforeEach(() => {
    jest.useFakeTimers();
    jest.setSystemTime(start);
  });
  afterEach(() => jest.useRealTimers());

  it('removes visible data at the exclusive deadline and notifies only once', () => {
    const expired = jest.fn();
    render(<Harness deadline={start + 1000} expired={expired} />);
    act(() => jest.advanceTimersByTime(999));
    expect(screen.getByText('private result')).toBeVisible();
    act(() => jest.advanceTimersByTime(1));
    expect(screen.queryByText('private result')).not.toBeInTheDocument();
    act(() => window.dispatchEvent(new Event('focus')));
    expect(expired).toHaveBeenCalledTimes(1);
  });

  it('handles thirty days without overflowing the browser timer', () => {
    const duration = 30 * 24 * 60 * 60 * 1000;
    render(<Harness deadline={start + duration} />);
    act(() => jest.advanceTimersByTime(10));
    expect(screen.getByText('private result')).toBeVisible();
    act(() => jest.advanceTimersByTime(duration - 10));
    expect(screen.getByText('expired')).toBeVisible();
  });

  it.each(['focus', 'visibilitychange'])(
    'clears paused data before %s returns',
    (event) => {
      render(<Harness deadline={start + 1000} />);
      jest.setSystemTime(start + 1000);
      act(() => {
        (event === 'focus' ? window : document).dispatchEvent(new Event(event));
        expect(screen.queryByText('private result')).not.toBeInTheDocument();
      });
    },
  );

  it('rejects an old response without expiring a newly selected source', () => {
    let current!: Lifetime;
    const expired = jest.fn();
    const capture = (value: Lifetime) => {
      current = value;
    };
    const rendered = render(
      <Harness deadline={start + 1000} capture={capture} />,
    );
    const old = current;
    rendered.rerender(
      <Harness
        source="b"
        deadline={start + 2000}
        expired={expired}
        capture={capture}
      />,
    );
    expect(old.isExpired()).toBe(true);
    act(() => {
      old.expire();
      jest.advanceTimersByTime(1000);
    });
    expect(screen.getByText('private result')).toBeVisible();
    expect(expired).not.toHaveBeenCalled();
    act(() => jest.advanceTimersByTime(1000));
    expect(expired).toHaveBeenCalledTimes(1);
  });

  it('honors server expiry even if a later snapshot advertises another deadline', () => {
    let current!: Lifetime;
    const capture = (value: Lifetime) => {
      current = value;
    };
    const rendered = render(
      <Harness deadline={start + 1000} capture={capture} />,
    );
    act(() => current.expire());
    rendered.rerender(<Harness deadline={start + 10_000} capture={capture} />);
    expect(screen.queryByText('private result')).not.toBeInTheDocument();
  });

  it('removes timers/listeners on unmount', () => {
    const expired = jest.fn();
    const rendered = render(
      <Harness deadline={start + 1000} expired={expired} />,
    );
    rendered.unmount();
    act(() => {
      jest.advanceTimersByTime(2000);
      window.dispatchEvent(new Event('focus'));
    });
    expect(expired).not.toHaveBeenCalled();
  });

  it('uses the earliest server deadline and rejects invalid deadlines', () => {
    expect(earliestResultDeadline(undefined, start + 20, start + 10)).toBe(
      start + 10,
    );
    expect(earliestResultDeadline(undefined, null)).toBeUndefined();
    expect(resultHasExpired(NaN)).toBe(true);
    expect(resultHasExpired(start)).toBe(true);
    expect(resultHasExpired(start + 1)).toBe(false);
    expect(isExpiredResultError({ code: 'SQL_RESULT_EXPIRED' })).toBe(true);
    expect(isExpiredResultError({ code: 'SQL_TIMEOUT' })).toBe(false);
  });
});
