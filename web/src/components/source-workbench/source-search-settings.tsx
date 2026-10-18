import { Button } from '@/components/ui/button';
import { configureWorkspaceSearch } from '@/services/linked-document-service';
import {
  sourceRequestError,
  type SourceDataset,
  type SourceWorkspace,
} from '@/services/source-workbench-service';
import { useEffect, useState } from 'react';

export function SourceSearchSettings({
  workspace,
  datasets,
  disabled,
  onChange,
  onMutationChange,
}: {
  workspace: SourceWorkspace;
  datasets: SourceDataset[];
  disabled: boolean;
  onChange(value: SourceWorkspace): void;
  onMutationChange(busy: boolean): void;
}) {
  const [selected, setSelected] = useState(workspace.dataset_ids);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => setSelected(workspace.dataset_ids), [workspace.dataset_ids]);
  const first = datasets.find((dataset) => dataset.id === selected[0]);
  return (
    <details
      className="mb-5 border-b border-border-button pb-4"
      open={!workspace.dataset_ids.length}
    >
      <summary className="cursor-pointer text-sm font-medium">
        Где искать источники
      </summary>
      <form
        className="mt-3 space-y-3"
        onSubmit={async (event) => {
          event.preventDefault();
          setBusy(true);
          onMutationChange(true);
          setError('');
          try {
            onChange(await configureWorkspaceSearch(workspace, selected));
          } catch (cause) {
            setError(sourceRequestError(cause, 'Не удалось настроить поиск'));
          } finally {
            setBusy(false);
            onMutationChange(false);
          }
        }}
      >
        <p className="text-xs text-text-secondary">
          Совместимость моделей нужна для совместного поиска. В рабочем
          пространстве можно связывать документы из разных поисковых подборок.
        </p>
        {datasets.map((dataset) => {
          const checked = selected.includes(dataset.id);
          const compatible =
            !first ||
            (dataset.tenant_id === first.tenant_id &&
              dataset.embd_id === first.embd_id);
          return (
            <label className="flex gap-2 text-sm" key={dataset.id}>
              <input
                type="checkbox"
                checked={checked}
                disabled={disabled || busy || (!compatible && !checked)}
                onChange={() =>
                  setSelected(
                    checked
                      ? selected.filter((id) => id !== dataset.id)
                      : [...selected, dataset.id],
                  )
                }
              />
              {dataset.name}
              {!compatible && !checked
                ? ' · другая модель поиска или владелец'
                : ''}
            </label>
          );
        })}
        <Button type="submit" variant="outline" disabled={disabled || busy}>
          Сохранить область поиска
        </Button>
        {error && (
          <p role="alert" className="text-state-error">
            {error}
          </p>
        )}
      </form>
    </details>
  );
}
