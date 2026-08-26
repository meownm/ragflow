import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import {
  askOpenMetadata,
  confirmOpenMetadataChange,
  fetchOpenMetadataEntities,
  fetchOpenMetadataStarterQuestions,
  fetchOpenMetadataStatus,
  previewOpenMetadataChange,
  provisionOpenMetadataAgents,
} from '@/services/openmetadata-service';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AlertTriangle,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  ExternalLink,
  LoaderCircle,
  MessageCircle,
  Network,
  RefreshCw,
  Search,
  ShieldCheck,
  Sparkles,
} from 'lucide-react';
import { FormEvent, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import type {
  CatalogAction,
  CatalogAnswer,
  CatalogEntity,
  CatalogFilters,
  ForeignKeyEdge,
  GovernancePreview,
  LineageEdge,
  SemanticRelationship,
} from './types';

const count = (value: number | null | undefined) =>
  value === null || value === undefined ? '—' : value.toLocaleString('ru-RU');
const CATALOG_PAGE_SIZE = 20;

type AskPayload = {
  value: string;
  action?: CatalogAction;
  selectedEntityId?: string;
};

const formatSnapshot = (value?: string | null) => {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleString();
};

function EntityCard({
  entity,
  governanceAllowed,
  onGovern,
  onSelect,
  onAsk,
  onExploreRelations,
  disabled = false,
}: {
  entity: CatalogEntity;
  governanceAllowed: boolean;
  onGovern: (entity: CatalogEntity) => void;
  onSelect?: (entity: CatalogEntity) => void;
  onAsk?: (entity: CatalogEntity) => void;
  onExploreRelations?: (entity: CatalogEntity) => void;
  disabled?: boolean;
}) {
  const { t } = useTranslation();
  return (
    <Card className="bg-bg-card" data-testid="openmetadata-entity-card">
      <CardHeader className="gap-2 p-4 pb-2">
        <div className="flex min-w-0 items-start justify-between gap-3">
          <div className="min-w-0">
            <CardTitle as="h3" className="truncate text-base">
              {entity.name || entity.technical_name}
            </CardTitle>
            <CardDescription className="break-all font-mono text-xs">
              {entity.fqn}
            </CardDescription>
          </div>
          <a
            href={entity.url}
            target="_blank"
            rel="noreferrer noopener"
            aria-label={t('openMetadata.openInOmd')}
            className="shrink-0 rounded-md p-2 text-text-secondary hover:bg-bg-input hover:text-text-primary"
          >
            <ExternalLink className="size-4" />
          </a>
        </div>
      </CardHeader>
      <CardContent className="space-y-3 p-4 pt-2">
        <p className="line-clamp-3 text-sm text-text-secondary">
          {entity.description || t('openMetadata.noDescription')}
        </p>
        <div className="flex flex-wrap gap-1.5">
          {entity.service && (
            <Badge variant="secondary">{entity.service}</Badge>
          )}
          {entity.domains.map((domain) => (
            <Badge key={`domain-${domain}`} variant="outline">
              {domain}
            </Badge>
          ))}
          {entity.tags.slice(0, 3).map((tag) => (
            <Badge key={`tag-${tag}`} variant="secondary">
              {tag}
            </Badge>
          ))}
        </div>
        <div className="grid grid-cols-2 gap-2 text-xs text-text-secondary sm:grid-cols-4">
          <span>
            {t('openMetadata.columns')}: {count(entity.column_count)}
          </span>
          <span>
            {t('openMetadata.owner')}: {entity.owners.join(', ') || '—'}
          </span>
          <span>
            {t('openMetadata.schema')}: {entity.schema || '—'}
          </span>
          <span>
            {t('openMetadata.updated')}: {formatSnapshot(entity.updated_at)}
          </span>
        </div>
        <div className="flex flex-wrap gap-2">
          {onSelect && (
            <Button
              size="sm"
              disabled={disabled}
              onClick={() => onSelect(entity)}
            >
              {t('openMetadata.selectTable')}
            </Button>
          )}
          {onAsk && (
            <Button
              size="sm"
              variant="outline"
              disabled={disabled}
              onClick={() => onAsk(entity)}
            >
              <MessageCircle className="me-2 size-4" />
              {t('openMetadata.askAboutTable')}
            </Button>
          )}
          {onExploreRelations && (
            <Button
              size="sm"
              variant="outline"
              disabled={disabled}
              onClick={() => onExploreRelations(entity)}
            >
              <Network className="me-2 size-4" />
              {t('openMetadata.exploreRelationships')}
            </Button>
          )}
          {governanceAllowed && (
            <Button
              size="sm"
              variant="outline"
              disabled={disabled}
              onClick={() => onGovern(entity)}
            >
              <ShieldCheck className="me-2 size-4" />
              {t('openMetadata.prepareChange')}
            </Button>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

function LineageList({
  title,
  edges,
}: {
  title: string;
  edges?: LineageEdge[];
}) {
  if (!edges?.length) return null;
  return (
    <div className="space-y-2">
      <h4 className="text-sm font-medium">{title}</h4>
      <ul className="space-y-1 text-sm text-text-secondary">
        {edges.map((edge) => (
          <li
            key={`${edge.from.id}-${edge.to.id}`}
            className="flex flex-wrap items-center gap-2 rounded-md bg-bg-input px-3 py-2"
          >
            <span className="break-all font-mono text-xs">
              {edge.from.fqn || edge.from.name || edge.from.id}
            </span>
            <span aria-hidden>→</span>
            <span className="break-all font-mono text-xs">
              {edge.to.fqn || edge.to.name || edge.to.id}
            </span>
            <Badge variant="secondary">{edge.source}</Badge>
            {!!edge.column_lineage?.length && (
              <span className="basis-full break-all font-mono text-xs">
                {edge.column_lineage
                  .map(
                    (mapping) =>
                      `${mapping.from_columns.join(', ')} → ${mapping.to_column}`,
                  )
                  .join('; ')}
              </span>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

function ForeignKeyList({ edges }: { edges?: ForeignKeyEdge[] }) {
  const { t } = useTranslation();
  if (!edges?.length) return null;
  return (
    <div className="space-y-2">
      <h4 className="text-sm font-medium">{t('openMetadata.foreignKeys')}</h4>
      <ul className="space-y-1 text-sm text-text-secondary">
        {edges.map((edge) => (
          <li
            key={`${edge.from.id}-${edge.to.id}-${edge.from_columns.join(',')}`}
            className="flex flex-wrap items-center gap-2 rounded-md bg-bg-input px-3 py-2"
          >
            <span className="break-all font-mono text-xs">
              {edge.from.fqn} ({edge.from_columns.join(', ')})
            </span>
            <span aria-hidden>→</span>
            <span className="break-all font-mono text-xs">
              {edge.to.fqn} ({edge.to_columns.join(', ')})
            </span>
            <Badge variant="outline">FK</Badge>
            {edge.cardinality && (
              <Badge variant="secondary">{edge.cardinality}</Badge>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

function SemanticRelationshipList({
  edges,
  truncated,
}: {
  edges?: SemanticRelationship[];
  truncated?: boolean;
}) {
  const { t } = useTranslation();
  if (!edges?.length) return null;
  return (
    <div className="space-y-2">
      <h4 className="text-sm font-medium">
        {t('openMetadata.semanticRelationships')}
      </h4>
      <ul className="space-y-1 text-sm text-text-secondary">
        {edges.map((edge) => (
          <li
            key={`${edge.from.id}-${edge.to.id}`}
            className="flex flex-wrap items-center gap-2 rounded-md bg-bg-input px-3 py-2"
          >
            <span className="break-all font-mono text-xs">{edge.to.fqn}</span>
            {edge.shared_terms.map((term) => (
              <Badge key={term} variant="secondary">
                {term}
              </Badge>
            ))}
          </li>
        ))}
      </ul>
      {truncated && (
        <p className="text-xs text-text-secondary">
          {t('openMetadata.semanticRelationshipsTruncated')}
        </p>
      )}
    </div>
  );
}

function GovernancePanel({
  entity,
  onClose,
  onApplied,
}: {
  entity: CatalogEntity;
  onClose: () => void;
  onApplied: () => void;
}) {
  const { t } = useTranslation();
  const initialDisplayName = entity.display_name ?? '';
  const initialDescription = entity.description ?? '';
  const [displayName, setDisplayName] = useState(initialDisplayName);
  const [description, setDescription] = useState(initialDescription);
  const [preview, setPreview] = useState<GovernancePreview | null>(null);
  const changes = useMemo(() => {
    const next: { description?: string | null; displayName?: string | null } =
      {};
    if (displayName !== initialDisplayName)
      next.displayName = displayName || null;
    if (description !== initialDescription)
      next.description = description || null;
    return next;
  }, [description, displayName, initialDescription, initialDisplayName]);
  const hasChanges = Object.keys(changes).length > 0;
  const previewMutation = useMutation({
    mutationFn: () => previewOpenMetadataChange(entity.id, changes),
    onSuccess: setPreview,
  });
  const confirmMutation = useMutation({
    mutationFn: (token: string) => confirmOpenMetadataChange(token),
    onSuccess: () => {
      setPreview(null);
      onApplied();
      onClose();
    },
  });

  return (
    <Card
      className="border border-accent-primary"
      data-testid="openmetadata-governance-panel"
    >
      <CardHeader>
        <CardTitle as="h2" className="flex items-center gap-2 text-lg">
          <ShieldCheck className="size-5" />
          {t('openMetadata.governanceTitle')}
        </CardTitle>
        <CardDescription>{entity.fqn}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <label className="block space-y-1 text-sm">
          <span>{t('openMetadata.displayName')}</span>
          <Input
            value={displayName}
            maxLength={10000}
            disabled={Boolean(preview) || confirmMutation.isPending}
            onChange={(event) => setDisplayName(event.target.value)}
          />
        </label>
        <label className="block space-y-1 text-sm">
          <span>{t('openMetadata.description')}</span>
          <Textarea
            value={description}
            maxLength={10000}
            autoSize={{ minRows: 4, maxRows: 12 }}
            resize="vertical"
            disabled={Boolean(preview) || confirmMutation.isPending}
            onChange={(event) => setDescription(event.target.value)}
          />
        </label>
        {previewMutation.error && (
          <p role="alert" className="text-sm text-state-error">
            {previewMutation.error.message}
          </p>
        )}
        {!preview && (
          <div className="flex flex-wrap gap-2">
            <Button
              onClick={() => previewMutation.mutate()}
              disabled={!hasChanges || previewMutation.isPending}
            >
              {previewMutation.isPending && (
                <LoaderCircle className="me-2 size-4 animate-spin" />
              )}
              {t('openMetadata.previewChange')}
            </Button>
            <Button variant="outline" onClick={onClose}>
              {t('common.cancel')}
            </Button>
          </div>
        )}
        {preview && (
          <div className="space-y-4 rounded-lg border border-border-button bg-bg-input p-4">
            <div>
              <h3 className="font-medium">{t('openMetadata.diffTitle')}</h3>
              <p className="text-xs text-text-secondary">
                {t('openMetadata.confirmationExpires', {
                  seconds: preview.expires_in_seconds,
                })}
              </p>
            </div>
            {preview.diff.map((item) => (
              <div
                key={item.field}
                className="grid gap-2 text-sm md:grid-cols-[8rem_1fr_1fr]"
              >
                <strong>{item.field}</strong>
                <div className="rounded bg-state-error/5 p-2 break-words">
                  − {item.before || '∅'}
                </div>
                <div className="rounded bg-state-success/5 p-2 break-words">
                  + {item.after || '∅'}
                </div>
              </div>
            ))}
            {confirmMutation.error && (
              <p role="alert" className="text-sm text-state-error">
                {confirmMutation.error.message}
              </p>
            )}
            <div className="flex flex-wrap gap-2">
              <Button
                variant="destructive"
                disabled={confirmMutation.isPending}
                onClick={() =>
                  confirmMutation.mutate(preview.confirmation_token)
                }
              >
                {confirmMutation.isPending && (
                  <LoaderCircle className="me-2 size-4 animate-spin" />
                )}
                {t('openMetadata.confirmApply')}
              </Button>
              <Button variant="outline" onClick={() => setPreview(null)}>
                {t('openMetadata.backToEdit')}
              </Button>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

export default function OpenMetadataPage() {
  const { t, i18n } = useTranslation();
  const queryClient = useQueryClient();
  const locale = i18n.resolvedLanguage || i18n.language || 'en';
  const [question, setQuestion] = useState('');
  const [history, setHistory] = useState<CatalogAnswer[]>([]);
  const [governanceEntity, setGovernanceEntity] =
    useState<CatalogEntity | null>(null);
  const [filters, setFilters] = useState({
    owner: '',
    domain: '',
    service: '',
    tag: '',
  });
  const [catalogDraft, setCatalogDraft] = useState('');
  const [catalogSearch, setCatalogSearch] = useState('');
  const [catalogPage, setCatalogPage] = useState(0);
  const [onlyMissingDescriptions, setOnlyMissingDescriptions] = useState(false);
  const activeFilters = useMemo<CatalogFilters>(
    () =>
      Object.fromEntries(Object.entries(filters).filter(([, item]) => item)),
    [filters],
  );
  const catalogFilters = useMemo<CatalogFilters>(
    () => ({
      ...activeFilters,
      ...(onlyMissingDescriptions ? { has_description: false } : {}),
    }),
    [activeFilters, onlyMissingDescriptions],
  );

  const statusQuery = useQuery({
    queryKey: ['openmetadata', 'status'],
    queryFn: () => fetchOpenMetadataStatus(),
    retry: 1,
  });
  const starterQuery = useQuery({
    queryKey: ['openmetadata', 'starter-questions', locale],
    queryFn: () => fetchOpenMetadataStarterQuestions(locale),
    retry: 1,
  });
  const catalogQuery = useQuery({
    queryKey: [
      'openmetadata',
      'entities',
      catalogSearch,
      catalogFilters,
      catalogPage,
      locale,
    ],
    queryFn: () =>
      fetchOpenMetadataEntities({
        query: catalogSearch,
        filters: catalogFilters,
        limit: CATALOG_PAGE_SIZE,
        offset: catalogPage * CATALOG_PAGE_SIZE,
        sort: catalogSearch ? 'relevance' : 'fqn',
        locale,
      }),
    enabled: Boolean(statusQuery.data),
    retry: 1,
  });
  const refreshMutation = useMutation({
    mutationFn: () => fetchOpenMetadataStatus(true),
    onSuccess: (data) => {
      queryClient.setQueryData(['openmetadata', 'status'], data);
      queryClient.invalidateQueries({
        queryKey: ['openmetadata', 'starter-questions'],
      });
      queryClient.invalidateQueries({ queryKey: ['openmetadata', 'entities'] });
      setHistory([]);
      setGovernanceEntity(null);
    },
  });
  const provisionAgentsMutation = useMutation({
    mutationFn: provisionOpenMetadataAgents,
  });
  const askMutation = useMutation({
    mutationFn: ({ value, action, selectedEntityId }: AskPayload) =>
      askOpenMetadata(value, activeFilters, {
        action,
        selectedEntityId,
        locale,
        context: history.slice(-8).map((answer) => ({
          question: answer.question,
          entity_ids: Array.from(
            new Set([
              ...(answer.entities || []).map((entity) => entity.id),
              ...(answer.entity?.id ? [answer.entity.id] : []),
            ]),
          ),
        })),
      }),
    onSuccess: (answer) => {
      setHistory((current) => [...current, answer]);
      setQuestion('');
    },
  });

  useEffect(() => {
    setCatalogPage(0);
  }, [
    filters.domain,
    filters.owner,
    filters.service,
    filters.tag,
    onlyMissingDescriptions,
  ]);

  const status = statusQuery.data;
  const metrics = useMemo(
    () => [
      [t('openMetadata.tables'), status?.capabilities.tables],
      [t('openMetadata.columns'), status?.capabilities.columns],
      [
        t('openMetadata.describedTables'),
        status?.capabilities.described_tables,
      ],
      [t('openMetadata.testCases'), status?.capabilities.test_cases],
      [
        t('openMetadata.foreignKeys'),
        status?.capabilities.foreign_key_constraints,
      ],
    ],
    [status, t],
  );

  const submit = (event?: FormEvent) => {
    event?.preventDefault();
    const value = question.trim();
    if (value && !askMutation.isPending) askMutation.mutate({ value });
  };

  const ask = (payload: AskPayload) => {
    if (!askMutation.isPending) askMutation.mutate(payload);
  };

  if (statusQuery.isLoading) {
    return (
      <main
        className="flex min-h-[60vh] items-center justify-center"
        data-testid="openmetadata-loading"
      >
        <LoaderCircle className="size-8 animate-spin" />
      </main>
    );
  }

  if (statusQuery.error || !status) {
    return (
      <main className="mx-auto max-w-3xl p-6">
        <Card className="border border-state-error">
          <CardHeader>
            <CardTitle as="h1" className="flex items-center gap-2 text-xl">
              <AlertTriangle className="size-5 text-state-error" />
              {t('openMetadata.unavailable')}
            </CardTitle>
            <CardDescription>{statusQuery.error?.message}</CardDescription>
          </CardHeader>
          <CardContent>
            <Button onClick={() => statusQuery.refetch()}>
              {t('openMetadata.retry')}
            </Button>
          </CardContent>
        </Card>
      </main>
    );
  }

  return (
    <main
      className="mx-auto h-full w-full max-w-7xl space-y-6 overflow-y-auto px-4 py-6 scrollbar-auto lg:px-8"
      data-testid="openmetadata-page"
    >
      <section className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
        <div>
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <Badge variant="success">
              <CheckCircle2 className="me-1 size-3" />
              OMD {status.version}
            </Badge>
            <Badge variant={status.freshness.stale ? 'destructive' : 'success'}>
              {status.freshness.stale
                ? t('openMetadata.stale')
                : t('openMetadata.fresh')}
            </Badge>
            {status.governance_allowed && (
              <Badge variant="outline">Governance</Badge>
            )}
            {status.knowledge_graph?.enabled && (
              <Badge variant="outline">
                RDF {status.knowledge_graph.storage_type}
              </Badge>
            )}
          </div>
          <h1 className="text-3xl font-semibold tracking-tight">
            {t('openMetadata.title')}
          </h1>
          <p className="mt-2 max-w-3xl text-text-secondary">
            {t('openMetadata.subtitle')}
          </p>
          <p className="mt-1 text-xs text-text-secondary">
            {t('openMetadata.snapshot')}:{' '}
            {formatSnapshot(status.freshness.snapshot_at)}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {status.governance_allowed && (
            <Button
              variant="outline"
              disabled={provisionAgentsMutation.isPending}
              onClick={() => provisionAgentsMutation.mutate()}
            >
              {provisionAgentsMutation.isPending && (
                <LoaderCircle className="me-2 size-4 animate-spin" />
              )}
              {provisionAgentsMutation.data
                ? t('openMetadata.agentAppsReady', {
                    count: provisionAgentsMutation.data.count,
                  })
                : t('openMetadata.provisionAgentApps')}
            </Button>
          )}
          <Button
            variant="outline"
            disabled={refreshMutation.isPending}
            onClick={() => refreshMutation.mutate()}
          >
            <RefreshCw
              className={`me-2 size-4 ${refreshMutation.isPending ? 'animate-spin' : ''}`}
            />
            {t('openMetadata.refresh')}
          </Button>
        </div>
      </section>

      {provisionAgentsMutation.error && (
        <p role="alert" className="text-sm text-state-error">
          {provisionAgentsMutation.error.message}
        </p>
      )}

      {status.freshness.stale && (
        <div
          role="status"
          className="flex gap-3 rounded-lg border border-state-warning/40 bg-state-warning/5 p-4 text-sm"
        >
          <AlertTriangle className="mt-0.5 size-5 shrink-0 text-state-warning" />
          <span>{t('openMetadata.staleWarning')}</span>
        </div>
      )}

      <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {metrics.map(([label, value]) => (
          <Card key={String(label)}>
            <CardContent className="p-4">
              <p className="text-sm text-text-secondary">{label}</p>
              <p className="mt-1 text-2xl font-semibold">
                {count(value as number | null)}
              </p>
            </CardContent>
          </Card>
        ))}
      </section>

      <Card>
        <CardHeader>
          <CardTitle as="h2" className="flex items-center gap-2 text-xl">
            <Sparkles className="size-5" />
            {t('openMetadata.starterTitle')}
          </CardTitle>
          <CardDescription>{t('openMetadata.starterSubtitle')}</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap gap-2">
          {starterQuery.isLoading && (
            <LoaderCircle className="size-5 animate-spin" />
          )}
          {starterQuery.data?.questions.map((item) => (
            <Button
              key={item.id}
              variant="outline"
              className="h-auto whitespace-normal py-2 text-start"
              title={item.reason}
              disabled={askMutation.isPending}
              onClick={() => ask({ value: item.question, action: item.action })}
            >
              {item.question}
            </Button>
          ))}
          {!starterQuery.isLoading && !starterQuery.data?.questions.length && (
            <span className="text-sm text-text-secondary">
              {t('openMetadata.noStarterQuestions')}
            </span>
          )}
        </CardContent>
      </Card>

      <Card data-testid="openmetadata-browser">
        <CardHeader>
          <CardTitle as="h2" className="text-xl">
            {t('openMetadata.catalogTitle')}
          </CardTitle>
          <CardDescription>{t('openMetadata.catalogSubtitle')}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <form
            className="flex flex-col gap-2 sm:flex-row"
            onSubmit={(event) => {
              event.preventDefault();
              setCatalogPage(0);
              setCatalogSearch(catalogDraft.trim());
            }}
          >
            <Input
              value={catalogDraft}
              aria-label={t('openMetadata.catalogSearch')}
              placeholder={t('openMetadata.catalogSearch')}
              onChange={(event) => setCatalogDraft(event.target.value)}
            />
            <Button type="submit" disabled={catalogQuery.isFetching}>
              {catalogQuery.isFetching ? (
                <LoaderCircle className="me-2 size-4 animate-spin" />
              ) : (
                <Search className="me-2 size-4" />
              )}
              {t('openMetadata.searchCatalog')}
            </Button>
          </form>
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
            {(['domain', 'service', 'owner', 'tag'] as const).map((key) => (
              <Input
                key={key}
                value={filters[key]}
                aria-label={t(`openMetadata.${key}`)}
                placeholder={t(`openMetadata.filterBy`, {
                  field: t(`openMetadata.${key}`),
                })}
                onChange={(event) =>
                  setFilters((current) => ({
                    ...current,
                    [key]: event.target.value,
                  }))
                }
              />
            ))}
          </div>
          <label className="flex w-fit items-center gap-2 text-sm text-text-secondary">
            <input
              type="checkbox"
              checked={onlyMissingDescriptions}
              onChange={(event) =>
                setOnlyMissingDescriptions(event.target.checked)
              }
            />
            {t('openMetadata.onlyMissingDescriptions')}
          </label>
          {catalogQuery.error && (
            <p role="alert" className="text-sm text-state-error">
              {catalogQuery.error.message}
            </p>
          )}
          {catalogQuery.isLoading && (
            <LoaderCircle className="size-6 animate-spin" />
          )}
          {!catalogQuery.isLoading && !catalogQuery.data?.entities.length && (
            <p className="text-sm text-text-secondary">
              {t('openMetadata.noCatalogEntities')}
            </p>
          )}
          {!!catalogQuery.data?.entities.length && (
            <div className="grid gap-3 lg:grid-cols-2">
              {catalogQuery.data.entities.map((entity) => (
                <EntityCard
                  key={entity.id || entity.fqn}
                  entity={entity}
                  governanceAllowed={status.governance_allowed}
                  onGovern={setGovernanceEntity}
                  disabled={askMutation.isPending}
                  onAsk={(selected) =>
                    ask({
                      value: t('openMetadata.aboutTableQuestion', {
                        fqn: selected.fqn,
                      }),
                      selectedEntityId: selected.id,
                    })
                  }
                  onExploreRelations={(selected) =>
                    ask({
                      value: t('openMetadata.relationshipsQuestion', {
                        fqn: selected.fqn,
                      }),
                      action: { type: 'impact', entity_id: selected.id },
                    })
                  }
                />
              ))}
            </div>
          )}
          {catalogQuery.data && catalogQuery.data.total_matches > 0 && (
            <div className="flex flex-wrap items-center justify-between gap-3">
              <span className="text-sm text-text-secondary">
                {t('openMetadata.catalogPage', {
                  from: catalogQuery.data.offset + 1,
                  to: Math.min(
                    catalogQuery.data.offset +
                      catalogQuery.data.entities.length,
                    catalogQuery.data.total_matches,
                  ),
                  total: catalogQuery.data.total_matches,
                })}
              </span>
              <div className="flex gap-2">
                <Button
                  variant="outline"
                  disabled={catalogPage === 0 || catalogQuery.isFetching}
                  onClick={() =>
                    setCatalogPage((current) => Math.max(0, current - 1))
                  }
                >
                  <ChevronLeft className="me-2 size-4" />
                  {t('openMetadata.previousPage')}
                </Button>
                <Button
                  variant="outline"
                  disabled={
                    catalogQuery.isFetching ||
                    catalogQuery.data.offset +
                      catalogQuery.data.entities.length >=
                      catalogQuery.data.total_matches
                  }
                  onClick={() => setCatalogPage((current) => current + 1)}
                >
                  {t('openMetadata.nextPage')}
                  <ChevronRight className="ms-2 size-4" />
                </Button>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex-row items-start justify-between gap-4">
          <div>
            <CardTitle as="h2" className="text-xl">
              {t('openMetadata.conversationTitle')}
            </CardTitle>
            <CardDescription>
              {t('openMetadata.conversationSubtitle')}
            </CardDescription>
          </div>
          {history.length > 0 && (
            <Button
              variant="outline"
              size="sm"
              disabled={askMutation.isPending}
              onClick={() => setHistory([])}
            >
              {t('openMetadata.newConversation')}
            </Button>
          )}
        </CardHeader>
        <CardContent>
          <form onSubmit={submit} className="space-y-3">
            <div className="flex items-end gap-2">
              <Textarea
                value={question}
                maxLength={2000}
                autoSize={{ minRows: 2, maxRows: 8 }}
                resize="vertical"
                placeholder={t('openMetadata.questionPlaceholder')}
                aria-label={t('openMetadata.questionPlaceholder')}
                onChange={(event) => setQuestion(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' && !event.shiftKey) {
                    event.preventDefault();
                    submit();
                  }
                }}
              />
              <Button
                type="submit"
                size="lg"
                disabled={!question.trim() || askMutation.isPending}
              >
                {askMutation.isPending ? (
                  <LoaderCircle className="size-5 animate-spin" />
                ) : (
                  <Search className="size-5" />
                )}
                <span className="sr-only">{t('openMetadata.ask')}</span>
              </Button>
            </div>
            <p className="text-xs text-text-secondary">
              {t('openMetadata.filtersApplyToConversation')}
            </p>
            {askMutation.error && (
              <p role="alert" className="text-sm text-state-error">
                {askMutation.error.message}
              </p>
            )}
          </form>
        </CardContent>
      </Card>

      <section
        className="space-y-6"
        aria-live="polite"
        aria-label={t('openMetadata.conversationTitle')}
      >
        {!history.length && (
          <p className="text-sm text-text-secondary">
            {t('openMetadata.emptyConversation')}
          </p>
        )}
        {history.map((answer, index) => (
          <div
            key={`${index}-${answer.question}`}
            className="space-y-4"
            data-testid="openmetadata-turn"
          >
            <Card>
              <CardHeader>
                <div className="flex flex-wrap items-center gap-2">
                  <Badge variant="secondary">{answer.intent}</Badge>
                  <Badge
                    variant={answer.freshness.stale ? 'destructive' : 'success'}
                  >
                    {answer.freshness.stale
                      ? t('openMetadata.snapshotAnswer')
                      : t('openMetadata.liveAnswer')}
                  </Badge>
                  {answer.context_applied && (
                    <Badge variant="outline">
                      {t('openMetadata.contextUsed')}
                    </Badge>
                  )}
                </div>
                <CardTitle as="h3" className="text-lg">
                  {answer.question}
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                <p className="whitespace-pre-wrap text-base leading-7">
                  {answer.answer}
                </p>
                {answer.quality && (
                  <div className="rounded-lg bg-bg-input p-3 text-sm">
                    {answer.quality.status === 'not_configured' && (
                      <AlertTriangle className="me-2 inline size-4 text-state-warning" />
                    )}
                    {answer.quality.message}
                    {!!answer.quality.test_cases?.length && (
                      <div className="mt-3">
                        <p className="font-medium">
                          {t('openMetadata.testCases')}
                        </p>
                        <ul className="mt-1 list-disc space-y-1 ps-5">
                          {answer.quality.test_cases.map((testCase) => (
                            <li key={testCase.id || testCase.fqn}>
                              {testCase.name || testCase.fqn}
                              {testCase.definition
                                ? ` — ${testCase.definition}`
                                : ''}
                              {testCase.status ? ` (${testCase.status})` : ''}
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}
                  </div>
                )}
                <LineageList
                  title={t('openMetadata.upstream')}
                  edges={answer.upstream}
                />
                <LineageList
                  title={t('openMetadata.downstream')}
                  edges={answer.downstream}
                />
                <ForeignKeyList edges={answer.foreign_keys} />
                <SemanticRelationshipList
                  edges={answer.semantic_relations}
                  truncated={answer.semantic_relations_truncated}
                />
                {answer.warnings?.map((warning) => (
                  <p key={warning} className="text-xs text-state-warning">
                    {warning}
                  </p>
                ))}
              </CardContent>
            </Card>

            {!!answer.entities?.length && (
              <div className="grid gap-3 lg:grid-cols-2">
                {answer.entities.map((entity) => (
                  <EntityCard
                    key={entity.id || entity.fqn}
                    entity={entity}
                    governanceAllowed={status.governance_allowed}
                    onGovern={setGovernanceEntity}
                    disabled={askMutation.isPending}
                    onSelect={
                      answer.needs_clarification
                        ? (selected) =>
                            ask({
                              value: answer.question,
                              selectedEntityId: selected.id,
                            })
                        : undefined
                    }
                  />
                ))}
              </div>
            )}
          </div>
        ))}
      </section>

      {governanceEntity && status.governance_allowed && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label={t('openMetadata.governanceTitle')}
          className="fixed inset-0 z-50 overflow-y-auto bg-black/50 p-4 sm:p-8"
        >
          <div className="mx-auto max-w-3xl">
            <GovernancePanel
              key={`${governanceEntity.id}-${governanceEntity.version}`}
              entity={governanceEntity}
              onClose={() => setGovernanceEntity(null)}
              onApplied={() => {
                queryClient.invalidateQueries({ queryKey: ['openmetadata'] });
              }}
            />
          </div>
        </div>
      )}
    </main>
  );
}
