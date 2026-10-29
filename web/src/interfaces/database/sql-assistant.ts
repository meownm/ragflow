import type {
  SqlAgentProject,
  SqlHistoryContext,
  SqlQueryCompileResponse,
  SqlQueryPlanProposal,
  SqlResultChecks,
} from '@/pages/business-documents/types';

export type AssistantFilterTree =
  | { filter_id: string }
  | { operator: 'AND' | 'OR'; children: AssistantFilterTree[] };

export type AssistantAggregateFilter = Omit<
  SqlQueryPlanProposal['filters'][number],
  'column_id'
> & { select_item_id: string; decision: 'user' };

export interface AssistantQuery extends SqlQueryPlanProposal {
  row_filter_tree?: AssistantFilterTree | null;
  aggregate_filters?: AssistantAggregateFilter[];
  aggregate_filter_tree?: AssistantFilterTree | null;
  timezone?: string;
}

export type DefinitionFreshness =
  | 'CURRENT'
  | 'SUPERSEDED'
  | 'RETIRED'
  | 'METADATA_CHANGED'
  | 'UNKNOWN'
  | 'NOT_APPLICABLE';
export interface AssistantDefinition {
  definition_id: string;
  version: number;
  version_id: string;
  content_hash: string;
  title: string;
  description: string;
  approved_by: string;
  approved_at: number;
  freshness: DefinitionFreshness;
  checks: string[];
  contract: {
    grain: string[];
    metrics: Array<{
      select_item_id: string;
      unit: string;
      null_policy: string;
      reaggregation: 'FORBIDDEN';
    }>;
    time_column_id: string | null;
    timezone: string;
    mutable_parameters: string[];
  };
}

export interface AssistantView {
  appearance?: {
    display: 'table' | 'chart' | 'both';
    density: 'comfortable' | 'compact';
    striped: boolean;
    wrap: boolean;
  };
  metrics?: AssistantResultMetric[];
  column_order: string[];
  hidden: string[];
  labels: Record<string, string>;
  formats: Record<string, 'raw' | 'number' | 'currency' | 'percent'>;
  filters: Array<{ column_id: string; operator: string; value: unknown }>;
  sort: { column_id: string; direction: 'ASC' | 'DESC' } | null;
  chart: { kind: 'bar' | 'line'; x: string; y: string } | null;
}

export interface AssistantResultMetric {
  id: string;
  title: string;
  kind: 'value' | 'rows' | 'sum' | 'avg' | 'min' | 'max' | 'count' | 'distinct';
  column_id: string | null;
  row_index: number | null;
}

export interface AssistantDialogueContext {
  run_id: string;
  query_artifact_id: string;
  compilation_id: string;
  sql_hash: string;
  parameters_hash: string;
  timezone: string | null;
  view_revision: number;
}

export type AssistantReference =
  | { kind: 'TABLE'; id: string; title: string; fqn?: string }
  | {
      kind: 'DEFINITION';
      id: string;
      title: string;
      version: number;
      content_hash: string;
      description?: string;
      freshness: DefinitionFreshness;
    };

export interface AssistantDialogueOptions {
  references: AssistantReference[];
  metrics: Extract<AssistantReference, { kind: 'DEFINITION' }>[];
}

export type AssistantCard =
  | {
      kind: 'RESULT';
      run_id: string;
      query_artifact_id: string;
      created_at: number;
      expires_at: number;
      context: AssistantDialogueContext;
      parameters: Record<string, unknown>;
    }
  | { kind: 'SOURCE'; entity_id: string; title: string }
  | {
      kind: 'QUERY_CHANGE';
      proposal_id: string;
      before_sql: string;
      after_sql: string;
      summary: string;
    };

export interface AssistantSession {
  cards?: AssistantCard[];
  id: string;
  revision: number;
  mode: 'EXPLORATORY' | 'AGREED';
  project: SqlAgentProject;
  execution_timezone?: string | null;
  messages: Array<{
    id: string;
    text: string;
    created_at: number;
    parent_result_id: string | null;
    answer?: {
      status: 'READY' | 'OTHER_RESULT' | 'UNAVAILABLE';
      kind?: 'ANSWER' | 'OVERVIEW' | 'NEEDS_QUERY' | 'CLARIFICATION';
      text: string;
      citations?: Array<{
        row_index: number;
        column_id: string;
        column: string;
        value: string;
      }>;
      scope?: 'LIMITED' | 'SAVED_RESULT';
      context_limited?: boolean;
      expires_at?: number;
    };
  }>;
  selected_result_id: string | null;
  draft: {
    base_query_artifact_id: string | null;
    query:
      | AssistantQuery
      | {
          mode: 'manual';
          sql: string;
          parameters: Array<{ name: string; type: string; value: unknown }>;
          timezone?: string;
        };
    compiled: SqlQueryCompileResponse;
    revision: number;
    mode?: 'EXPLORATORY' | 'AGREED';
    definition_ref?: Pick<
      AssistantDefinition,
      'definition_id' | 'version' | 'version_id' | 'content_hash'
    > | null;
    definition?: AssistantDefinition | null;
  } | null;
  structured_query: AssistantQuery | null;
  definition_status: 'NOT_VALIDATED' | 'DEFINITION_VALIDATED';
  definition_freshness?: DefinitionFreshness;
  definition?: AssistantDefinition | null;
  source_completeness: 'UNKNOWN' | 'COMPLETE' | 'PARTIAL' | 'STALE';
}

export interface AssistantResult {
  metrics?: Array<{
    id: string;
    title: string;
    kind: AssistantResultMetric['kind'];
    value: unknown;
    status:
      | 'OK'
      | 'NULL'
      | 'NON_NUMERIC'
      | 'FILTERED_OUT'
      | 'REQUIRES_QUERY'
      | 'OUT_OF_RANGE';
    rounded: boolean;
  }>;
  metric_aggregation_allowed?: boolean;
  execution_timezone?: string | null;
  dialogue_context?: AssistantDialogueContext;
  run_id: string;
  compilation_id: string;
  query_artifact_id: string;
  sql: string;
  sql_hash: string;
  parameters: Record<string, unknown>;
  columns: Array<{ id: string; name: string; drillable?: boolean }>;
  rows: Array<{ index: number; values: unknown[] }>;
  offset: number;
  filtered_count: number;
  row_count: number;
  duration_ms: number;
  created_at: number;
  expires_at: number;
  checks: SqlResultChecks;
  history: SqlHistoryContext;
  view: AssistantView;
  view_revision: number;
  view_transfer_removed?: number;
  execution_status: string;
  output_completeness: 'FULL' | 'LIMITED';
  source_completeness: 'UNKNOWN' | 'COMPLETE' | 'PARTIAL' | 'STALE';
  definition_status: 'NOT_VALIDATED' | 'DEFINITION_VALIDATED';
  definition_freshness?: DefinitionFreshness;
  definition?: AssistantDefinition | null;
  catalog_changed: boolean;
}

export interface AssistantEffect {
  kind: string;
  run_id?: string;
  data?: unknown;
  scope?: string;
}

export interface AssistantSessionList {
  sessions: Array<{
    id: string;
    title: string;
    revision: number;
    updated_at: number;
  }>;
}
