import { Button } from '@/components/ui/button';
import {
  assistantCommandId,
  useAssistantAccess,
  useAssistantCatalog,
  useAssistantSession,
  useAssistantSessions,
  useCreateAssistant,
} from '@/hooks/use-sql-assistant-request';
import { useFetchUserInfo } from '@/hooks/use-user-setting-request';
import type { AssistantReference } from '@/interfaces/database/sql-assistant';
import { useDebounce } from 'ahooks';
import {
  ArrowRight,
  Database,
  LoaderCircle,
  MessageSquare,
  Plus,
  Search,
} from 'lucide-react';
import { useRef, useState } from 'react';
import { useSearchParams } from 'react-router';
import { AssistantCatalog } from './catalog';
import { assistantFailure } from './failure';
import { MentionInput } from './mention-input';
import { AssistantWorkspace } from './workspace';
import './workspace.css';

export default function SqlAssistantPage() {
  const { data: user, loading } = useFetchUserInfo();
  const actor = user.id || '';
  const access = useAssistantAccess(actor);
  const canCreate = access.data?.capabilities.create === true;
  const [params, setParams] = useSearchParams();
  const id = params.get('session');
  const catalog = params.get('view') === 'catalog';
  const sessions = useAssistantSessions(actor);
  const session = useAssistantSession(actor, id);
  const create = useCreateAssistant(actor);
  const [question, setQuestion] = useState('');
  const [references, setReferences] = useState<AssistantReference[]>([]);
  const [mentionSearch, setMentionSearch] = useState('');
  const [mentionOpen, setMentionOpen] = useState(false);
  const mentions = useAssistantCatalog(
    actor,
    useDebounce(mentionSearch, { wait: 250 }),
    mentionOpen && !id && !catalog,
  );
  const questionRef = useRef<HTMLTextAreaElement>(null);
  const attempt = useRef<{
    question: string;
    signature: string;
    key: string;
  } | null>(null);
  const navigate = (sessionId: string | null, view?: string) =>
    setParams({
      ...(sessionId ? { session: sessionId } : {}),
      ...(view ? { view } : {}),
    });
  const start = async () => {
    if (!question.trim() || create.isPending || !canCreate) return;
    const selected = references
      .filter((item) => item.kind === 'TABLE')
      .map((item) => ({ kind: 'TABLE' as const, id: item.id }));
    const signature = JSON.stringify([question.trim(), selected]);
    if (attempt.current?.signature !== signature)
      attempt.current = {
        question: question.trim(),
        signature,
        key: assistantCommandId(),
      };
    try {
      const result = await create.mutateAsync({
        question: attempt.current.question,
        client_request_id: attempt.current.key,
        ...(selected.length ? { references: selected } : {}),
      });
      attempt.current = null;
      setQuestion('');
      setReferences([]);
      navigate(result.id);
    } catch (failure) {
      if (
        ['EDIT', 'DENIED', 'RESELECT', 'REFRESH'].includes(
          assistantFailure(failure).recovery,
        )
      )
        attempt.current = null;
      /* The retained question and contextual error allow a safe retry. */
    }
  };
  if (loading)
    return (
      <p role="status" className="p-6">
        Проверяем учётную запись…
      </p>
    );
  return (
    <div className="sa-shell" data-testid="sql-assistant">
      <aside className="sa-sidebar" aria-label="Исследования SQL">
        <div className="sa-sidebar-heading">
          <Database className="size-5" />
          <span>Исследования</span>
        </div>
        <Button
          disabled={!canCreate}
          onClick={() => {
            navigate(null);
            requestAnimationFrame(() => questionRef.current?.focus());
          }}
        >
          <Plus className="size-4" />
          Новое исследование
        </Button>
        <nav aria-label="Разделы помощника">
          <button
            className={!catalog ? 'sa-nav-active' : ''}
            onClick={() => navigate(id)}
          >
            <MessageSquare className="size-4" />
            Вопросы к данным
          </button>
          <button
            className={catalog ? 'sa-nav-active' : ''}
            onClick={() => navigate(id, 'catalog')}
          >
            <Search className="size-4" />
            Каталог данных
          </button>
        </nav>
        <p className="sa-caption">Недавние исследования</p>
        {sessions.isPending ? (
          <p role="status">Загружаем исследования…</p>
        ) : sessions.isError ? (
          <div role="alert">
            <p>Список исследований недоступен.</p>
            <Button variant="ghost" onClick={() => void sessions.refetch()}>
              Повторить
            </Button>
          </div>
        ) : (
          <div className="sa-recent">
            {sessions.data.sessions.length ? (
              sessions.data.sessions.map((item) => (
                <button
                  key={item.id}
                  aria-current={id === item.id ? 'page' : undefined}
                  onClick={() => navigate(item.id)}
                  title={item.title}
                >
                  {item.title}
                </button>
              ))
            ) : (
              <p className="text-sm text-text-secondary">
                Здесь появятся сохранённые вопросы.
              </p>
            )}
          </div>
        )}
        <p className="sa-sidebar-footer">
          Данные и метаданные доступны в пределах ваших прав.
        </p>
      </aside>
      <div
        className={id && !catalog ? 'sa-content sa-content-chat' : 'sa-content'}
      >
        {catalog ? (
          <AssistantCatalog
            actor={actor}
            onUse={(entity) => {
              setQuestion(`Исследуй таблицу ${entity.fqn}: `);
              setReferences([
                {
                  kind: 'TABLE',
                  id: entity.id,
                  title: entity.fqn,
                  fqn: entity.fqn,
                },
              ]);
              navigate(null);
              requestAnimationFrame(() => questionRef.current?.focus());
            }}
          />
        ) : id ? (
          session.isPending ? (
            <p role="status" className="p-8">
              Открываем исследование…
            </p>
          ) : session.isError ? (
            <div className="sa-empty" role="alert">
              <h1>Не удалось открыть исследование</h1>
              <p>
                Проверьте доступ и подключение. Сохранённая работа останется на
                сервере.
              </p>
              <Button onClick={() => void session.refetch()}>Повторить</Button>
            </div>
          ) : (
            <AssistantWorkspace
              key={id}
              actor={actor}
              session={session.data}
              refresh={async () => {
                await session.refetch();
              }}
            />
          )
        ) : (
          <section className="sa-welcome" aria-labelledby="sa-welcome-title">
            <h1 id="sa-welcome-title">Что хотите узнать о данных?</h1>
            <p className="sa-description">
              Задайте вопрос. Помощник уточнит только то, что нужно для точного
              ответа.
            </p>
            <form
              className="sa-composer"
              onSubmit={(event) => {
                event.preventDefault();
                void start();
              }}
            >
              <label htmlFor="sa-question" className="sr-only">
                Вопрос к данным
              </label>
              <MentionInput
                id="sa-question"
                ref={questionRef}
                value={question}
                onChange={setQuestion}
                onSend={() => void start()}
                references={references}
                onReferences={setReferences}
                options={(mentions.data?.entities || []).map((entity) => ({
                  kind: 'TABLE',
                  id: entity.id,
                  title: entity.fqn,
                  fqn: entity.fqn,
                }))}
                loading={mentions.isFetching}
                error={mentions.isError}
                onSearch={(query, open) => {
                  setMentionSearch(query);
                  setMentionOpen(open);
                }}
                maxLength={20000}
                placeholder="Например: сравни число поисковых запросов по дням за август и сентябрь 2026"
                rows={3}
              />
              <div className="sa-composer-actions">
                <p>Упомяните таблицу через @, если знаете источник.</p>
                <Button
                  type="submit"
                  disabled={!question.trim() || create.isPending || !canCreate}
                >
                  {create.isPending ? (
                    <LoaderCircle className="size-4 animate-spin" />
                  ) : (
                    <ArrowRight className="size-4" />
                  )}
                  Отправить
                </Button>
              </div>
              {create.isError && (
                <p role="alert">{assistantFailure(create.error).message}</p>
              )}
            </form>
          </section>
        )}
      </div>
    </div>
  );
}
