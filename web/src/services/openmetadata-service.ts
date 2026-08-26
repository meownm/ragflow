import type {
  CatalogAction,
  CatalogAnswer,
  CatalogContextTurn,
  CatalogEntitiesResponse,
  CatalogFilters,
  CatalogStatus,
  GovernancePreview,
  StarterQuestionsResponse,
} from '@/pages/openmetadata/types';
import api from '@/utils/api';
import request from '@/utils/next-request';

type Envelope<T> = { code: number; message?: string; data: T };

async function unwrap<T>(promise: Promise<{ data: Envelope<T> }>): Promise<T> {
  const response = await promise;
  if (response.data.code !== 0) {
    throw new Error(response.data.message || 'OpenMetadata request failed');
  }
  return response.data.data;
}

export function fetchOpenMetadataStatus(refresh = false) {
  return unwrap<CatalogStatus>(
    request.get(api.openMetadataStatus, {
      params: refresh ? { refresh: true } : undefined,
    }),
  );
}

export function fetchOpenMetadataStarterQuestions(locale = 'en') {
  return unwrap<StarterQuestionsResponse>(
    request.get(api.openMetadataStarterQuestions, { params: { locale } }),
  );
}

export function provisionOpenMetadataAgents() {
  return unwrap<{
    managed_by: string;
    created: string[];
    updated: string[];
    count: number;
    agents: Array<{ id: string; role_id: string; title: string; url: string }>;
  }>(request.post(api.openMetadataProvisionAgents));
}

export type AskOpenMetadataOptions = {
  depth?: number;
  context?: CatalogContextTurn[];
  selectedEntityId?: string;
  action?: CatalogAction;
  locale?: string;
};

export function askOpenMetadata(
  question: string,
  filters: CatalogFilters,
  options: AskOpenMetadataOptions = {},
) {
  return unwrap<CatalogAnswer>(
    request.post(api.openMetadataQuery, {
      question,
      filters,
      depth: options.depth ?? 2,
      context: options.context ?? [],
      selected_entity_id: options.selectedEntityId,
      action: options.action,
      locale: options.locale ?? 'en',
    }),
  );
}

export function fetchOpenMetadataEntities({
  query = '',
  filters = {},
  limit = 20,
  offset = 0,
  sort = 'fqn',
  locale = 'en',
}: {
  query?: string;
  filters?: CatalogFilters;
  limit?: number;
  offset?: number;
  sort?: 'relevance' | 'updated_at' | 'fqn';
  locale?: string;
}) {
  return unwrap<CatalogEntitiesResponse>(
    request.get(api.openMetadataEntities, {
      params: {
        q: query,
        ...filters,
        limit,
        offset,
        sort,
        locale,
      },
    }),
  );
}

export function previewOpenMetadataChange(
  entityId: string,
  changes: { description?: string | null; displayName?: string | null },
) {
  return unwrap<GovernancePreview>(
    request.post(api.openMetadataGovernancePreview, {
      entity_id: entityId,
      changes,
    }),
  );
}

export function confirmOpenMetadataChange(confirmationToken: string) {
  return unwrap<{
    entity: CatalogAnswer['entity'];
    applied: boolean;
    fields: string[];
  }>(
    request.post(api.openMetadataGovernanceConfirm, {
      confirmation_token: confirmationToken,
    }),
  );
}
