import { Authorization, Token, UserInfo } from '@/constants/authorization';
import authorization, {
  AUTHORIZATION_CHANGED,
  getAuthorization,
} from '@/utils/authorization-util';
import {
  Fragment,
  type ReactNode,
  useLayoutEffect,
  useRef,
  useState,
} from 'react';
import { flushSync } from 'react-dom';

function currentSession() {
  let identity: unknown = null;
  try {
    const user = authorization.getUserInfoObject();
    identity = [user?.id, user?.tenant_id];
  } catch {
    // Invalid persisted identity cannot preserve the previous SQL state.
  }
  return JSON.stringify([
    getAuthorization(),
    authorization.getToken(),
    identity,
  ]);
}

/** Drop the entire SQL state before the browser can preserve the old session. */
export function SqlSessionBoundary({ children }: { children: ReactNode }) {
  const session = useRef(currentSession());
  const [epoch, setEpoch] = useState(0);
  const [visible, setVisible] = useState(() => Boolean(getAuthorization()));

  useLayoutEffect(() => {
    const changed = () => {
      const next = currentSession();
      if (next === session.current) return;
      session.current = next;
      flushSync(() => {
        setEpoch((value) => value + 1);
        setVisible(Boolean(getAuthorization()));
      });
    };
    const storageChanged = (event: StorageEvent) => {
      if (
        event.key === null ||
        [Authorization, Token, UserInfo].includes(event.key)
      )
        changed();
    };
    const hide = () => flushSync(() => setVisible(false));
    const restore = (event: PageTransitionEvent) => {
      if (!event.persisted) return;
      session.current = currentSession();
      flushSync(() => {
        setEpoch((value) => value + 1);
        setVisible(Boolean(getAuthorization()));
      });
    };
    window.addEventListener(AUTHORIZATION_CHANGED, changed);
    window.addEventListener('storage', storageChanged);
    window.addEventListener('focus', changed);
    window.addEventListener('pagehide', hide);
    window.addEventListener('pageshow', restore);
    return () => {
      window.removeEventListener(AUTHORIZATION_CHANGED, changed);
      window.removeEventListener('storage', storageChanged);
      window.removeEventListener('focus', changed);
      window.removeEventListener('pagehide', hide);
      window.removeEventListener('pageshow', restore);
    };
  }, []);

  return visible ? <Fragment key={epoch}>{children}</Fragment> : null;
}
