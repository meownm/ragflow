import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { useQuery } from '@tanstack/react-query';
import {
  AlertTriangle,
  ChevronRight,
  CircleX,
  Clock3,
  ExternalLink,
  RefreshCw,
  SearchX,
} from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { SearchInput } from '@/components/ui/input';
import { RAGFlowPagination } from '@/components/ui/ragflow-pagination';
import { ScrollArea } from '@/components/ui/scroll-area';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { cn } from '@/lib/utils';
import { listAuditEvents } from '@/services/admin-service';

const outcomeClasses: Record<AdminService.AuditEventOutcome, string> = {
  success: 'bg-state-success',
  failure: 'bg-state-error',
  pending: 'bg-state-warning',
  cancelled: 'bg-text-disabled',
};

function actorLabel(event: AdminService.AuditEvent) {
  return (
    event.actor.nickname ||
    event.actor.email ||
    event.actor.id ||
    event.actor.type
  );
}

function AuditOutcome({
  outcome,
}: {
  outcome: AdminService.AuditEventOutcome;
}) {
  const { t } = useTranslation();
  return (
    <span className="inline-flex items-center gap-2 whitespace-nowrap text-sm">
      <span className={cn('size-2 rounded-full', outcomeClasses[outcome])} />
      {t(`admin.auditPage.outcomes.${outcome}`)}
    </span>
  );
}

function DetailRow({ label, value }: { label: string; value?: unknown }) {
  if (value === null || value === undefined || value === '') return null;
  return (
    <div className="grid grid-cols-[128px_minmax(0,1fr)] gap-4 border-b border-border-button py-3 last:border-0">
      <dt className="text-xs text-text-secondary">{label}</dt>
      <dd className="min-w-0 break-words text-sm text-text-primary">
        {typeof value === 'object' ? JSON.stringify(value) : String(value)}
      </dd>
    </div>
  );
}

function AuditInspector({
  event,
  open,
  onOpenChange,
  observability,
}: {
  event: AdminService.AuditEvent | null;
  open: boolean;
  onOpenChange(open: boolean): void;
  observability?: AdminService.AuditEventPage['observability'];
}) {
  const { t, i18n } = useTranslation();
  const { data: chainData, isFetching } = useQuery({
    queryKey: ['admin/audit-chain', event?.correlation_id],
    queryFn: async () =>
      (
        await listAuditEvents({
          correlation_id: event!.correlation_id!,
          page_size: 100,
        })
      ).data.data,
    enabled: open && Boolean(event?.correlation_id),
    retry: false,
  });
  const chain = useMemo(
    () =>
      [...(chainData?.items ?? [])].sort(
        (left, right) => left.occurred_at - right.occurred_at,
      ),
    [chainData?.items],
  );
  const exploreUrl = (kind: 'trace' | 'logs') => {
    if (!observability?.enabled || !event?.trace_id) return '';
    const datasource =
      kind === 'trace'
        ? observability.tempo_datasource_uid
        : observability.loki_datasource_uid;
    const query =
      kind === 'trace'
        ? { refId: 'A', query: event.trace_id, queryType: 'traceql' }
        : {
            refId: 'A',
            expr: `{service_name=~".+"} | json | trace_id="${event.trace_id}"`,
          };
    const panes = {
      audit: {
        datasource,
        queries: [query],
        range: { from: 'now-24h', to: 'now' },
      },
    };
    return `${observability.grafana_url}/explore?schemaVersion=1&panes=${encodeURIComponent(JSON.stringify(panes))}`;
  };

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="flex w-[560px] max-w-[92vw] flex-col p-0 sm:max-w-none">
        {event && (
          <>
            <SheetHeader className="border-b border-border-button px-6 py-5 pr-12">
              <div className="mb-1 flex items-center gap-3">
                <AuditOutcome outcome={event.outcome} />
                <Badge variant="secondary">
                  {t(`admin.auditPage.sources.${event.source}`)}
                </Badge>
              </div>
              <SheetTitle className="break-words">{event.action}</SheetTitle>
              <SheetDescription>{event.summary}</SheetDescription>
              {observability?.enabled && event.trace_id && (
                <div className="mt-3 flex gap-2">
                  <Button variant="outline" size="sm" asChild>
                    <a
                      href={exploreUrl('trace')}
                      target="_blank"
                      rel="noreferrer"
                    >
                      {t('admin.auditPage.openTrace')}
                      <ExternalLink className="size-3.5" />
                    </a>
                  </Button>
                  <Button variant="outline" size="sm" asChild>
                    <a
                      href={exploreUrl('logs')}
                      target="_blank"
                      rel="noreferrer"
                    >
                      {t('admin.auditPage.openLogs')}
                      <ExternalLink className="size-3.5" />
                    </a>
                  </Button>
                </div>
              )}
            </SheetHeader>

            <ScrollArea className="min-h-0 flex-1 px-6">
              <section className="py-5">
                <h3 className="mb-2 text-xs font-medium uppercase tracking-[0.12em] text-text-secondary">
                  {t('admin.auditPage.context')}
                </h3>
                <dl>
                  <DetailRow
                    label={t('admin.auditPage.time')}
                    value={new Intl.DateTimeFormat(i18n.language, {
                      dateStyle: 'medium',
                      timeStyle: 'medium',
                    }).format(event.occurred_at)}
                  />
                  <DetailRow
                    label={t('admin.auditPage.actor')}
                    value={actorLabel(event)}
                  />
                  <DetailRow
                    label={t('admin.auditPage.actorType')}
                    value={event.actor.type}
                  />
                  <DetailRow
                    label={t('admin.auditPage.object')}
                    value={`${event.object.label} · ${event.object.type}`}
                  />
                  <DetailRow
                    label={t('admin.auditPage.correlationId')}
                    value={event.correlation_id}
                  />
                  <DetailRow
                    label={t('admin.auditPage.causationId')}
                    value={event.causation_id}
                  />
                  <DetailRow
                    label={t('admin.auditPage.requestId')}
                    value={event.request_id}
                  />
                  <DetailRow
                    label={t('admin.auditPage.traceId')}
                    value={event.trace_id}
                  />
                  <DetailRow
                    label={t('admin.auditPage.spanId')}
                    value={event.span_id}
                  />
                  <DetailRow
                    label={t('admin.auditPage.interactionId')}
                    value={event.interaction_id}
                  />
                  <DetailRow
                    label={t('admin.auditPage.jobId')}
                    value={event.job_id}
                  />
                  <DetailRow
                    label={t('admin.auditPage.sessionId')}
                    value={event.session_id}
                  />
                  <DetailRow
                    label={t('admin.auditPage.errorId')}
                    value={event.error_id}
                  />
                </dl>
              </section>

              {event.error && (
                <section className="mb-5 rounded-lg border border-state-error/30 bg-state-error/5 p-4">
                  <div className="mb-2 flex items-center gap-2 text-sm font-medium text-state-error">
                    <CircleX className="size-4" />
                    {t('admin.auditPage.error')}
                  </div>
                  {event.error.code && (
                    <div className="mb-1 font-mono text-xs text-text-secondary">
                      {event.error.code}
                    </div>
                  )}
                  <p className="break-words text-sm text-text-primary">
                    {event.error.message}
                  </p>
                </section>
              )}

              <section className="mb-5">
                <h3 className="mb-2 text-xs font-medium uppercase tracking-[0.12em] text-text-secondary">
                  {t('admin.auditPage.details')}
                </h3>
                <dl className="rounded-lg border border-border-button px-4">
                  {Object.entries(event.details).map(([key, value]) => (
                    <DetailRow key={key} label={key} value={value} />
                  ))}
                </dl>
              </section>

              {event.correlation_id && (
                <section className="pb-6">
                  <div className="mb-3 flex items-center justify-between">
                    <h3 className="text-xs font-medium uppercase tracking-[0.12em] text-text-secondary">
                      {t('admin.auditPage.chain')}
                    </h3>
                    {isFetching && (
                      <RefreshCw className="size-3.5 animate-spin text-text-secondary" />
                    )}
                  </div>
                  <div className="border-l border-border-button pl-4">
                    {chain.map((chainEvent) => (
                      <div
                        key={chainEvent.id}
                        className="relative pb-4 last:pb-0"
                      >
                        <span
                          className={cn(
                            'absolute -left-[19px] top-1 size-2 rounded-full ring-4 ring-bg-base',
                            outcomeClasses[chainEvent.outcome],
                          )}
                        />
                        <div className="text-sm text-text-primary">
                          {chainEvent.action}
                        </div>
                        <div className="mt-0.5 text-xs text-text-secondary">
                          {new Intl.DateTimeFormat(i18n.language, {
                            dateStyle: 'short',
                            timeStyle: 'medium',
                          }).format(chainEvent.occurred_at)}
                        </div>
                      </div>
                    ))}
                  </div>
                </section>
              )}
            </ScrollArea>
          </>
        )}
      </SheetContent>
    </Sheet>
  );
}

export default function AdminAudit() {
  const { t, i18n } = useTranslation();
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const [source, setSource] = useState<AdminService.AuditEventSource | ''>('');
  const [outcome, setOutcome] = useState<AdminService.AuditEventOutcome | ''>(
    '',
  );
  const [search, setSearch] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const [selectedEvent, setSelectedEvent] =
    useState<AdminService.AuditEvent | null>(null);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setDebouncedSearch(search.trim());
      setPage(1);
    }, 300);
    return () => window.clearTimeout(timer);
  }, [search]);

  const { data, isLoading, isFetching, refetch } = useQuery({
    queryKey: [
      'admin/audit-events',
      page,
      pageSize,
      source,
      outcome,
      debouncedSearch,
    ],
    queryFn: async () =>
      (
        await listAuditEvents({
          page,
          page_size: pageSize,
          source,
          outcome,
          query: debouncedSearch,
        })
      ).data.data,
    retry: false,
    placeholderData: (previousData) => previousData,
  });

  const events = data?.items ?? [];
  const unavailable = data?.unavailable_sources ?? [];

  return (
    <div className="flex h-full min-w-0 flex-col overflow-hidden rounded-xl border border-border-button bg-bg-base">
      <header className="border-b border-border-button px-6 py-5">
        <div className="flex items-start justify-between gap-6">
          <div>
            <h1 className="text-xl font-semibold text-text-primary">
              {t('admin.auditPage.title')}
            </h1>
            <p className="mt-1 text-sm text-text-secondary">
              {t('admin.auditPage.description', {
                days: data?.retention_days ?? 30,
              })}
            </p>
          </div>
          <Button
            variant="outline"
            onClick={() => refetch()}
            disabled={isFetching}
          >
            <RefreshCw className={cn(isFetching && 'animate-spin')} />
            {t('admin.auditPage.refresh')}
          </Button>
        </div>

        <div className="mt-5 flex items-center divide-x divide-border-button text-sm">
          <div className="pr-6">
            <span className="font-semibold text-text-primary">
              {data?.total ?? 0}
            </span>{' '}
            <span className="text-text-secondary">
              {t('admin.auditPage.events')}
            </span>
          </div>
          <div className="px-6">
            <span className="font-semibold text-state-error">
              {data?.stats.failures ?? 0}
            </span>{' '}
            <span className="text-text-secondary">
              {t('admin.auditPage.failures')}
            </span>
          </div>
          <div className="px-6">
            <span className="font-semibold text-text-primary">
              {data?.stats.sources ?? 0}
            </span>{' '}
            <span className="text-text-secondary">
              {t('admin.auditPage.activeSources')}
            </span>
          </div>
        </div>
      </header>

      <div className="flex flex-wrap items-center gap-3 border-b border-border-button px-6 py-3">
        <SearchInput
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder={t('admin.auditPage.searchPlaceholder')}
          rootClassName="min-w-[280px] flex-1"
        />
        <Select
          value={source || 'all'}
          onValueChange={(value) => {
            setSource(
              value === 'all' ? '' : (value as AdminService.AuditEventSource),
            );
            setPage(1);
          }}
        >
          <SelectTrigger className="w-52">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">
              {t('admin.auditPage.allSources')}
            </SelectItem>
            {(
              [
                'application',
                'business_documents',
                'ingestion',
                'connectors',
              ] as const
            ).map((value) => (
              <SelectItem key={value} value={value}>
                {t(`admin.auditPage.sources.${value}`)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select
          value={outcome || 'all'}
          onValueChange={(value) => {
            setOutcome(
              value === 'all' ? '' : (value as AdminService.AuditEventOutcome),
            );
            setPage(1);
          }}
        >
          <SelectTrigger className="w-44">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">
              {t('admin.auditPage.allOutcomes')}
            </SelectItem>
            {(['success', 'failure', 'pending', 'cancelled'] as const).map(
              (value) => (
                <SelectItem key={value} value={value}>
                  {t(`admin.auditPage.outcomes.${value}`)}
                </SelectItem>
              ),
            )}
          </SelectContent>
        </Select>
      </div>

      {unavailable.length > 0 && (
        <div className="flex items-center gap-2 border-b border-state-warning/30 bg-state-warning/5 px-6 py-2 text-sm text-text-primary">
          <AlertTriangle className="size-4 text-state-warning" />
          {t('admin.auditPage.unavailableSources', {
            sources: unavailable
              .map((item) => t(`admin.auditPage.sources.${item}`))
              .join(', '),
          })}
        </div>
      )}

      <div className="min-h-0 flex-1 overflow-auto">
        <Table rootClassName="min-h-full rounded-none bg-transparent">
          <TableHeader>
            <TableRow>
              <TableHead className="w-44">
                {t('admin.auditPage.time')}
              </TableHead>
              <TableHead className="w-32">
                {t('admin.auditPage.outcome')}
              </TableHead>
              <TableHead>{t('admin.auditPage.event')}</TableHead>
              <TableHead className="w-48">
                {t('admin.auditPage.actor')}
              </TableHead>
              <TableHead className="w-48">
                {t('admin.auditPage.object')}
              </TableHead>
              <TableHead className="w-40">
                {t('admin.auditPage.source')}
              </TableHead>
              <TableHead className="w-10" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {isLoading ? (
              Array.from({ length: 8 }).map((_, index) => (
                <TableRow key={index}>
                  <TableCell colSpan={7} className="py-4">
                    <div className="h-4 animate-pulse rounded bg-bg-card" />
                  </TableCell>
                </TableRow>
              ))
            ) : events.length === 0 ? (
              <TableRow>
                <TableCell colSpan={7} className="h-56 text-center">
                  <SearchX className="mx-auto mb-3 size-8 text-text-disabled" />
                  <div className="text-sm text-text-primary">
                    {t('admin.auditPage.empty')}
                  </div>
                  <div className="mt-1 text-xs text-text-secondary">
                    {t('admin.auditPage.emptyHint')}
                  </div>
                </TableCell>
              </TableRow>
            ) : (
              events.map((event) => (
                <TableRow
                  key={event.id}
                  tabIndex={0}
                  className="group cursor-pointer hover:bg-bg-card/60 focus-visible:bg-bg-card focus-visible:outline-none"
                  onClick={() => setSelectedEvent(event)}
                  onKeyDown={(keyEvent) => {
                    if (keyEvent.key === 'Enter' || keyEvent.key === ' ') {
                      keyEvent.preventDefault();
                      setSelectedEvent(event);
                    }
                  }}
                >
                  <TableCell className="whitespace-nowrap text-xs text-text-secondary">
                    <div>
                      {new Intl.DateTimeFormat(i18n.language, {
                        dateStyle: 'short',
                      }).format(event.occurred_at)}
                    </div>
                    <div className="mt-0.5 flex items-center gap-1">
                      <Clock3 className="size-3" />
                      {new Intl.DateTimeFormat(i18n.language, {
                        timeStyle: 'medium',
                      }).format(event.occurred_at)}
                    </div>
                  </TableCell>
                  <TableCell>
                    <AuditOutcome outcome={event.outcome} />
                  </TableCell>
                  <TableCell>
                    <div className="font-medium text-text-primary">
                      {event.action}
                    </div>
                    <div className="mt-0.5 max-w-[420px] truncate text-xs text-text-secondary">
                      {event.summary}
                    </div>
                  </TableCell>
                  <TableCell>
                    <div className="max-w-44 truncate">{actorLabel(event)}</div>
                    <div className="mt-0.5 text-xs text-text-secondary">
                      {event.actor.type}
                    </div>
                  </TableCell>
                  <TableCell>
                    <div className="max-w-44 truncate">
                      {event.object.label}
                    </div>
                    <div className="mt-0.5 font-mono text-[11px] text-text-secondary">
                      {event.object.id.slice(0, 12)}
                    </div>
                  </TableCell>
                  <TableCell>
                    <Badge variant="secondary">
                      {t(`admin.auditPage.sources.${event.source}`)}
                    </Badge>
                  </TableCell>
                  <TableCell>
                    <ChevronRight className="size-4 text-text-disabled transition-transform group-hover:translate-x-0.5 group-hover:text-text-primary" />
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </div>

      <footer className="border-t border-border-button px-6 py-3">
        <RAGFlowPagination
          current={page}
          pageSize={pageSize}
          total={data?.total ?? 0}
          onChange={(nextPage, nextPageSize) => {
            setPage(nextPageSize === pageSize ? nextPage : 1);
            setPageSize(nextPageSize);
          }}
        />
      </footer>

      <AuditInspector
        event={selectedEvent}
        open={Boolean(selectedEvent)}
        observability={data?.observability}
        onOpenChange={(open) => {
          if (!open) setSelectedEvent(null);
        }}
      />
    </div>
  );
}
