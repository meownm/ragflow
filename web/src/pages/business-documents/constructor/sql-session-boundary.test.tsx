import authorization, {
  AUTHORIZATION_CHANGED,
} from '@/utils/authorization-util';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { useEffect, useState } from 'react';
import { SqlSessionBoundary } from './sql-session-boundary';

function PrivateState() {
  const [draft, setDraft] = useState('new session');
  return (
    <input
      aria-label="SQL draft"
      value={draft}
      onChange={(event) => setDraft(event.target.value)}
    />
  );
}

function open() {
  return render(
    <SqlSessionBoundary>
      <PrivateState />
    </SqlSessionBoundary>,
  );
}

describe('SQL session state lifetime', () => {
  beforeEach(() => {
    localStorage.clear();
    authorization.setItems({
      Authorization: 'Bearer account-a',
      token: 'account-a',
      userInfo: JSON.stringify({ id: 'a', tenant_id: 't-a' }),
    });
  });

  it('clears SQL before the logout notification returns and does not resurrect it on back', () => {
    open();
    fireEvent.change(screen.getByLabelText('SQL draft'), {
      target: { value: 'private old SQL' },
    });
    act(() => {
      authorization.removeAll();
      expect(
        screen.queryByDisplayValue('private old SQL'),
      ).not.toBeInTheDocument();
      expect(screen.queryByLabelText('SQL draft')).not.toBeInTheDocument();
    });
    act(() =>
      window.dispatchEvent(
        new PageTransitionEvent('pageshow', { persisted: true }),
      ),
    );
    expect(screen.queryByLabelText('SQL draft')).not.toBeInTheDocument();
  });

  it.each(['Authorization', 'token', 'userInfo'])(
    'drops old state on a cross-tab %s change',
    (key) => {
      open();
      fireEvent.change(screen.getByLabelText('SQL draft'), {
        target: { value: 'private old SQL' },
      });
      act(() => {
        localStorage.setItem(
          key,
          key === 'userInfo'
            ? JSON.stringify({ id: 'b', tenant_id: 't-b' })
            : 'account-b',
        );
        window.dispatchEvent(new StorageEvent('storage', { key }));
        expect(
          screen.queryByDisplayValue('private old SQL'),
        ).not.toBeInTheDocument();
      });
      expect(screen.getByLabelText('SQL draft')).toHaveValue('new session');
    },
  );

  it('unmounts all SQL before pagehide returns and remounts fresh after BFcache restore', () => {
    let cleanups = 0;
    function Result() {
      useEffect(
        () => () => {
          cleanups += 1;
        },
        [],
      );
      return <PrivateState />;
    }
    render(
      <SqlSessionBoundary>
        <Result />
      </SqlSessionBoundary>,
    );
    fireEvent.change(screen.getByLabelText('SQL draft'), {
      target: { value: 'old result' },
    });
    act(() => {
      window.dispatchEvent(
        new PageTransitionEvent('pagehide', { persisted: true }),
      );
      expect(screen.queryByLabelText('SQL draft')).not.toBeInTheDocument();
      expect(cleanups).toBe(1);
    });
    act(() =>
      window.dispatchEvent(
        new PageTransitionEvent('pageshow', { persisted: true }),
      ),
    );
    expect(screen.getByLabelText('SQL draft')).toHaveValue('new session');
  });

  it('preserves a draft for unchanged session and profile display changes', () => {
    open();
    fireEvent.change(screen.getByLabelText('SQL draft'), {
      target: { value: 'keep draft' },
    });
    act(() => {
      authorization.setUserInfo({
        id: 'a',
        tenant_id: 't-a',
        nickname: 'New name',
      });
      window.dispatchEvent(new Event(AUTHORIZATION_CHANGED));
      window.dispatchEvent(new StorageEvent('storage', { key: 'lng' }));
    });
    expect(screen.getByLabelText('SQL draft')).toHaveValue('keep draft');
  });

  it('does not mount private SQL without authorization', () => {
    authorization.removeAll();
    open();
    expect(screen.queryByLabelText('SQL draft')).not.toBeInTheDocument();
  });

  it('rechecks identity on focus if a storage notification was missed', () => {
    open();
    fireEvent.change(screen.getByLabelText('SQL draft'), {
      target: { value: 'private old SQL' },
    });
    act(() => {
      localStorage.removeItem('Authorization');
      window.dispatchEvent(new Event('focus'));
    });
    expect(screen.queryByLabelText('SQL draft')).not.toBeInTheDocument();
  });
});
