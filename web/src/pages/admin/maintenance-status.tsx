import { Button } from '@/components/ui/button';
import { getMaintenanceStatus } from '@/services/admin-service';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';

export default function MaintenanceStatus() {
  const { t, i18n } = useTranslation();
  const label = (key: string) => t(`admin.maintenancePage.${key}`);
  const { data, error, isLoading, refetch, isFetching } = useQuery({
    queryKey: ['admin', 'maintenance'],
    queryFn: async () => {
      const response = (await getMaintenanceStatus()).data;
      if (response.code !== 0) throw new Error(response.message);
      return response.data;
    },
    refetchInterval: 60000,
    retry: false,
  });
  const date = (value?: string | null) => {
    if (!value) return label('never');
    const timestamp = Date.parse(value);
    return Number.isNaN(timestamp)
      ? value
      : new Date(timestamp).toLocaleString(i18n.resolvedLanguage);
  };
  const bytes = (value?: number) =>
    value === undefined
      ? '—'
      : `${(value / 1024 ** 2).toLocaleString(i18n.resolvedLanguage, { maximumFractionDigits: 1 })} MiB`;
  const stale =
    data?.generated_at &&
    (!Number.isFinite(Date.parse(data.generated_at)) ||
      Date.now() - Date.parse(data.generated_at) > 2 * 3600000);
  const last = data?.elastic?.last_run;

  return (
    <section
      aria-label={label('title')}
      className="mb-6 border-b border-border-button pb-6 space-y-3"
    >
      <div className="flex items-center justify-between gap-4">
        <h2 className="font-semibold">{label('title')}</h2>
        <Button
          variant="outline"
          size="sm"
          disabled={isFetching}
          onClick={() => void refetch()}
        >
          {label('refresh')}
        </Button>
      </div>
      {error || data?.error ? (
        <p role="alert" className="text-state-error">
          {label('error')}
        </p>
      ) : isLoading ? (
        <p role="status">{label('loading')}</p>
      ) : !data?.configured ? (
        <p>{label('notConfigured')}</p>
      ) : (
        <>
          {stale && (
            <p role="alert" className="text-state-warning">
              {label('stale')}
            </p>
          )}
          <dl className="grid grid-cols-[minmax(12rem,1fr)_3fr] gap-x-6 gap-y-2 text-sm">
            <dt>{label('logs')}</dt>
            <dd>
              {t('admin.maintenancePage.retention', {
                days: data.logs?.retention_days,
              })}
            </dd>
            <dt>{label('scope')}</dt>
            <dd>{label('logScope')}</dd>
            <dt>{label('disk')}</dt>
            <dd>
              {bytes(data.disk?.free_bytes)} / {bytes(data.disk?.total_bytes)} (
              {data.disk?.used_percent}%)
            </dd>
            <dt>Elasticsearch</dt>
            <dd>
              {bytes(data.elastic?.store_bytes)} · {label('deleted')}:{' '}
              {data.elastic?.deleted_documents ?? '—'}
            </dd>
            <dt>{label('policy')}</dt>
            <dd>{label('policyDescription')}</dd>
            <dt>{label('nextRun')}</dt>
            <dd>{data.next_run?.elastic || label('never')}</dd>
            <dt>{label('lastRun')}</dt>
            <dd>
              {date(last?.finished_at)} ·{' '}
              {last ? label(last.outcome) : label('never')}
            </dd>
            {last?.indices.map((item) => (
              <div className="contents" key={item.index}>
                <dt className="break-all text-text-secondary">{item.index}</dt>
                <dd>{label(item.outcome)}</dd>
              </div>
            ))}
            <dt>{label('updated')}</dt>
            <dd>{date(data.generated_at)}</dd>
          </dl>
        </>
      )}
    </section>
  );
}
