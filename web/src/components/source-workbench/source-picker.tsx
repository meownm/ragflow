import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  getSourceWorkspace,
  saveSourceSelection,
  searchSourceWorkspace,
  sourceRequestError,
  type SourceCandidate,
  type SourceReference,
  type SourceWorkspace,
} from '@/services/source-workbench-service';
import { useMemo, useState, type FormEvent } from 'react';
import { buildSourceTree, sourceKey, type SourceTreeNode } from './source-tree';

interface SourcePickerProps {
  workspace: SourceWorkspace;
  datasetNames?: Record<string, string>;
  selectionLocked?: boolean;
  onChange(workspace: SourceWorkspace): void;
  onMutationChange?(busy: boolean): void;
}

function CandidateNode({
  node,
  selected,
  disabled,
  onToggle,
}: {
  node: SourceTreeNode;
  selected: Set<string>;
  disabled: boolean;
  onToggle(candidate: SourceCandidate): void;
}) {
  const candidate = node.candidate;
  const row = (
    <div className="flex items-start gap-2 py-1.5">
      {candidate && (
        <input
          type="checkbox"
          className="mt-1 accent-accent-primary"
          checked={selected.has(sourceKey(candidate))}
          disabled={disabled}
          aria-label={`Выбрать ${candidate.title}`}
          onChange={() => onToggle(candidate)}
          onClick={(event) => event.stopPropagation()}
        />
      )}
      <div className="min-w-0">
        <span className="text-sm text-text-primary">{node.label}</span>
        {candidate?.source_url && (
          <a
            className="ms-2 text-xs text-accent-primary hover:underline"
            href={candidate.source_url}
            target="_blank"
            rel="noopener noreferrer"
            onClick={(event) => event.stopPropagation()}
          >
            Открыть
          </a>
        )}
        {candidate?.excerpts[0] && (
          <p className="mt-1 line-clamp-2 text-xs text-text-secondary">
            {candidate.excerpts[0]}
          </p>
        )}
      </div>
    </div>
  );

  return (
    <li className="border-s border-border-button ps-3">
      {node.children.length ? (
        <details open>
          <summary className="cursor-pointer list-item">{row}</summary>
          <ul className="ms-2">
            {node.children.map((child) => (
              <CandidateNode
                key={child.key}
                node={child}
                selected={selected}
                disabled={disabled}
                onToggle={onToggle}
              />
            ))}
          </ul>
        </details>
      ) : (
        row
      )}
    </li>
  );
}

/** Reusable article picker. The host owns the workspace and can pass its selection to any workflow. */
export function SourcePicker({
  workspace,
  datasetNames = {},
  selectionLocked = false,
  onChange,
  onMutationChange,
}: SourcePickerProps) {
  const [query, setQuery] = useState('');
  const [candidates, setCandidates] = useState<SourceCandidate[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [activeSearch, setActiveSearch] = useState<{
    query: string;
    page: number;
    hasMore: boolean;
  } | null>(null);

  const selected = useMemo(
    () => new Set(workspace.selected_documents.map(sourceKey)),
    [workspace.selected_documents],
  );
  const candidateByKey = useMemo(
    () =>
      new Map(candidates.map((candidate) => [sourceKey(candidate), candidate])),
    [candidates],
  );
  const tree = useMemo(() => buildSourceTree(candidates), [candidates]);

  const runSearch = async (searchQuery: string, page: number) => {
    if (!searchQuery || busy) return;
    setBusy(true);
    setError('');
    try {
      const result = await searchSourceWorkspace(
        workspace.id,
        searchQuery,
        page,
      );
      setCandidates((previous) => {
        const merged = new Map(
          (page === 1 ? [] : previous).map((candidate) => [
            sourceKey(candidate),
            candidate,
          ]),
        );
        result.candidates.forEach((candidate) =>
          merged.set(sourceKey(candidate), candidate),
        );
        return [...merged.values()];
      });
      setActiveSearch({
        query: searchQuery,
        page: result.page,
        hasMore: result.has_more,
      });
      onChange(await getSourceWorkspace(workspace.id));
    } catch (cause) {
      setError(sourceRequestError(cause, 'Поиск не выполнен'));
    } finally {
      setBusy(false);
    }
  };

  const search = async (event: FormEvent) => {
    event.preventDefault();
    await runSearch(query.trim(), 1);
  };

  const toggle = async (candidate: SourceCandidate | SourceReference) => {
    if (busy || selectionLocked) return;
    const key = sourceKey(candidate);
    const next = selected.has(key)
      ? workspace.selected_documents.filter((item) => sourceKey(item) !== key)
      : [
          ...workspace.selected_documents,
          {
            dataset_id: candidate.dataset_id,
            document_id: candidate.document_id,
          },
        ];
    setBusy(true);
    onMutationChange?.(true);
    setError('');
    try {
      onChange(await saveSourceSelection(workspace, next));
    } catch (cause) {
      setError(sourceRequestError(cause, 'Не удалось сохранить выбор'));
      try {
        onChange(await getSourceWorkspace(workspace.id));
      } catch {
        // Keep the current selection visible if a refresh is also unavailable.
      }
    } finally {
      setBusy(false);
      onMutationChange?.(false);
    }
  };

  const refreshSelection = async () => {
    if (busy || selectionLocked || !workspace.selected_documents.length) return;
    setBusy(true);
    onMutationChange?.(true);
    setError('');
    try {
      onChange(
        await saveSourceSelection(workspace, workspace.selected_documents),
      );
    } catch (cause) {
      setError(sourceRequestError(cause, 'Не удалось обновить статьи'));
      try {
        onChange(await getSourceWorkspace(workspace.id));
      } catch {
        // Keep the existing selection visible when refresh is unavailable.
      }
    } finally {
      setBusy(false);
      onMutationChange?.(false);
    }
  };

  const moveSelection = async (index: number, direction: -1 | 1) => {
    if (busy || selectionLocked) return;
    const next = [...workspace.selected_documents];
    const target = index + direction;
    if (target < 0 || target >= next.length) return;
    [next[index], next[target]] = [next[target], next[index]];
    setBusy(true);
    onMutationChange?.(true);
    setError('');
    try {
      onChange(await saveSourceSelection(workspace, next));
    } catch (cause) {
      setError(sourceRequestError(cause, 'Не удалось изменить порядок статей'));
      try {
        onChange(await getSourceWorkspace(workspace.id));
      } catch {
        // Keep the current order visible when refresh is unavailable.
      }
    } finally {
      setBusy(false);
      onMutationChange?.(false);
    }
  };

  return (
    <section className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_18rem]">
      <div>
        <form className="flex gap-2" onSubmit={search}>
          <Input
            aria-label="Поиск статей"
            value={query}
            maxLength={500}
            placeholder="Тема или уточняющий запрос"
            onChange={(event) => setQuery(event.target.value)}
          />
          <Button type="submit" disabled={busy || !query.trim()}>
            Найти
          </Button>
        </form>
        {workspace.search_queries.length > 0 && (
          <p className="mt-2 text-xs text-text-secondary">
            Запросы: {workspace.search_queries.join(' · ')}
          </p>
        )}
        {error && (
          <p role="alert" className="mt-3 text-sm text-state-error">
            {error}
          </p>
        )}
        <div className="mt-5">
          <h2 className="text-base font-semibold">Найденные статьи</h2>
          {activeSearch && (
            <p className="mt-2 text-sm text-text-secondary">
              Результаты запроса «{activeSearch.query}»: {candidates.length} на
              загруженных страницах.
            </p>
          )}
          {!activeSearch && (
            <p className="mt-3 text-sm text-text-secondary">
              Введите запрос, чтобы найти статьи в выбранных базах знаний.
            </p>
          )}
          {activeSearch && !candidates.length && (
            <p className="mt-3 text-sm text-text-secondary">
              По этому запросу статьи не найдены. Уточните формулировку.
            </p>
          )}
          <ul className="mt-3 space-y-2">
            {tree.map((node) => (
              <li
                key={node.key}
                className="rounded-md border border-border-button p-3"
              >
                <p className="mb-2 text-xs font-medium text-text-secondary">
                  {node.label} ·{' '}
                  {datasetNames[node.key.split(':')[1]] ||
                    node.key.split(':')[1]}
                </p>
                <ul>
                  {node.children.map((child) => (
                    <CandidateNode
                      key={child.key}
                      node={child}
                      selected={selected}
                      disabled={busy || selectionLocked}
                      onToggle={toggle}
                    />
                  ))}
                </ul>
              </li>
            ))}
          </ul>
          {activeSearch?.hasMore && (
            <Button
              type="button"
              className="mt-4"
              disabled={busy}
              onClick={() =>
                runSearch(activeSearch.query, activeSearch.page + 1)
              }
            >
              Показать ещё
            </Button>
          )}
        </div>
      </div>
      <aside className="rounded-md border border-border-button p-4">
        <h2 className="font-semibold">Выбрано: {selected.size}</h2>
        {selectionLocked && (
          <p className="mt-2 text-xs text-text-secondary">
            Подборка закреплена до завершения обработки.
          </p>
        )}
        {selected.size > 1 && (
          <p className="mt-2 text-xs text-text-secondary">
            Статьи обрабатываются в этом порядке. Номера используются в итоговом
            тексте.
          </p>
        )}
        {selected.size > 0 && (
          <button
            type="button"
            className="mt-2 text-xs text-accent-primary hover:underline"
            disabled={busy || selectionLocked}
            onClick={refreshSelection}
          >
            Обновить выбранные статьи
          </button>
        )}
        {!selected.size && (
          <p className="mt-3 text-sm text-text-secondary">
            Отметьте нужные статьи слева.
          </p>
        )}
        <ul className="mt-3 space-y-3">
          {workspace.selected_documents.map((source, index) => {
            const detail =
              candidateByKey.get(sourceKey(source)) ||
              workspace.selected_sources?.find(
                (item) => sourceKey(item) === sourceKey(source),
              );
            return (
              <li
                key={sourceKey(source)}
                className="border-b border-border-button pb-2 text-sm"
              >
                <p className="font-medium">
                  {index + 1}. {detail?.title || source.document_id}
                </p>
                {detail?.path && detail.path.length > 1 && (
                  <p className="text-xs text-text-secondary">
                    {detail.path.join(' › ')}
                  </p>
                )}
                <div className="mt-2 flex flex-wrap gap-3">
                  {workspace.selected_documents.length > 1 && (
                    <>
                      <button
                        type="button"
                        className="text-xs text-accent-primary hover:underline disabled:opacity-40"
                        disabled={busy || selectionLocked || index === 0}
                        onClick={() => moveSelection(index, -1)}
                        aria-label={`Поднять статью ${detail?.title || source.document_id}`}
                      >
                        Выше
                      </button>
                      <button
                        type="button"
                        className="text-xs text-accent-primary hover:underline disabled:opacity-40"
                        disabled={
                          busy ||
                          selectionLocked ||
                          index === workspace.selected_documents.length - 1
                        }
                        onClick={() => moveSelection(index, 1)}
                        aria-label={`Опустить статью ${detail?.title || source.document_id}`}
                      >
                        Ниже
                      </button>
                    </>
                  )}
                  <button
                    type="button"
                    className="text-xs text-accent-primary hover:underline"
                    disabled={busy || selectionLocked}
                    onClick={() => toggle(source)}
                  >
                    Убрать
                  </button>
                </div>
              </li>
            );
          })}
        </ul>
      </aside>
    </section>
  );
}
