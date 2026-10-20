import { Button } from '@/components/ui/button';
import {
  checkAlignment,
  getAlignment,
  listAlignmentSources,
  type AlignmentView,
} from '@/services/technical-specification-service';
import { useEffect, useRef, useState } from 'react';
import { messageId, type Specification } from './model';

export function AlignmentPanel({
  document,
  disabled,
  canEdit,
  onDocument,
}: {
  document: Specification;
  disabled: boolean;
  canEdit: boolean;
  onDocument: (value: Specification) => void;
}) {
  const [sources, setSources] = useState<
    { document_id: string; title: string; revision_number: number }[]
  >([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [view, setView] = useState<AlignmentView | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [query, setQuery] = useState('');
  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(false);
  const catalogueGeneration = useRef(0);
  const generation = useRef({ active: false });
  const latest = useRef({ document, onDocument });
  latest.current = { document, onDocument };
  const request = useRef<{
    version: number;
    request_id: string;
    business_document_ids: string[];
  } | null>(null);
  useEffect(() => {
    const current = { active: true };
    generation.current = current;
    setError('');
    setBusy(false);
    request.current = null;
    Promise.all([
      getAlignment(document.workspace_id, document.id),
      canEdit
        ? listAlignmentSources(document.workspace_id)
        : Promise.resolve({ items: [] }),
    ])
      .then(([value, catalogue]) => {
        if (!current.active) return;
        setView(value);
        const visible = latest.current.document;
        if (
          value.document.id === visible.id &&
          value.document.revision === visible.revision &&
          value.document.version > visible.version
        )
          latest.current.onDocument(value.document);
        setSources(catalogue.items);
        setPage(1);
        setHasMore('has_more' in catalogue && Boolean(catalogue.has_more));
      })
      .catch(() => {
        if (current.active)
          setError('Не удалось загрузить проверку или согласованные БТ.');
      });
    return () => {
      current.active = false;
    };
  }, [document.workspace_id, document.id, document.revision, canEdit]);

  async function search(nextPage = 1) {
    const current = ++catalogueGeneration.current;
    const lifecycle = generation.current;
    try {
      const value = await listAlignmentSources(
        document.workspace_id,
        query,
        nextPage,
      );
      if (!lifecycle.active || current !== catalogueGeneration.current) return;
      setSources(value.items);
      setPage(nextPage);
      setHasMore(Boolean(value.has_more));
    } catch {
      if (lifecycle.active && current === catalogueGeneration.current)
        setError('Не удалось найти согласованные БТ.');
    }
  }

  async function run() {
    setBusy(true);
    setError('');
    const current = generation.current;
    try {
      request.current ??= {
        version: document.version,
        request_id: messageId(),
        business_document_ids: [...selected],
      };
      const value = await checkAlignment(
        document.workspace_id,
        document.id,
        request.current,
      );
      if (!current.active) return;
      setView(value);
      onDocument(value.document);
      if (value.checks.every((item) => item.status !== 'PROCESSING'))
        request.current = null;
    } catch {
      if (current.active)
        setError(
          'Проверка не завершена. Повтор использует тот же запрос; положительный результат не подтверждён.',
        );
    } finally {
      if (current.active) setBusy(false);
    }
  }

  return (
    <section
      className="space-y-3 rounded border p-4"
      aria-label="Соответствие бизнес-требованиям"
    >
      <h2 className="font-medium">Соответствие БТ</h2>
      <p>
        Проверяются согласованные редакции БТ. Сохранённый черновик не означает
        подтверждённое соответствие.
      </p>
      {error && <p role="alert">{error}</p>}
      {canEdit && (
        <fieldset disabled={busy || disabled}>
          <legend>Основания</legend>
          <label>
            Поиск БТ
            <input
              value={query}
              maxLength={255}
              onChange={(event) => setQuery(event.target.value)}
            />
          </label>
          <Button onClick={() => void search()}>Найти БТ</Button>
          <p>Выбрано оснований: {selected.length} из 16</p>
          {sources.length === 0 && <p>Доступных согласованных БТ пока нет.</p>}
          {sources.map((source) => (
            <label key={source.document_id} className="block">
              <input
                type="checkbox"
                checked={selected.includes(source.document_id)}
                disabled={
                  selected.length >= 16 &&
                  !selected.includes(source.document_id)
                }
                onChange={(event) => {
                  request.current = null;
                  setSelected((value) =>
                    event.target.checked
                      ? [...value, source.document_id]
                      : value.filter((id) => id !== source.document_id),
                  );
                }}
              />
              {source.title} · редакция {source.revision_number}
            </label>
          ))}
          <Button disabled={page <= 1} onClick={() => void search(page - 1)}>
            Предыдущие БТ
          </Button>
          <Button disabled={!hasMore} onClick={() => void search(page + 1)}>
            Следующие БТ
          </Button>
        </fieldset>
      )}
      <Button
        disabled={!canEdit || disabled || busy || selected.length === 0}
        onClick={() => void run()}
      >
        {busy ? 'Проверка выполняется…' : 'Проверить соответствие'}
      </Button>
      {disabled && <p>Перед проверкой сохраните ручные правки.</p>}
      {view?.checks.length === 0 && <p>Проверок ещё нет.</p>}
      {[...(view?.checks ?? [])].reverse().map((check) => (
        <article key={check.id} className="border-t pt-2">
          <p role="status">
            {check.qualifies &&
            check.current &&
            !disabled &&
            view?.document.id === document.id &&
            view.document.revision === document.revision
              ? 'Соответствие подтверждено'
              : 'Соответствие не подтверждено'}
            {check.current ? '' : ' · исторический результат'}
          </p>
          <p>
            Смысловая оценка: {check.result?.quality_status ?? 'UNKNOWN'} ·
            доказательства: {check.result?.evidence_status ?? 'INCOMPLETE'}
          </p>
          {(check.reason_code || check.result?.reason_code) && (
            <p>{check.reason_code || check.result?.reason_code}</p>
          )}
          {check.result?.report && (
            <details>
              <summary>Требования и замечания</summary>
              {check.result.report.requirements.map((row) => (
                <div key={row.id} className="mt-2">
                  <p>
                    {row.id}: {row.coverage} · {row.consistency}
                  </p>
                  <p>{row.reason}</p>
                  {row.references.map((ref, index) => (
                    <blockquote key={index}>
                      Раздел {ref.section}: {ref.quote}
                    </blockquote>
                  ))}
                </div>
              ))}
              {check.result.report.additional_rules.map((rule, index) => (
                <p key={index}>
                  {rule.kind}: {rule.reason}
                </p>
              ))}
              <p>
                Совокупные ограничения: {check.result.report.joint.consistency}{' '}
                · {check.result.report.joint.reason}
              </p>
            </details>
          )}
        </article>
      ))}
    </section>
  );
}
