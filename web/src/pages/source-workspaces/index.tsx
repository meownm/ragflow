import { SourceChat } from '@/components/source-workbench/source-chat';
import { SourcePicker } from '@/components/source-workbench/source-picker';
import { SourceProcessor } from '@/components/source-workbench/source-processor';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  createSourceWorkspace,
  getSourceWorkspace,
  listSourceDatasets,
  listSourceWorkspaces,
  sourceRequestError,
  type SourceDataset,
  type SourceWorkspace,
} from '@/services/source-workbench-service';
import { useEffect, useMemo, useState, type FormEvent } from 'react';

export default function SourceWorkspacesPage() {
  const [datasets, setDatasets] = useState<SourceDataset[]>([]);
  const [datasetsStatus, setDatasetsStatus] = useState<
    'loading' | 'ready' | 'failed'
  >('loading');
  const [workspaces, setWorkspaces] = useState<SourceWorkspace[]>([]);
  const [active, setActive] = useState<SourceWorkspace | null>(null);
  const [title, setTitle] = useState('');
  const [datasetIds, setDatasetIds] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [selectionBusy, setSelectionBusy] = useState(false);
  const [processingBusy, setProcessingBusy] = useState(false);
  const [error, setError] = useState('');
  const datasetNames = useMemo(
    () =>
      Object.fromEntries(datasets.map((dataset) => [dataset.id, dataset.name])),
    [datasets],
  );
  const selectedDataset = datasets.find(
    (dataset) => dataset.id === datasetIds[0],
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
    listSourceDatasets()
      .then((items) => {
        if (!cancelled) {
          setDatasets(items);
          setDatasetsStatus('ready');
        }
      })
      .catch((cause) => {
        if (!cancelled) {
          setDatasetsStatus('failed');
          setError(
            sourceRequestError(cause, 'Не удалось загрузить базы знаний'),
          );
        }
      });
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
    <main className="h-full min-h-0 overflow-y-auto scrollbar-auto">
      <div className="mx-auto max-w-7xl px-6 py-8">
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
              disabled={processingBusy || selectionBusy}
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
            <nav
              aria-label="Этапы работы со статьями"
              className="sticky top-0 z-10 mb-6 flex flex-wrap gap-4 border-b border-border-button bg-bg-base py-3 text-sm"
            >
              <a
                className="text-accent-primary hover:underline"
                href="#source-picker"
              >
                Источники · {active.selected_documents.length} выбрано
              </a>
              <a
                className="text-accent-primary hover:underline"
                href="#source-questions"
              >
                Вопросы
              </a>
              <a
                className="text-accent-primary hover:underline"
                href="#source-processing"
              >
                Обработка
              </a>
            </nav>
            <div id="source-picker" className="scroll-mt-16">
              <SourcePicker
                key={active.id}
                workspace={active}
                datasetNames={datasetNames}
                selectionLocked={processingBusy}
                onChange={update}
                onMutationChange={setSelectionBusy}
              />
            </div>
            <div id="source-questions" className="scroll-mt-16">
              <SourceChat key={active.id} workspace={active} />
            </div>
            <div id="source-processing" className="scroll-mt-16">
              <SourceProcessor
                key={active.id}
                workspace={active}
                selectionBusy={selectionBusy}
                onBusyChange={setProcessingBusy}
              />
            </div>
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
                      <span className="block font-medium">
                        {workspace.title}
                      </span>
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
              {datasetsStatus === 'loading' && (
                <p className="mt-2 text-sm text-text-secondary">
                  Загрузка баз знаний…
                </p>
              )}
              {datasetsStatus === 'ready' && !datasets.length && (
                <p className="mt-2 text-sm text-text-secondary">
                  Нет доступных баз с проиндексированными статьями.
                </p>
              )}
              <div className="mt-2 max-h-60 space-y-2 overflow-y-auto">
                {datasets.map((dataset) => {
                  const checked = datasetIds.includes(dataset.id);
                  const compatible =
                    !selectedDataset ||
                    (dataset.tenant_id === selectedDataset.tenant_id &&
                      dataset.embd_id === selectedDataset.embd_id);
                  return (
                    <label
                      key={dataset.id}
                      className="flex items-start gap-2 text-sm"
                    >
                      <input
                        type="checkbox"
                        className="mt-1 accent-accent-primary"
                        checked={checked}
                        disabled={!compatible && !checked}
                        onChange={() =>
                          setDatasetIds((previous) =>
                            previous.includes(dataset.id)
                              ? previous.filter((id) => id !== dataset.id)
                              : [...previous, dataset.id],
                          )
                        }
                      />
                      <span>
                        {dataset.name}
                        {!compatible && !checked && (
                          <span className="block text-xs text-text-secondary">
                            Другой владелец или модель поиска
                          </span>
                        )}
                      </span>
                    </label>
                  );
                })}
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
      </div>
    </main>
  );
}
