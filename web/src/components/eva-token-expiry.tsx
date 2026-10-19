import { useTranslate } from '@/hooks/common-hooks';
import { useEffect, useState } from 'react';

type EvaTokenExpiry = {
  expires_at?: number | null;
  expiry_checked_at?: number | null;
};

function evaTokenExpiryWarning(
  expiresAt: number | null | undefined,
  now: number,
) {
  if (expiresAt == null || !Number.isFinite(expiresAt)) return undefined;
  const remaining = expiresAt - now;
  if (remaining <= 0) return 'expired';
  if (remaining <= 86_400_000) return 'day';
  if (remaining <= 7 * 86_400_000) return 'week';
  return undefined;
}

export function useEvaTokenExpiryWarning(expiresAt?: number | null) {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    setNow(Date.now());
    if (expiresAt == null) return;
    const timer = window.setInterval(() => setNow(Date.now()), 30_000);
    return () => window.clearInterval(timer);
  }, [expiresAt]);
  return evaTokenExpiryWarning(expiresAt, now);
}

export function EvaTokenExpiryNotice({
  expiry,
  showStatus = false,
}: {
  expiry: EvaTokenExpiry;
  showStatus?: boolean;
}) {
  const { t } = useTranslate('setting');
  const warning = useEvaTokenExpiryWarning(expiry.expires_at);
  const date =
    expiry.expires_at == null
      ? ''
      : new Date(expiry.expires_at).toLocaleString();
  return (
    <>
      {showStatus && (
        <p className="text-xs text-text-secondary">
          {expiry.expires_at != null
            ? t('evaTokenExpiresAt', { date })
            : t(
                expiry.expiry_checked_at != null
                  ? 'evaTokenUnlimited'
                  : 'evaTokenExpiryUnknown',
              )}
        </p>
      )}
      {warning && (
        <p
          role="alert"
          className={`text-xs ${warning === 'week' ? 'text-state-warning' : 'text-state-error'}`}
          data-testid="eva-token-expiry-warning"
        >
          {t(
            warning === 'expired'
              ? 'evaTokenExpired'
              : warning === 'day'
                ? 'evaTokenExpiresDay'
                : 'evaTokenExpiresWeek',
            { date },
          )}
        </p>
      )}
    </>
  );
}
