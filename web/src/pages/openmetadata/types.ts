export type Freshness = {
  snapshot_at?: string | null;
  latest_entity_updated_at?: string | null;
  checked_at?: string;
  catalog_checked_at?: string;
  age_hours?: number | null;
  stale: boolean;
  threshold_hours: number;
};

export type CatalogEntity = {
  id: string;
  type: string;
  name: string;
  display_name?: string | null;
  technical_name: string;
  fqn: string;
  description: string | null;
  version?: number;
  updated_at?: string | null;
  updated_by?: string;
  service: string;
  schema: string;
  database: string;
  owners: string[];
  domains: string[];
  tags: string[];
  glossary_terms?: string[];
  columns?: string[];
  column_details?: Array<{
    name: string;
    fqn: string;
    data_type: string;
    description: string;
    constraint: string;
    glossary_terms: string[];
  }>;
  table_constraints?: Array<{
    constraint_type: string;
    columns: string[];
    referred_columns: string[];
    relationship_type?: string;
  }>;
  matched_columns?: string[];
  column_count: number;
  described_column_count: number;
  url: string;
  score?: number;
  matched_by?: string[];
};

export type LineageEdge = {
  from: Partial<CatalogEntity> & { id: string };
  to: Partial<CatalogEntity> & { id: string };
  source: string;
  relationship_type: 'lineage';
  column_lineage?: Array<{ from_columns: string[]; to_column: string }>;
  pipeline?: unknown;
};

export type ForeignKeyEdge = {
  from: CatalogEntity;
  to: CatalogEntity;
  source: string;
  relationship_type: 'foreign_key';
  from_columns: string[];
  to_columns: string[];
  cardinality?: string;
};

export type SemanticRelationship = {
  from: CatalogEntity;
  to: CatalogEntity;
  source: string;
  relationship_type: 'semantic';
  shared_terms: string[];
};

export type KnowledgeGraph = {
  source: string;
  nodes: Array<{
    id: string;
    type: string;
    name: string;
    fqn: string;
    description: string;
  }>;
  edges: Array<{ from: string; to: string; label: string }>;
};

export type CatalogStatus = {
  connected: boolean;
  version?: string;
  base_url: string;
  write_enabled: boolean;
  governance_allowed: boolean;
  freshness: Freshness;
  capabilities: Record<string, number | null>;
  knowledge_graph: {
    enabled: boolean;
    storage_type?: string | null;
    inference?: Record<string, unknown>;
  };
  warnings: string[];
  agents: Array<{ id: string; mode: 'read' | 'write'; description: string }>;
};

export type StarterQuestion = {
  id: string;
  agent: string;
  question: string;
  reason: string;
  action: CatalogAction;
};

export type CatalogAction = {
  type: 'missing_descriptions' | 'domain' | 'impact' | 'recent' | 'quality';
  domain?: string;
  entity_id?: string;
};

export type CatalogContextTurn = {
  question: string;
  entity_ids: string[];
};

export type CatalogFilters = {
  owner?: string;
  domain?: string;
  service?: string;
  tag?: string;
  has_description?: boolean;
};

export type CatalogEntitiesResponse = {
  entities: CatalogEntity[];
  total_matches: number;
  total_visible_candidates: number;
  limit: number;
  offset: number;
  freshness: Freshness;
  warnings: string[];
  retrieval: string;
};

export type StarterQuestionsResponse = {
  questions: StarterQuestion[];
  freshness: Freshness;
  capabilities: Record<string, number | null>;
  warnings: string[];
};

export type CatalogAnswer = {
  agent: string;
  intent: string;
  question: string;
  answer: string;
  freshness: Freshness;
  warnings?: string[];
  entities?: CatalogEntity[];
  entity?: CatalogEntity;
  nodes?: Array<Partial<CatalogEntity> & { id: string }>;
  upstream?: LineageEdge[];
  downstream?: LineageEdge[];
  foreign_keys?: ForeignKeyEdge[];
  semantic_relations?: SemanticRelationship[];
  semantic_relations_truncated?: boolean;
  knowledge_graph?: KnowledgeGraph;
  relationship_counts?: Record<string, number>;
  quality?: {
    status?: string;
    message?: string;
    test_case_count?: number | null;
    test_cases?: Array<{
      id: string;
      name: string;
      fqn: string;
      entity_link: string;
      definition: string;
      suite: string;
      status: string;
      result_timestamp?: string | null;
    }>;
    truncated?: boolean;
  };
  capabilities?: Record<string, number | null>;
  needs_clarification?: boolean;
  clarification?: string;
  write_enabled?: boolean;
  governance_request?: {
    entity_id: string;
    changes: { description?: string; displayName?: string };
    preview_endpoint: string;
  };
  context_applied?: boolean;
  total_matches?: number;
};

export type GovernancePreview = {
  entity: CatalogEntity;
  diff: Array<{ field: string; before?: string | null; after?: string | null }>;
  confirmation_token: string;
  expires_in_seconds: number;
};
