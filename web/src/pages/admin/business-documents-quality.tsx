import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { useQuery } from '@tanstack/react-query';
import { ExternalLink, Gauge, LoaderCircle } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { listAuditEvents } from '@/services/admin-service';

const dashboardUid = 'business-documents-ai-quality';

function qualityDashboardUrl(grafanaUrl: string) {
  const baseUrl = grafanaUrl.replace(/\/$/, '');
  return `${baseUrl}/d/${dashboardUid}/business-documents-ai-quality?orgId=1&refresh=30s&kiosk`;
}

export default function AdminBusinessDocumentsQuality() {
  const { t } = useTranslation();
  const label = (key: string) => t(`admin.businessDocumentsQualityPage.${key}`);
  const [frameLoaded, setFrameLoaded] = useState(false);
  const {
    data: observability,
    isLoading,
    error,
  } = useQuery({
    queryKey: ['admin', 'business-documents-quality', 'observability'],
    queryFn: async () => {
      const { data: response } = await listAuditEvents({
        page: 1,
        page_size: 1,
      });
      if (response.code !== 0) throw new Error(response.message);
      return response.data.observability;
    },
    retry: false,
  });
  const dashboardUrl = useMemo(
    () =>
      observability?.enabled && observability.grafana_url
        ? qualityDashboardUrl(observability.grafana_url)
        : '',
    [observability],
  );

  useEffect(() => setFrameLoaded(false), [dashboardUrl]);

  return (
    <section
      className="flex h-full min-h-0 flex-col overflow-hidden rounded-xl border border-border-button bg-transparent"
      data-testid="business-documents-quality-admin"
    >
      <header className="flex flex-wrap items-center justify-between gap-4 border-b border-border-button px-6 py-5">
        <div className="min-w-0">
          <div className="flex items-center gap-3">
            <h1 className="text-xl font-semibold text-text-primary">
              {label('title')}
            </h1>
            {!isLoading && !error && (
              <span className="inline-flex items-center gap-2 text-xs text-text-secondary">
                <span
                  className={cn(
                    'size-2 rounded-full',
                    dashboardUrl ? 'bg-state-success' : 'bg-text-disabled',
                  )}
                />
                {label(dashboardUrl ? 'connected' : 'unavailable')}
              </span>
            )}
          </div>
          <p className="mt-1 max-w-3xl text-sm text-text-secondary">
            {label('description')}
          </p>
        </div>
        {dashboardUrl && (
          <Button variant="outline" size="sm" asChild>
            <a href={dashboardUrl} target="_blank" rel="noreferrer">
              {label('openGrafana')}
              <ExternalLink className="ml-2 size-3.5" />
            </a>
          </Button>
        )}
      </header>

      <div className="relative min-h-0 flex-1 bg-bg-card">
        {isLoading && (
          <div className="absolute inset-0 flex items-center justify-center gap-3 text-sm text-text-secondary">
            <LoaderCircle className="size-5 animate-spin" />
            {label('loading')}
          </div>
        )}

        {error && (
          <div
            className="absolute inset-0 flex flex-col items-center justify-center gap-3 px-6 text-center"
            role="alert"
          >
            <Gauge className="size-8 text-state-error" />
            <p className="text-sm text-state-error">{label('loadError')}</p>
          </div>
        )}

        {!isLoading && !error && !dashboardUrl && (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 px-6 text-center">
            <Gauge className="size-8 text-text-secondary" />
            <div>
              <p className="font-medium text-text-primary">
                {label('unavailable')}
              </p>
              <p className="mt-1 text-sm text-text-secondary">
                {label('unavailableDescription')}
              </p>
            </div>
          </div>
        )}

        {dashboardUrl && (
          <>
            {!frameLoaded && (
              <div className="absolute inset-0 z-10 flex items-center justify-center gap-3 bg-bg-card text-sm text-text-secondary">
                <LoaderCircle className="size-5 animate-spin" />
                {label('loading')}
              </div>
            )}
            <iframe
              className={cn(
                'h-full w-full border-0 transition-opacity duration-300',
                frameLoaded ? 'opacity-100' : 'opacity-0',
              )}
              data-testid="business-documents-quality-frame"
              src={dashboardUrl}
              title={label('frameTitle')}
              onLoad={() => setFrameLoaded(true)}
            />
          </>
        )}
      </div>
    </section>
  );
}
