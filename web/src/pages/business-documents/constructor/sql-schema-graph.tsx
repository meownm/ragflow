import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import type {
  SqlAgentProject,
  SqlQueryGraph,
} from '@/pages/business-documents/types';
import {
  fetchBusinessDocumentSqlGraph,
  fetchBusinessDocumentSqlProjectGraph,
  fetchBusinessDocumentSqlView,
  saveBusinessDocumentSqlView,
} from '@/services/business-document-service';
import {
  Background,
  Controls,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  applyNodeChanges,
  type Node,
  type NodeProps,
  type OnNodesChange,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import {
  Database,
  Expand,
  List,
  LoaderCircle,
  Plus,
  RefreshCw,
  Search,
} from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { SqlExportButton } from './sql-export-button';
import { graphPositions } from './sql-graph-layout';
import {
  SqlRelationshipEdge,
  type RelationshipEdge,
} from './sql-relationship-edge';
import './sql-schema-graph.css';

type GraphNode = SqlQueryGraph['nodes'][number];
type TableNode = Node<
  { table: GraphNode; change: 'ADDING' | 'REMOVING' | null },
  'sqlTable'
>;

function SqlTableNode({ data, selected }: NodeProps<TableNode>) {
  const { table, change } = data;
  const active = table.membership === 'QUERY';
  const columns = table.columns.filter(
    (column) => column.roles.length || column.constraint.includes('KEY'),
  );
  const visible = columns.length
    ? columns.slice(0, 8)
    : table.columns.slice(0, 4);
  return (
    <article
      className={`w-[250px] rounded-lg border-2 shadow-sm transition-[border-color,box-shadow] ${
        active
          ? 'border-accent-primary bg-bg-base text-text-primary'
          : 'sql-er-related border-border-button bg-bg-canvas text-text-secondary'
      } ${selected ? 'outline outline-2 outline-offset-4 outline-accent-primary' : ''} ${change ? 'border-dashed' : ''}`}
      data-testid="sql-er-table"
      data-membership={table.membership}
      data-entity-id={table.entity_id || ''}
      data-alias={table.alias}
      data-change={change || ''}
      aria-label={`${table.label}${table.alias ? ` · ${table.alias}` : ''} · ${active ? 'В запросе' : 'Связанная таблица'}`}
    >
      <Handle id="left-in" type="target" position={Position.Left} />
      <Handle id="right-in" type="target" position={Position.Right} />
      <div
        className={`border-b border-border-button px-3 py-2.5 ${active ? 'bg-accent-primary/10' : ''}`}
      >
        <div className="flex items-start justify-between gap-2">
          <span className="break-words text-sm font-semibold">
            {table.label}
          </span>
          <Database
            className={`size-3.5 shrink-0 ${active ? 'text-accent-primary' : 'text-text-secondary'}`}
          />
        </div>
        <p className="mt-0.5 break-all font-mono text-[11px]">
          {table.schema ? `${table.schema}.` : ''}
          {table.technical_name}
          {table.alias ? ` · ${table.alias}` : ''}
        </p>
        <p
          className={`mt-1 text-[11px] font-medium ${active ? 'text-text-primary' : 'text-text-secondary'}`}
        >
          {change === 'ADDING'
            ? 'Предлагается добавить'
            : change === 'REMOVING'
              ? 'Предлагается удалить'
              : active
                ? 'В запросе'
                : 'Не входит в запрос'}
          {table.kind !== 'TABLE' ? ` · ${table.kind}` : ''}
        </p>
      </div>
      <ul className="space-y-1 px-3 py-2 text-xs">
        {visible.map((column) => (
          <li
            key={column.id}
            className={`flex items-center justify-between gap-2 ${column.roles.length ? 'font-medium text-text-primary' : ''}`}
          >
            <span className="truncate">
              {column.constraint.includes('KEY') ? '◆ ' : ''}
              {column.name}
            </span>
            <span className="shrink-0 text-[9px] text-text-secondary">
              {column.roles.join(' · ') || column.data_type}
            </span>
          </li>
        ))}
      </ul>
      <Handle id="right-out" type="source" position={Position.Right} />
      <Handle id="left-out" type="source" position={Position.Left} />
    </article>
  );
}

const nodeTypes = { sqlTable: SqlTableNode };
const edgeTypes = { sqlRelationship: SqlRelationshipEdge };

export type SqlGraphChangeRequest = {
  text: string;
  entityIds: string[];
  nonce: number;
};

export function SqlSchemaGraph({
  project,
  sql,
  schema,
  candidateSql,
  viewLabel,
  readOnlyView = false,
  exportSource,
  onRequestChange,
}: {
  project: SqlAgentProject;
  sql: string;
  schema: Record<string, unknown> | null;
  candidateSql?: string;
  viewLabel?: string;
  readOnlyView?: boolean;
  exportSource?: { compilation_id: string; query_artifact_id: string };
  onRequestChange: (request: SqlGraphChangeRequest) => void;
}) {
  const accepted = schema?.accepted_schema as
    | Array<{ entity_id: string }>
    | undefined;
  const seedKey = [...new Set(accepted?.map((item) => item.entity_id) || [])]
    .sort()
    .join('\n');
  const [graph, setGraph] = useState<SqlQueryGraph | null>(null);
  const [candidate, setCandidate] = useState<SqlQueryGraph | null>(null);
  const [nodes, setNodes] = useState<TableNode[]>([]);
  const [depth, setDepth] = useState(1);
  const [onlyQuery, setOnlyQuery] = useState(false);
  const [listView, setListView] = useState(false);
  const [search, setSearch] = useState('');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedEdge, setSelectedEdge] = useState<string | null>(null);
  const [selectedRoute, setSelectedRoute] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [viewReload, setViewReload] = useState(0);
  const [fullscreen, setFullscreen] = useState(false);
  const generation = useRef(0);
  const [showAllFields, setShowAllFields] = useState(false);
  const [viewReady, setViewReady] = useState(false);
  const [viewLoaded, setViewLoaded] = useState(false);
  const [hasSavedView, setHasSavedView] = useState(false);
  const [viewState, setViewState] = useState('');
  const [viewport, setViewport] = useState({ x: 0, y: 0, zoom: 1 });
  const savedPositions = useRef<Record<string, { x: number; y: number }>>({});
  const viewVersion = useRef(0);
  const viewQueue = useRef<Promise<void>>(Promise.resolve());
  const requestLifetime = useRef<{
    projectId: string;
    controller: AbortController;
  } | null>(null);
  const historicalCompilation = readOnlyView
    ? exportSource?.compilation_id
    : undefined;
  const historicalQueryId = readOnlyView
    ? exportSource?.query_artifact_id
    : undefined;
  const hasSeed = Boolean(seedKey || historicalCompilation);

  useEffect(() => {
    let lifetime = { projectId: project.id, controller: new AbortController() };
    requestLifetime.current = lifetime;
    const abort = () => lifetime.controller.abort();
    const restore = (event: PageTransitionEvent) => {
      if (!event.persisted) return;
      lifetime = { projectId: project.id, controller: new AbortController() };
      requestLifetime.current = lifetime;
      setReload((current) => current + 1);
      setViewReload((current) => current + 1);
    };
    window.addEventListener('pagehide', abort);
    window.addEventListener('pageshow', restore);
    return () => {
      abort();
      window.removeEventListener('pagehide', abort);
      window.removeEventListener('pageshow', restore);
      if (requestLifetime.current === lifetime) requestLifetime.current = null;
    };
  }, [project.id]);

  useEffect(() => {
    let active = true;
    const lifetime = requestLifetime.current;
    setViewReady(false);
    setViewLoaded(false);
    setHasSavedView(false);
    savedPositions.current = {};
    if (!hasSeed) return;
    void fetchBusinessDocumentSqlView(project.id, lifetime?.controller.signal)
      .then((saved) => {
        if (!active || lifetime?.controller.signal.aborted) return;
        viewVersion.current = saved.version;
        setHasSavedView(Boolean(saved.view));
        if (saved.view) {
          savedPositions.current = saved.view.positions;
          setNodes((current) =>
            current.map((node) => ({
              ...node,
              position: saved.view!.positions[node.id] || node.position,
            })),
          );
          setDepth(saved.view.depth);
          setOnlyQuery(saved.view.mode === 'query');
          setListView(saved.view.list_view);
          setViewport(saved.view.viewport);
        }
        setViewReady(true);
        setViewLoaded(true);
        setViewState('');
      })
      .catch(() => {
        if (active && !lifetime?.controller.signal.aborted) {
          setViewLoaded(true);
          setViewState(
            'Не удалось загрузить расположение. Локальные изменения вида не сохраняются.',
          );
        }
      });
    return () => {
      active = false;
    };
  }, [project.id, hasSeed, viewReload]);

  const positionsKey = JSON.stringify(
    Object.fromEntries(nodes.map((node) => [node.id, node.position])),
  );
  const viewportKey = JSON.stringify(viewport);
  useEffect(() => {
    if (!viewReady || !graph || loading) return;
    const lifetime = requestLifetime.current;
    if (!lifetime || lifetime.projectId !== project.id) return;
    let active = true;
    setViewState('Сохранение вида…');
    const timer = window.setTimeout(() => {
      const view = {
        positions: JSON.parse(positionsKey),
        viewport: JSON.parse(viewportKey),
        depth,
        mode: onlyQuery ? ('query' as const) : ('all' as const),
        list_view: listView,
      };
      viewQueue.current = viewQueue.current
        .catch(() => {})
        .then(async () => {
          if (
            !active ||
            requestLifetime.current !== lifetime ||
            lifetime.controller.signal.aborted
          )
            return;
          try {
            const saved = await saveBusinessDocumentSqlView(
              project.id,
              viewVersion.current,
              view,
              lifetime.controller.signal,
            );
            if (
              requestLifetime.current !== lifetime ||
              lifetime.controller.signal.aborted
            )
              return;
            viewVersion.current = saved.version;
            if (active) setViewState('Вид сохранён');
          } catch (failure) {
            if (
              active &&
              requestLifetime.current === lifetime &&
              !lifetime.controller.signal.aborted
            ) {
              setViewReady(false);
              setViewState(
                failure instanceof Error
                  ? failure.message
                  : 'Не удалось сохранить расположение.',
              );
            }
          }
        });
    }, 800);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [
    viewReady,
    graph,
    loading,
    positionsKey,
    viewportKey,
    depth,
    onlyQuery,
    listView,
    project.id,
  ]);

  useEffect(() => {
    if (!fullscreen) return;
    const close = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setFullscreen(false);
    };
    window.addEventListener('keydown', close);
    return () => window.removeEventListener('keydown', close);
  }, [fullscreen]);

  useEffect(() => {
    const current = ++generation.current;
    const signal = requestLifetime.current?.controller.signal;
    setGraph(null);
    setCandidate(null);
    setSelectedId(null);
    setSelectedEdge(null);
    if (!hasSeed) return;
    setLoading(true);
    setError(null);
    const input = {
      schema_version: '1' as const,
      entity_ids: seedKey.split('\n'),
      sql,
      depth,
    };
    const request =
      historicalCompilation && historicalQueryId
        ? fetchBusinessDocumentSqlProjectGraph(
            project.id,
            {
              compilation_id: historicalCompilation,
              query_artifact_id: historicalQueryId,
            },
            depth,
            signal,
          )
        : fetchBusinessDocumentSqlGraph(input, signal);
    void request
      .then(async (value) => {
        if (generation.current !== current) return;
        setGraph(value);
        if (candidateSql && candidateSql !== sql) {
          const preview = await fetchBusinessDocumentSqlGraph(
            {
              ...input,
              sql: candidateSql,
            },
            signal,
          );
          if (generation.current === current) setCandidate(preview);
        }
      })
      .catch((nextError) => {
        if (generation.current === current && !signal?.aborted)
          setError(
            nextError instanceof Error
              ? nextError.message
              : 'Схема недоступна.',
          );
      })
      .finally(() => {
        if (generation.current === current) setLoading(false);
      });
    return () => {
      generation.current += 1;
    };
  }, [
    project.id,
    seedKey,
    sql,
    depth,
    candidateSql,
    reload,
    historicalCompilation,
    historicalQueryId,
    hasSeed,
  ]);

  const projectedNodes = useMemo(() => {
    if (!graph) return [];
    const currentKeys = new Set(
      graph.nodes
        .filter((node) => node.membership === 'QUERY')
        .map((node) => node.id),
    );
    const proposedKeys = new Set(
      candidate?.nodes
        .filter((node) => node.membership === 'QUERY')
        .map((node) => node.id),
    );
    const additions =
      candidate?.nodes.filter(
        (node) => node.membership === 'QUERY' && !currentKeys.has(node.id),
      ) || [];
    return [
      ...graph.nodes.filter(
        (node) =>
          node.membership === 'QUERY' ||
          !additions.some(
            (addition) =>
              addition.entity_id && addition.entity_id === node.entity_id,
          ),
      ),
      ...additions.filter(
        (addition) => !graph.nodes.some((node) => node.id === addition.id),
      ),
    ].map((table) => {
      const key = table.id;
      const change =
        candidate && table.membership === 'QUERY'
          ? !currentKeys.has(key)
            ? ('ADDING' as const)
            : !proposedKeys.has(key)
              ? ('REMOVING' as const)
              : null
          : null;
      return {
        table:
          change === 'ADDING'
            ? { ...table, membership: 'RELATED' as const }
            : table,
        change,
      };
    });
  }, [graph, candidate]);

  const projectedEdges = useMemo(() => {
    const same = (
      left: SqlQueryGraph['edges'][number],
      right: SqlQueryGraph['edges'][number],
    ) =>
      left.id === right.id &&
      left.condition === right.condition &&
      left.join_type === right.join_type;
    return [
      ...(graph?.edges || []).map((edge) => ({
        ...edge,
        change:
          candidate &&
          edge.kind !== 'FOREIGN_KEY' &&
          !candidate.edges.some((item) => same(edge, item))
            ? 'REMOVING'
            : null,
      })),
      ...(candidate?.edges || [])
        .filter(
          (edge) =>
            edge.kind !== 'FOREIGN_KEY' &&
            !graph?.edges.some((item) => same(edge, item)),
        )
        .map((edge) => ({
          ...edge,
          id: `proposal:${edge.id}`,
          change: 'ADDING',
        })),
    ];
  }, [graph, candidate]);

  useEffect(() => {
    const defaults = graphPositions({
      nodes: projectedNodes.map((node) => node.table),
      edges: projectedEdges,
    });
    setNodes((previous) =>
      projectedNodes.map((data) => ({
        id: data.table.id,
        type: 'sqlTable',
        data,
        position:
          previous.find((node) => node.id === data.table.id)?.position ||
          savedPositions.current[data.table.id] ||
          defaults[data.table.id],
      })),
    );
  }, [projectedNodes, projectedEdges]);

  const onNodesChange: OnNodesChange<TableNode> = useCallback(
    (changes) =>
      setNodes((current) => {
        const next = applyNodeChanges(changes, current);
        for (const node of next) {
          savedPositions.current[node.id] = { ...node.position };
        }
        return next;
      }),
    [],
  );
  const visibleNodes = nodes.filter(
    (node) =>
      (!onlyQuery || node.data.table.membership === 'QUERY') &&
      (!search ||
        `${node.data.table.label} ${node.data.table.technical_name} ${node.data.table.schema} ${node.data.table.alias}`
          .toLocaleLowerCase()
          .includes(search.toLocaleLowerCase())),
  );
  const visibleIds = new Set(visibleNodes.map((node) => node.id));
  const graphEdges = projectedEdges.filter(
    (edge) =>
      visibleIds.has(edge.from) &&
      visibleIds.has(edge.to) &&
      (!onlyQuery || edge.kind !== 'FOREIGN_KEY'),
  );
  const groups = new Map<string, string[]>();
  const pairKey = (edge: (typeof graphEdges)[number]) =>
    [edge.from, edge.to].sort().join('|');
  for (const edge of graphEdges)
    groups.set(pairKey(edge), [...(groups.get(pairKey(edge)) || []), edge.id]);
  const positionById = new Map(nodes.map((node) => [node.id, node.position]));
  const edges: RelationshipEdge[] = graphEdges.map((edge) => {
    const siblings = groups.get(pairKey(edge))!;
    const forward =
      (positionById.get(edge.to)?.x || 0) >=
      (positionById.get(edge.from)?.x || 0);
    const relationshipCaption =
      edge.kind === 'JOIN'
        ? `${edge.join_type} JOIN`
        : edge.kind === 'LINEAGE'
          ? 'SQL'
          : `FK · ${edge.column_pairs.map((pair) => pair.from).join(', ')}${edge.cardinality === 'UNKNOWN' ? ' · ?' : ''}`;
    const caption =
      edge.change === 'ADDING'
        ? `Добавить · ${relationshipCaption}`
        : edge.change === 'REMOVING'
          ? `Убрать · ${relationshipCaption}`
          : relationshipCaption;
    return {
      id: edge.id,
      type: 'sqlRelationship',
      source: edge.from,
      target: edge.to,
      sourceHandle: forward ? 'right-out' : 'left-out',
      targetHandle: forward ? 'left-in' : 'right-in',
      data: {
        caption,
        offset: (siblings.indexOf(edge.id) - (siblings.length - 1) / 2) * 48,
        onSelect: (id: string) => {
          setSelectedEdge(id);
          setSelectedId(null);
        },
      },
      markerEnd: {
        type: MarkerType.ArrowClosed,
        color:
          edge.kind === 'JOIN'
            ? 'rgb(var(--accent-primary))'
            : 'rgb(var(--text-secondary))',
      },
      style:
        edge.kind === 'JOIN'
          ? {
              stroke: 'rgb(var(--accent-primary))',
              strokeWidth: 2.5,
              strokeDasharray: edge.change ? '6 4' : undefined,
            }
          : {
              stroke: 'rgb(var(--text-secondary))',
              strokeWidth: 1,
              strokeDasharray: edge.kind === 'LINEAGE' ? '5 4' : '3 3',
              opacity: 0.55,
            },
      selected: edge.id === selectedEdge,
    };
  });
  const selected = projectedNodes.find(
    (node) => node.table.id === selectedId,
  )?.table;
  const selectedChange = projectedNodes.find(
    (node) => node.table.id === selectedId,
  )?.change;
  const edge = projectedEdges.find((item) => item.id === selectedEdge);
  const routes = selected?.entity_id
    ? graph?.paths?.[selected.entity_id] || []
    : [];
  useEffect(() => {
    setSelectedRoute(routes.length === 1 ? '0' : '');
  }, [selectedId, graph, routes.length]);
  const route =
    selectedRoute === '' ? undefined : routes[Number(selectedRoute)];
  const routeEntities =
    route?.entity_ids.filter((id) => !seedKey.split('\n').includes(id)) || [];
  const routeLabel = (option: (typeof routes)[number]) =>
    option.entity_ids
      .map(
        (id) =>
          graph?.tables.find((table) => table.id === id)?.technical_name || id,
      )
      .join(' → ') +
    ' · ' +
    option.relationship_ids
      .map((id) =>
        graph?.relationships
          ?.find((item) => item.id === id)
          ?.column_pairs.map((pair) => `${pair.from}=${pair.to}`)
          .join(', '),
      )
      .filter(Boolean)
      .join('; ');
  const canRequest =
    !readOnlyView &&
    project.capabilities?.edit !== false &&
    project.operation_state === 'IDLE' &&
    (selected?.membership !== 'RELATED' || Boolean(route)) &&
    seedKey.split('\n').length + routeEntities.length <= 8;
  const routeExplanation = route
    ? ` Используй выбранный путь: ${routeLabel(route)}.`
    : '';
  const requestChange = (text: string, entityIds: string[] = []) =>
    onRequestChange({ text, entityIds, nonce: Date.now() });

  if (!hasSeed) return null;
  return (
    <section
      aria-label="Сущности и ER-диаграмма"
      data-testid="sql-schema-graph"
      className={`${fullscreen ? 'fixed inset-3 z-50 overflow-auto bg-bg-base p-5 shadow-xl' : 'mb-6 border-y border-border-button py-4'} min-w-0`}
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 className="text-base font-semibold">Сущности и связи</h3>
          {viewLabel && (
            <p role="status" className="mt-1 text-xs font-medium">
              {viewLabel} · просмотр сохранённого SQL
            </p>
          )}
          <p className="text-xs text-text-secondary">
            Таблицы запроса выделены. Доступные соседи показаны приглушённо.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {exportSource &&
            ['svg', 'json'].map((format) => (
              <SqlExportButton
                key={format}
                projectId={project.id}
                stateVersion={project.state_version}
                command={{
                  schema_version: '1',
                  kind: 'GRAPH',
                  format: format as 'svg' | 'json',
                  ...exportSource,
                  ...(candidateSql &&
                  project.pending_proposal?.kind === 'REVISION'
                    ? { proposal_id: project.pending_proposal.id }
                    : {}),
                  view: {
                    positions: Object.fromEntries(
                      nodes.map((node) => [node.id, node.position]),
                    ),
                    viewport,
                    mode: onlyQuery ? 'query' : 'all',
                    depth,
                    list_view: listView,
                  },
                }}
                disabled={loading || !graph}
                label={`Скачать ER ${format.toUpperCase()}`}
              />
            ))}
          <Button
            size="sm"
            variant="ghost"
            onClick={() => setListView((value) => !value)}
          >
            <List className="size-4" />
            {listView ? 'Диаграмма' : 'Список'}
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => setFullscreen((value) => !value)}
          >
            <Expand className="size-4" />
            {fullscreen ? 'Свернуть' : 'Развернуть'}
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => setReload((value) => value + 1)}
            disabled={loading}
          >
            <RefreshCw className="size-4" />
            Обновить схему
          </Button>
        </div>
      </div>
      <div className="my-3 flex flex-wrap items-center gap-3">
        <label className="relative min-w-40 flex-1">
          <Search className="absolute left-2 top-2.5 size-4 text-text-secondary" />
          <Input
            aria-label="Поиск таблицы на диаграмме"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            className="pl-8"
            placeholder="Таблица, схема или псевдоним"
          />
        </label>
        <label className="flex items-center gap-2 text-xs">
          <input
            type="checkbox"
            checked={onlyQuery}
            onChange={(event) => setOnlyQuery(event.target.checked)}
          />
          Только запрос
        </label>
        <label className="text-xs">
          Глубина связей{' '}
          <select
            aria-label="Глубина связанных таблиц"
            className="ml-2 rounded border border-border-button bg-bg-base p-1"
            value={depth}
            onChange={(event) => setDepth(Number(event.target.value))}
          >
            {[1, 2, 3].map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </label>
      </div>
      <div
        className="mb-3 flex flex-wrap gap-4 text-xs text-text-secondary"
        aria-label="Легенда диаграммы"
      >
        <span className="font-semibold text-text-primary">
          <span className="text-accent-primary" aria-hidden="true">
            ━━{' '}
          </span>
          В запросе / JOIN
        </span>
        <span>┄ Связанные таблицы / FK</span>
        <span>Пунктирная рамка — предложение</span>
      </div>
      {error && (
        <div role="alert" className="mb-3 text-sm text-state-error">
          {error} Состав запроса сохранён.
        </div>
      )}
      {loading && (
        <p role="status" className="flex items-center gap-2 text-sm">
          <LoaderCircle className="size-4 animate-spin" />
          Загрузка схемы…
        </p>
      )}
      {graph?.freshness?.stale && (
        <p role="status" className="mb-2 text-xs text-state-warning">
          Метаданные устарели. Перед выполнением необходима проверка источника.
        </p>
      )}
      {graph?.truncated && (
        <p role="status" className="mb-2 text-xs">
          Показана ограниченная часть графа. Уточните исходные таблицы.
        </p>
      )}
      <div className="grid min-w-0 gap-3 xl:grid-cols-[minmax(0,1fr)_280px]">
        {listView ? (
          <div className="max-h-[580px] overflow-auto border border-border-button">
            <table className="w-full text-left text-xs">
              <thead>
                <tr>
                  <th className="p-2">Таблица / псевдоним</th>
                  <th className="p-2">Участие</th>
                  <th className="p-2">Действие</th>
                </tr>
              </thead>
              <tbody>
                {visibleNodes.map(({ data: { table }, id }) => (
                  <tr key={id} className="border-t border-border-button">
                    <td className="p-2">
                      {table.schema}.{table.technical_name} {table.alias}
                    </td>
                    <td className="p-2">
                      {table.membership === 'QUERY'
                        ? 'В запросе'
                        : 'Не входит в запрос'}
                    </td>
                    <td className="p-2">
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => setSelectedId(id)}
                      >
                        Посмотреть поля
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div
            className="sql-er-canvas h-[580px] min-w-0 rounded border border-border-button bg-bg-canvas"
            data-testid="sql-er-canvas"
          >
            {viewLoaded && graph && (
              <ReactFlow<TableNode>
                nodes={visibleNodes}
                edges={edges}
                nodeTypes={nodeTypes}
                edgeTypes={edgeTypes}
                onNodesChange={onNodesChange}
                onMoveEnd={(_, position) => {
                  setViewport(position);
                  setHasSavedView(true);
                }}
                onNodeClick={(_, node) => {
                  setSelectedId(node.id);
                  setSelectedEdge(null);
                }}
                onEdgeClick={(_, item) => {
                  setSelectedEdge(item.id);
                  setSelectedId(null);
                }}
                defaultViewport={viewport}
                fitView={!hasSavedView}
                fitViewOptions={{ padding: 0.12, maxZoom: 1 }}
                minZoom={0.15}
                maxZoom={2}
                nodesConnectable={false}
                deleteKeyCode={null}
              >
                <Background gap={24} />
                <Controls showInteractive={false} />
              </ReactFlow>
            )}
          </div>
        )}
        <aside
          className="min-w-0 border-l border-border-button pl-3 text-xs"
          aria-label="Свойства выбранной сущности"
        >
          {selected ? (
            <>
              <h4 className="break-words text-sm font-semibold">
                {selected.label}
              </h4>
              <p className="mb-2 break-all text-text-secondary">
                {selected.schema}.{selected.technical_name} {selected.alias}
              </p>
              <p className="mb-3">
                {selectedChange === 'ADDING'
                  ? 'Предлагается добавить. Пока не входит в текущий запрос.'
                  : selectedChange === 'REMOVING'
                    ? 'Входит в текущий запрос. Предлагается удалить.'
                    : selected.membership === 'QUERY'
                      ? 'Входит в текущий запрос'
                      : 'Доступна по связи. Пока не входит в запрос.'}
              </p>
              {selected.membership === 'RELATED' && routes.length > 0 && (
                <label className="mb-3 block">
                  Путь соединения
                  <select
                    aria-label="Путь соединения связанной таблицы"
                    className="mt-1 w-full rounded border border-border-button bg-bg-base p-2"
                    value={selectedRoute}
                    onChange={(event) => setSelectedRoute(event.target.value)}
                  >
                    <option value="">Выберите связь</option>
                    {routes.map((option, index) => (
                      <option
                        key={option.relationship_ids.join(':')}
                        value={String(index)}
                      >
                        {routeLabel(option)}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              {selected.membership === 'RELATED' && routes.length === 0 && (
                <p className="mb-3 text-text-secondary">
                  Подтверждённый путь к текущему запросу не найден. Раскройте
                  следующие уровни связей.
                </p>
              )}
              {graph?.paths_truncated && (
                <p className="mb-3 text-text-secondary">
                  Количество маршрутов ограничено. Уточните исходную таблицу.
                </p>
              )}
              {project.artifact_ids.query && (
                <Button
                  size="sm"
                  variant="outline"
                  disabled={!canRequest}
                  onClick={() =>
                    requestChange(
                      selected.membership === 'QUERY'
                        ? `Убери из запроса таблицу ${selected.schema}.${selected.technical_name}${selected.alias ? ` с псевдонимом ${selected.alias}` : ''}. Покажи зависимые поля и условия, сохрани незатронутые требования.`
                        : `Добавь в запрос таблицу ${selected.schema}.${selected.technical_name}.${routeExplanation} Объясни влияние на детализацию и сохрани остальные условия.`,
                      selected.membership === 'RELATED' && selected.entity_id
                        ? routeEntities
                        : [],
                    )
                  }
                >
                  <Plus className="size-3.5" />
                  {selected.membership === 'QUERY'
                    ? 'Обсудить удаление'
                    : 'Добавить в запрос'}
                </Button>
              )}
              <label className="my-3 flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={showAllFields}
                  onChange={(event) => setShowAllFields(event.target.checked)}
                />
                Все поля
              </label>
              <ul className="max-h-80 space-y-2 overflow-auto">
                {selected.columns
                  .filter(
                    (column) =>
                      showAllFields ||
                      column.roles.length ||
                      column.constraint.includes('KEY'),
                  )
                  .map((column) => (
                    <li
                      key={column.id}
                      className="border-b border-border-button pb-2"
                    >
                      <p className="break-all font-mono">
                        {column.name}{' '}
                        <span className="text-text-secondary">
                          {column.data_type}
                        </span>
                      </p>
                      <p className="text-accent-primary">
                        {column.roles.join(' · ')}
                      </p>
                      {project.artifact_ids.query && (
                        <div className="mt-1 flex gap-2">
                          <button
                            className="underline"
                            disabled={!canRequest}
                            onClick={() =>
                              requestChange(
                                `Добавь поле ${selected.alias || selected.technical_name}.${column.name} в результат, сохрани остальные условия.${routeExplanation}`,
                                routeEntities,
                              )
                            }
                          >
                            В результат
                          </button>
                          <button
                            className="underline"
                            disabled={!canRequest}
                            onClick={() =>
                              requestChange(
                                `Добавь условие для поля ${selected.alias || selected.technical_name}.${column.name}. Уточни оператор и значение.${routeExplanation}`,
                                routeEntities,
                              )
                            }
                          >
                            В фильтр
                          </button>
                        </div>
                      )}
                    </li>
                  ))}
              </ul>
            </>
          ) : edge ? (
            <>
              <h4 className="text-sm font-semibold">
                {edge.change === 'ADDING'
                  ? 'Предлагается добавить: '
                  : edge.change === 'REMOVING'
                    ? 'Предлагается удалить: '
                    : ''}
                {edge.kind === 'JOIN'
                  ? `${edge.join_type} JOIN`
                  : edge.kind === 'LINEAGE'
                    ? 'Происхождение данных SQL'
                    : 'Внешний ключ каталога'}
              </h4>
              <pre className="my-3 whitespace-pre-wrap break-all font-mono">
                {edge.condition ||
                  edge.column_pairs
                    .map((pair) => `${pair.from} → ${pair.to}`)
                    .join('\n')}
              </pre>
              <p>Источник: {edge.source}</p>
              <p>
                Кратность:{' '}
                {edge.cardinality === 'UNKNOWN'
                  ? 'неизвестна'
                  : edge.cardinality}
              </p>
              {edge.kind === 'JOIN' && (
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={readOnlyView}
                  onClick={() =>
                    requestChange(
                      `Объясни соединение ${edge.join_type} JOIN с условием ${edge.condition}. Не изменяй запрос.`,
                    )
                  }
                >
                  Объяснить связь
                </Button>
              )}
            </>
          ) : (
            <p className="text-text-secondary">
              Выберите таблицу или связь. Просмотр и перемещение элементов не
              меняют SQL.
            </p>
          )}
        </aside>
      </div>
      {graph?.warnings.map((warning) => (
        <p key={warning} className="mt-2 text-xs text-text-secondary">
          {warning}
        </p>
      ))}
      {viewState && (
        <p role="status" className="mt-2 text-xs text-text-secondary">
          {viewState}
        </p>
      )}
      {viewLoaded && !viewReady && (
        <Button
          size="sm"
          variant="outline"
          onClick={() => setViewReload((current) => current + 1)}
        >
          Загрузить сохранённый вид
        </Button>
      )}
    </section>
  );
}
