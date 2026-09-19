import type { DocumentConstructorStorageScope } from './draft-storage';
import type { SchemaWorkspaceState } from './schema-workspace';
import {
  createSchemaWorkspaceStorageKey,
  emptySchemaWorkspace,
  loadSchemaWorkspace,
  saveSchemaWorkspace,
} from './schema-workspace-storage';

function createMemoryStorage() {
  const values = new Map<string, string>();
  return {
    getItem: jest.fn((key: string) => values.get(key) ?? null),
    setItem: jest.fn((key: string, value: string) => values.set(key, value)),
  } as unknown as Storage;
}

const scope: DocumentConstructorStorageScope = {
  userId: 'user-1',
  tenantId: 'tenant-1',
};

const workspace: SchemaWorkspaceState = {
  input: 'Заказ',
  requirements: 'Вывести оплаченные заказы.',
  resolutions: [
    {
      term: 'Заказ',
      status: 'confirmed',
      decision: 'user',
      selectedEntityId: 'orders',
      selectedColumnIds: ['dwh.order_fact.paid_amount_rub'],
      freshness: {
        snapshot_at: '2026-09-14T08:00:00+00:00',
        checked_at: '2026-09-14T08:05:00+00:00',
        stale: false,
        threshold_hours: 168,
      },
      retrieval: 'omd_dataset_hybrid_rrf',
      sources: [{ label: 'OpenMetadata', url: 'https://metadata.example' }],
      warnings: [],
      interpretation: {
        kind: 'entity',
        normalizedTerm: 'заказ',
        recommendedEntityId: 'orders',
        recommendedColumnIds: [],
        confidence: 0.82,
        reason: 'Таблица содержит факты заказов.',
        clarificationQuestion: null,
        llmStatus: 'APPLIED',
        promptName: 'sql_schema_interpreter',
        promptVersion: '1',
        promptHash: 'sha256:test-prompt',
        llmWarning: null,
      },
      candidates: [
        {
          id: 'orders',
          name: 'order_fact',
          displayName: 'Заказы',
          technicalName: 'order_fact',
          fqn: 'dwh.order_fact',
          description: 'Факты заказов',
          version: 3,
          updatedAt: '2026-09-14T08:00:00+00:00',
          service: 'warehouse',
          schema: 'dwh',
          database: 'analytics',
          owners: ['DWH'],
          domains: ['Sales'],
          tags: ['gold'],
          glossaryTerms: ['Заказ'],
          url: 'https://metadata.example/table/dwh.order_fact',
          matchedBy: ['catalog_projection'],
          columnCount: 1,
          columnsTruncated: false,
          columns: [
            {
              id: 'dwh.order_fact.paid_amount_rub',
              name: 'paid_amount_rub',
              fqn: 'dwh.order_fact.paid_amount_rub',
              dataType: 'NUMERIC(18,2)',
              description: 'Оплаченная сумма',
              constraint: '',
              glossaryTerms: [],
            },
          ],
          tableConstraints: [
            {
              constraintType: 'PRIMARY_KEY',
              columns: ['order_id'],
              referredColumns: [],
            },
          ],
          schemaStatus: 'loaded',
          schemaFingerprint: 'sha256:orders-v3',
          schemaError: null,
        },
      ],
    },
  ],
};

describe('schema workspace storage', () => {
  it('uses an owner-scoped key and rejects an empty owner', () => {
    expect(createSchemaWorkspaceStorageKey(scope)).not.toBe(
      createSchemaWorkspaceStorageKey({ ...scope, userId: 'user-2' }),
    );
    expect(() =>
      createSchemaWorkspaceStorageKey({ ...scope, tenantId: ' ' }),
    ).toThrow('Для снимка схемы нужны userId и tenantId.');
  });

  it('round-trips a validated workspace for the matching owner', () => {
    const storage = createMemoryStorage();
    const key = createSchemaWorkspaceStorageKey(scope);
    saveSchemaWorkspace(storage, key, scope, workspace);

    expect(loadSchemaWorkspace(storage, key, scope)).toEqual(workspace);
    expect(
      loadSchemaWorkspace(storage, key, { ...scope, userId: 'other-user' }),
    ).toEqual(emptySchemaWorkspace());
  });

  it('migrates the previous workspace envelope without inventing requirements or provenance', () => {
    const storage = createMemoryStorage();
    const key = createSchemaWorkspaceStorageKey(scope);
    saveSchemaWorkspace(storage, key, scope, workspace);
    const envelope = JSON.parse(storage.getItem(key)!);
    envelope.schemaVersion = 1;
    delete envelope.workspace.requirements;
    const candidate = envelope.workspace.resolutions[0].candidates[0];
    delete candidate.schemaStatus;
    delete candidate.schemaFingerprint;
    delete candidate.schemaError;
    const interpretation = envelope.workspace.resolutions[0].interpretation;
    delete interpretation.recommendedColumnIds;
    delete interpretation.promptName;
    delete interpretation.promptHash;
    storage.setItem(key, JSON.stringify(envelope));

    const migrated = loadSchemaWorkspace(storage, key, scope);
    expect(migrated.input).toBe('Заказ');
    expect(migrated.requirements).toBe('');
    expect(migrated.resolutions[0].candidates[0].schemaStatus).toBe('summary');
    expect(migrated.resolutions[0].interpretation?.promptName).toBeNull();
  });

  it('fails closed when a stored table selection no longer exists', () => {
    const storage = createMemoryStorage();
    const key = createSchemaWorkspaceStorageKey(scope);
    saveSchemaWorkspace(storage, key, scope, workspace);
    const envelope = JSON.parse(storage.getItem(key)!);
    envelope.workspace.resolutions[0].selectedEntityId = 'missing-table';
    storage.setItem(key, JSON.stringify(envelope));

    expect(loadSchemaWorkspace(storage, key, scope)).toEqual(
      emptySchemaWorkspace(),
    );
  });

  it('fails closed when a stored field does not belong to the selected table', () => {
    const storage = createMemoryStorage();
    const key = createSchemaWorkspaceStorageKey(scope);
    saveSchemaWorkspace(storage, key, scope, workspace);
    const envelope = JSON.parse(storage.getItem(key)!);
    envelope.workspace.resolutions[0].selectedColumnIds = ['secret.column'];
    storage.setItem(key, JSON.stringify(envelope));

    expect(loadSchemaWorkspace(storage, key, scope)).toEqual(
      emptySchemaWorkspace(),
    );
  });
});
