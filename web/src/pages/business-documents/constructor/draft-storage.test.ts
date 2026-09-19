import {
  createDocumentConstructorStorageKey,
  loadStoredTemplate,
  saveStoredTemplate,
  type DocumentConstructorStorageScope,
} from './draft-storage';
import { createSqlQueryTemplate } from './sql-query-template';

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

describe('document constructor draft storage', () => {
  it('uses a distinct key for every user and tenant', () => {
    expect(createDocumentConstructorStorageKey(scope)).not.toBe(
      createDocumentConstructorStorageKey({ ...scope, userId: 'user-2' }),
    );
    expect(createDocumentConstructorStorageKey(scope)).not.toBe(
      createDocumentConstructorStorageKey({ ...scope, tenantId: 'tenant-2' }),
    );
  });

  it('round-trips a draft only for the matching owner', () => {
    const storage = createMemoryStorage();
    const key = createDocumentConstructorStorageKey(scope);
    const draft = createSqlQueryTemplate();
    draft.name = 'Черновик пользователя';
    saveStoredTemplate(storage, key, scope, draft);

    expect(loadStoredTemplate(storage, key, scope).name).toBe(
      'Черновик пользователя',
    );
    expect(
      loadStoredTemplate(storage, key, { ...scope, userId: 'other-user' }).name,
    ).toBe('Пошаговое проектирование SQL-запроса');
  });

  it('falls back to a valid draft when stored JSON is malformed', () => {
    const storage = createMemoryStorage();
    const key = createDocumentConstructorStorageKey(scope);
    storage.setItem(key, '{broken');

    expect(
      loadStoredTemplate(storage, key, scope).sections.length,
    ).toBeGreaterThan(0);
  });

  it('normalizes a partial stored draft before the UI consumes it', () => {
    const storage = createMemoryStorage();
    const key = createDocumentConstructorStorageKey(scope);
    saveStoredTemplate(storage, key, scope, createSqlQueryTemplate());
    const envelope = JSON.parse(storage.getItem(key)!);
    envelope.draft = {
      schemaVersion: 1,
      name: 'Старый черновик',
      headingBaseLevel: 99,
      sections: [
        {
          depth: 8,
          title: '  ',
          allowedBlocks: ['table', 'unknown'],
          requirements: ['Проверяемое требование', 42],
          minWords: -10,
        },
      ],
    };
    storage.setItem(key, JSON.stringify(envelope));

    expect(loadStoredTemplate(storage, key, scope)).toMatchObject({
      name: 'Старый черновик',
      documentType: 'sql_query_plan',
      headingBaseLevel: 4,
      sections: [
        {
          depth: 0,
          title: 'Раздел 1',
          allowedBlocks: ['table'],
          requirements: ['Проверяемое требование'],
          minWords: null,
        },
      ],
    });
  });
});
