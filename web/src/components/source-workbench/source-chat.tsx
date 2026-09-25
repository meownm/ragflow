import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  chatWithSourceWorkspace,
  sourceRequestError,
  type SourceCandidate,
  type SourceChatMessage,
  type SourceWorkspace,
} from '@/services/source-workbench-service';
import { useState, type FormEvent } from 'react';

interface Turn extends SourceChatMessage {
  sources?: SourceCandidate[];
}

/** A small chat surface that only uses the source workspace passed by its host. */
export function SourceChat({ workspace }: { workspace: SourceWorkspace }) {
  const [question, setQuestion] = useState('');
  const [turns, setTurns] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const ask = async (event: FormEvent) => {
    event.preventDefault();
    const text = question.trim();
    if (!text || busy || !workspace.selected_documents.length) return;
    setBusy(true);
    setError('');
    try {
      const answer = await chatWithSourceWorkspace(
        workspace,
        text,
        [...turns].reverse().find((turn) => turn.role === 'user')?.content ||
          '',
      );
      setTurns((previous) => [
        ...previous,
        { role: 'user', content: text },
        { role: 'assistant', content: answer.answer, sources: answer.sources },
      ]);
      setQuestion('');
    } catch (cause) {
      setError(sourceRequestError(cause, 'Не удалось получить ответ'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="mt-8 rounded-md border border-border-button p-5">
      <h2 className="text-lg font-semibold">Чат по выбранным статьям</h2>
      <p className="mt-1 text-sm text-text-secondary">
        Ответ строится только по текущей подборке и сопровождается источниками.
      </p>
      {!workspace.selected_documents.length && (
        <p className="mt-4 text-sm text-text-secondary">
          Сначала выберите хотя бы одну статью.
        </p>
      )}
      <ol className="mt-5 space-y-4">
        {turns.map((turn, index) => (
          <li key={index} className="rounded-md bg-bg-card p-3">
            <p className="mb-1 text-xs font-semibold text-text-secondary">
              {turn.role === 'user' ? 'Вы' : 'Ответ'}
            </p>
            <p className="whitespace-pre-wrap text-sm text-text-primary">
              {turn.content}
            </p>
            {turn.sources && turn.sources.length > 0 && (
              <ul className="mt-3 flex flex-wrap gap-2 text-xs">
                {turn.sources.map((source) => (
                  <li key={`${source.dataset_id}:${source.document_id}`}>
                    [{source.citation_numbers?.join(', ') || 'источник'}]{' '}
                    {source.source_url ? (
                      <a
                        href={source.source_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-accent-primary hover:underline"
                      >
                        {source.path.join(' › ')}
                      </a>
                    ) : (
                      <span className="text-text-secondary">
                        {source.path.join(' › ')}
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </li>
        ))}
      </ol>
      <form className="mt-5 flex gap-2" onSubmit={ask}>
        <Input
          aria-label="Вопрос по выбранным статьям"
          placeholder="Задайте вопрос по выбранным статьям"
          value={question}
          maxLength={500}
          onChange={(event) => setQuestion(event.target.value)}
          disabled={busy || !workspace.selected_documents.length}
        />
        <Button
          type="submit"
          disabled={
            busy || !question.trim() || !workspace.selected_documents.length
          }
        >
          Спросить
        </Button>
      </form>
      {error && (
        <p role="alert" className="mt-3 text-sm text-state-error">
          {error}
        </p>
      )}
    </section>
  );
}
