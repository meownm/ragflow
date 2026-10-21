import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { flushSync } from 'react-dom';

export function resultHasExpired(deadline?: number | null) {
  return (
    deadline != null && (!Number.isFinite(deadline) || deadline <= Date.now())
  );
}

export function isExpiredResultError(error: unknown) {
  return (
    typeof error === 'object' &&
    error !== null &&
    'code' in error &&
    error.code === 'SQL_RESULT_EXPIRED'
  );
}

export function earliestResultDeadline(
  ...values: Array<number | null | undefined>
) {
  const deadlines = values.filter((value): value is number => value != null);
  return deadlines.length ? Math.min(...deadlines) : undefined;
}

/** Use the server's absolute deadline; activity must never extend retention. */
export function useSqlResultDeadline(
  source: string,
  deadline: number | undefined,
  onExpire: () => void,
) {
  const key = `${source}:${deadline}`;
  const currentKey = useRef(key);
  currentKey.current = key;
  const expiredSources = useRef(new Set<string>());
  const callback = useRef(onExpire);
  callback.current = onExpire;
  const [, invalidate] = useState(0);
  const isExpired = useCallback(
    () =>
      currentKey.current !== key ||
      expiredSources.current.has(source) ||
      resultHasExpired(deadline),
    [key, deadline, source],
  );
  const expire = useCallback(() => {
    if (currentKey.current !== key || expiredSources.current.has(source))
      return;
    expiredSources.current.add(source);
    invalidate((value) => value + 1);
    callback.current();
  }, [key, source]);

  useEffect(() => {
    if (deadline == null) return;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const check = (synchronous = false) => {
      clearTimeout(timer);
      if (resultHasExpired(deadline)) {
        if (synchronous) flushSync(expire);
        else expire();
      } else {
        // Browsers clamp delays above signed 32-bit milliseconds.
        timer = setTimeout(
          () => check(),
          Math.min(deadline - Date.now(), 2_147_483_647),
        );
      }
    };
    const resumed = () => check(true);
    check();
    window.addEventListener('focus', resumed);
    document.addEventListener('visibilitychange', resumed);
    return () => {
      clearTimeout(timer);
      window.removeEventListener('focus', resumed);
      document.removeEventListener('visibilitychange', resumed);
    };
  }, [deadline, expire]);

  const expired = isExpired();
  return useMemo(
    () => ({ expired, expire, isExpired }),
    [expired, expire, isExpired],
  );
}
