import { SourceChat } from '@/components/source-workbench/source-chat';
import { SourcePicker } from '@/components/source-workbench/source-picker';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import type { IDataset } from '@/interfaces/database/dataset';
import { listDataset } from '@/services/knowledge-service';
import {
  createSourceWorkspace,
  getSourceWorkspace,
  listSourceWorkspaces,
  sourceRequestError,
  type SourceWorkspace,
} from '@/services/source-workbench-service';
import { useEffect, useMemo, useState, type FormEvent } from 'react';

export default function SourceWorkspacesPage() {
  const [datasets, setDatasets] = useState<IDataset[]>([]);
  const [workspaces, setWorkspaces] = useState<SourceWorkspace[]>([]);
  const [active, setActive] = useState<SourceWorkspace | null>(null);
  const [title, setTitle] = useState('');
  const [datasetIds, setDatasetIds] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const datasetNames = useMemo(
    () =>
      Object.fromEntries(datasets.map((dataset) => [dataset.id, dataset.name])),
    [datasets],
  );

  useEffect(() => {
    listSourceWorkspaces()
      .then(setWorkspaces)
      .catch((cause) => {
        setError(sourceRequestError(cause, 'Не удалось загрузить подборки'));
      });
  }, []);

  useEffect(() => {
    let cancelled = false;
    const loadDatasets = async () => {
      try {
        const all: IDataset[] = [];
        for (let page = 1; ; page += 1) {
          const response = await listDataset({ page, page_size: 100 });
          if (response.data.code !== 0) {
            throw new Error(
              response.data.message || 'Не удалось загрузить базы знаний',
            );
          }
          const batch = (response.data.data || []) as IDataset[];
          all.push(...batch);
          if (batch.length < 100 || all.length >= response.data.total) break;
        }
        if (!cancelled)
          setDatasets(all.filter((dataset) => dataset.chunk_count > 0));
      } catch (cause) {
        if (!cancelled)
          setError(
            sourceRequestError(cause, 'Не удалось загрузить базы знаний'),
          );
      }
    };
    void loadDatasets();
    return () => {
      cancelled = true;
    };
  }, []);

  const create = async (event: FormEvent) => {
    event.preventDefault();
    if (!title.trim() || !datasetIds.length || busy) return;
    setBusy(true);
    setError('');
    try {
      const created = await createSourceWorkspace({
        title: title.trim(),
        dataset_ids: datasetIds,
      });
      setWorkspaces((previous) => [created, ...previous]);
      setActive(created);
      setTitle('');
      setDatasetIds([]);
    } catch (cause) {
      setError(sourceRequestError(cause, 'Не удалось создать подборку'));
    } finally {
      setBusy(false);
    }
  };

  const open = async (id: string) => {
    setBusy(true);
    setError('');
    try {
      setActive(await getSourceWorkspace(id));
    } catch (cause) {
      setError(sourceRequestError(cause, 'Не удалось открыть подборку'));
    } finally {
      setBusy(false);
    }
  };

  const update = (workspace: SourceWorkspace) => {
    setActive(workspace);
    setWorkspaces((previous) =>
      previous.map((item) => (item.id === workspace.id ? workspace : item)),
    );
  };

  return (
    <main className="mx-auto max-w-7xl px-6 py-8">
      <div className="mb-6 flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold text-text-primary">
            Работа со статьями
          </h1>
          <p className="mt-1 text-sm text-text-secondary">
            Найдите статьи и сохраните набор источников для дальнейшей работы.
          </p>
        </div>
        {active && (
          <Button
            type="button"
            variant="outline"
            onClick={() => setActive(null)}
          >
            К подборкам
          </Button>
        )}
      </div>

      {error && (
        <p role="alert" className="mb-4 text-sm text-state-error">
          {error}
        </p>
      )}

      {active ? (
        <>
          <div className="mb-6">
            <h2 className="text-lg font-medium">{active.title}</h2>
            <p className="mt-1 text-xs text-text-secondary">
              Базы знаний:{' '}
              {active.dataset_ids
                .map((id) => datasetNames[id] || id)
                .join(', ')}
            </p>
          </div>
          <SourcePicker
            key={active.id}
            workspace={active}
            datasetNames={datasetNames}
            onChange={update}
          />
          <SourceChat
            key={`${active.id}:${active.version}`}
            workspace={active}
          />
        </>
      ) : (
        <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_22rem]">
          <section>
            <h2 className="text-lg font-medium">Сохранённые подборки</h2>
            {!workspaces.length && (
              <p className="mt-4 text-sm text-text-secondary">
                Подборок пока нет.
              </p>
            )}
            <ul className="mt-4 space-y-2">
              {workspaces.map((workspace) => (
                <li key={workspace.id}>
                  <button
                    type="button"
                    className="w-full rounded-md border border-border-button p-4 text-start hover:bg-bg-card"
                    disabled={busy}
                    onClick={() => open(workspace.id)}
                  >
                    <span className="block font-medium">{workspace.title}</span>
                    <span className="mt-1 block text-xs text-text-secondary">
                      Статей: {workspace.selected_documents.length}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </section>
          <form
            className="rounded-md border border-border-button p-4"
            onSubmit={create}
          >
            <h2 className="font-medium">Новая подборка</h2>
            <Input
              className="mt-4"
              aria-label="Название подборки"
              maxLength={255}
              placeholder="Например, согласование требований"
              value={title}
              onChange={(event) => setTitle(event.target.value)}
            />
            <p className="mt-5 text-sm font-medium">Где искать</p>
            <div className="mt-2 max-h-60 space-y-2 overflow-y-auto">
              {datasets.map((dataset) => (
                <label
                  key={dataset.id}
                  className="flex items-start gap-2 text-sm"
                >
                  <input
                    type="checkbox"
                    className="mt-1 accent-accent-primary"
                    checked={datasetIds.includes(dataset.id)}
                    onChange={() =>
                      setDatasetIds((previous) =>
                        previous.includes(dataset.id)
                          ? previous.filter((id) => id !== dataset.id)
                          : [...previous, dataset.id],
                      )
                    }
                  />
                  <span>{dataset.name}</span>
                </label>
              ))}
            </div>
            <Button
              className="mt-5"
              type="submit"
              disabled={busy || !title.trim() || !datasetIds.length}
            >
              Создать
            </Button>
          </form>
        </div>
      )}
    </main>
  );
}
