import { Button } from '@/components/ui/button';
import {
  getWorkspaceDocument,
  removeDocumentDependency,
  saveDocumentDependency,
  type Dependency,
  type DocumentSummary,
  type Section,
  type WorkspaceDocument,
} from '@/services/linked-document-service';
import { sourceRequestError } from '@/services/source-workbench-service';
import { useEffect, useState } from 'react';
import { Choice, sectionTitle } from './document-controls';
import { DocumentSettings, type RunDocumentAction } from './document-settings';

const relations = {
  requires: 'Задаёт требования',
  constrains: 'Ограничивает решения',
  details: 'Детализируется в документе',
  reflects: 'Должен отражать результат',
  verifies: 'Проверяется документом',
  reference: 'Используется как справка',
};

export function DocumentDependencies({
  workspace,
  active,
  documents,
  busy,
  dirty,
  perform,
  open,
}: {
  workspace: { id: string };
  active: WorkspaceDocument;
  documents: DocumentSummary[];
  busy: boolean;
  dirty: boolean;
  perform: RunDocumentAction;
  open(id: string): void;
}) {
  const [error, setError] = useState('');
  const [sourceId, setSourceId] = useState('');
  const [sourceDocument, setSourceDocument] =
    useState<WorkspaceDocument | null>(null);
  const [dependencyId, setDependencyId] = useState<string>();
  const [relation, setRelation] = useState<Dependency['relation']>(
    active.capabilities.manage ? 'requires' : 'reference',
  );
  const [required, setRequired] = useState(active.capabilities.manage);
  const [blocksApproval, setBlocksApproval] = useState(
    active.capabilities.manage,
  );
  const [refreshOn, setRefreshOn] = useState<Dependency['refresh_on']>('saved');
  const [maintenance, setMaintenance] =
    useState<Dependency['maintenance']>('manual');
  const [sourceScope, setSourceScope] = useState<string[]>([]);
  const [targetScope, setTargetScope] = useState<string[]>([]);

  useEffect(() => {
    if (!sourceId) {
      setSourceDocument(null);
      return;
    }
    let cancelled = false;
    setSourceDocument(null);
    getWorkspaceDocument(workspace.id, sourceId)
      .then((item) => {
        if (!cancelled) {
          setSourceDocument(item);
          if (item.source_kind !== 'local')
            setRefreshOn((value) =>
              value === 'implemented' ? 'saved' : value,
            );
        }
      })
      .catch((cause) => {
        if (!cancelled)
          setError(sourceRequestError(cause, 'Не удалось открыть источник'));
      });
    return () => {
      cancelled = true;
    };
  }, [workspace.id, sourceId]);

  const configureDependency = (dependency: Dependency) => {
    setSourceId(dependency.source_id);
    setDependencyId(dependency.id);
    setRelation(dependency.relation);
    setRequired(dependency.required);
    setBlocksApproval(dependency.blocks_approval);
    setRefreshOn(dependency.refresh_on);
    setMaintenance(dependency.maintenance);
    setSourceScope(dependency.source_scope);
    setTargetScope(dependency.target_scope);
  };

  const scopePicker = (
    label: string,
    sections: Section[],
    scope: string[],
    update: (value: string[]) => void,
  ) => (
    <fieldset className="space-y-2 text-sm">
      <legend className="font-medium">{label}</legend>
      <p className="text-xs text-text-secondary">
        Ничего не выбрано — весь документ.
      </p>
      <button
        type="button"
        className="text-xs text-accent-primary"
        onClick={() => update([])}
      >
        Использовать весь документ
      </button>
      {sections.map((section) => (
        <label className="flex gap-2" key={section.id}>
          <input
            type="checkbox"
            checked={scope.includes(section.id)}
            onChange={() =>
              update(
                scope.includes(section.id)
                  ? scope.filter((id) => id !== section.id)
                  : [...scope, section.id],
              )
            }
          />
          {sectionTitle(section)}
        </label>
      ))}
    </fieldset>
  );

  return (
    <>
      {error && (
        <p role="alert" className="text-state-error">
          {error}
        </p>
      )}
      <div className="space-y-6">
        {active.source_ref && (
          <p className="text-sm">
            {active.source_kind === 'business'
              ? `Сохранена согласованная редакция ${active.source_ref.revision_number} Business Documents. `
              : 'Сохранён снимок индексированного текста. '}
            {active.source_update_available &&
              'Доступна новая согласованная редакция. Нажмите «Проверить источники» для обновления снимка. '}
            {active.source_ref.source_url && (
              <a
                className="text-accent-primary"
                href={active.source_ref.source_url}
                target="_blank"
                rel="noopener noreferrer"
              >
                Открыть оригинал
              </a>
            )}
          </p>
        )}
        {active.waiting.map((item) => (
          <p key={item.dependency_id} className="text-sm text-text-secondary">
            {item.reason}:{' '}
            {
              documents.find((document) => document.id === item.source_id)
                ?.title
            }
          </p>
        ))}
        <ul className="space-y-3">
          {active.dependencies.map((dependency) => (
            <li
              key={dependency.id}
              className="border-b border-border-button pb-3 text-sm"
            >
              <button
                className="font-medium text-accent-primary"
                onClick={() => open(dependency.source_id)}
              >
                {documents.find(
                  (document) => document.id === dependency.source_id,
                )?.title || 'Источник'}
              </button>
              <p>
                {relations[dependency.relation]} ·{' '}
                {dependency.required
                  ? 'Обязательное основание'
                  : 'Справочное основание'}
              </p>
              <p className="text-xs text-text-secondary">
                {dependency.refresh_on === 'approved'
                  ? 'После утверждения источника'
                  : dependency.refresh_on === 'implemented'
                    ? 'После подтверждения реализации'
                    : 'После сохранения источника'}{' '}
                ·{' '}
                {dependency.maintenance === 'auto'
                  ? 'Автоматический анализ'
                  : 'Проверка по запросу'}
              </p>
              <Button
                className="mt-2"
                variant="outline"
                disabled={
                  busy ||
                  dirty ||
                  !active.capabilities.edit ||
                  ((dependency.required || dependency.blocks_approval) &&
                    !active.capabilities.manage)
                }
                onClick={() => configureDependency(dependency)}
              >
                Настроить связь
              </Button>
              <Button
                className="ms-2 mt-2"
                variant="outline"
                disabled={
                  busy ||
                  dirty ||
                  !active.capabilities.edit ||
                  ((dependency.required || dependency.blocks_approval) &&
                    !active.capabilities.manage)
                }
                onClick={() =>
                  void perform(async () => {
                    const result = await removeDocumentDependency(
                      workspace.id,
                      active.id,
                      dependency.id,
                      active.version,
                    );
                    setDependencyId(undefined);
                    setSourceId('');
                    return result;
                  })
                }
              >
                Удалить связь
              </Button>
            </li>
          ))}
        </ul>
        <form
          className="space-y-4 border-t border-border-button pt-4"
          onSubmit={(event) => {
            event.preventDefault();
            if (!sourceId || !sourceDocument) return;
            void perform(() =>
              saveDocumentDependency(
                workspace.id,
                active.id,
                {
                  source_id: sourceId,
                  relation,
                  required,
                  blocks_approval: blocksApproval,
                  source_scope: sourceScope,
                  target_scope: targetScope,
                  refresh_on: refreshOn,
                  maintenance,
                  expected_version: active.version,
                },
                dependencyId,
              ),
            );
          }}
        >
          <fieldset
            className="space-y-4"
            disabled={busy || dirty || !active.capabilities.edit}
          >
            <h3 className="font-medium">
              {dependencyId ? 'Настройка связи' : 'Подключить основание'}
            </h3>
            <Choice
              label="Документ-источник"
              value={sourceId}
              options={{
                '': 'Выберите документ',
                ...Object.fromEntries(
                  documents
                    .filter((document) => document.id !== active.id)
                    .map((document) => [document.id, document.title]),
                ),
              }}
              disabled={busy}
              onChange={(value) => {
                setSourceId(value);
                setDependencyId(undefined);
                setSourceScope([]);
                setTargetScope([]);
              }}
            />
            <div className="grid gap-4 md:grid-cols-2">
              <Choice
                label="Назначение связи"
                value={relation}
                options={relations}
                onChange={(value) => {
                  setRelation(value as Dependency['relation']);
                  if (value === 'reference') {
                    setRequired(false);
                    setBlocksApproval(false);
                  }
                }}
              />
              <Choice
                label="Когда учитывать изменения"
                value={refreshOn}
                options={{
                  saved: 'После сохранения источника',
                  approved: 'После утверждения источника',
                  ...(sourceDocument?.source_kind === 'local'
                    ? { implemented: 'После подтверждения реализации' }
                    : {}),
                }}
                onChange={(value) =>
                  setRefreshOn(value as Dependency['refresh_on'])
                }
              />
            </div>
            <label className="flex gap-2 text-sm">
              <input
                type="checkbox"
                checked={required}
                disabled={!active.capabilities.manage}
                onChange={(event) => setRequired(event.target.checked)}
              />
              Обязательное основание
            </label>
            <label className="flex gap-2 text-sm">
              <input
                type="checkbox"
                checked={blocksApproval}
                disabled={!active.capabilities.manage}
                onChange={(event) => setBlocksApproval(event.target.checked)}
              />
              Разобрать влияние перед утверждением документа
            </label>
            <Choice
              label="Сопровождение"
              value={maintenance}
              options={{
                manual: 'Проверять по запросу',
                auto: 'Автоматически готовить предложения',
              }}
              onChange={(value) =>
                setMaintenance(value as Dependency['maintenance'])
              }
            />
            <details className="text-sm">
              <summary className="cursor-pointer">
                Область действия связи
              </summary>
              <div className="mt-3 grid gap-5 md:grid-cols-2">
                {sourceDocument &&
                  scopePicker(
                    'Разделы источника',
                    sourceDocument.sections,
                    sourceScope,
                    setSourceScope,
                  )}
                {scopePicker(
                  'Разделы рабочего документа',
                  active.sections,
                  targetScope,
                  setTargetScope,
                )}
              </div>
            </details>
            <Button type="submit" disabled={busy || dirty || !sourceDocument}>
              Сохранить связь
            </Button>
            {dependencyId && (
              <Button
                className="ms-2"
                variant="outline"
                type="button"
                onClick={() => {
                  setDependencyId(undefined);
                  setSourceId('');
                }}
              >
                Новая связь
              </Button>
            )}
          </fieldset>
        </form>
        <DocumentSettings
          workspaceId={workspace.id}
          document={active}
          disabled={busy || dirty}
          run={perform}
        />
      </div>
    </>
  );
}
