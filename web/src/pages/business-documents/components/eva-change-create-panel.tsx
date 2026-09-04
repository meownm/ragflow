import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { Routes } from '@/routes';
import {
  createEvaDocumentChange,
  listEvaDocumentChanges,
  searchEvaDocumentSources,
} from '@/services/business-document-service';
import { useMutation, useQuery } from '@tanstack/react-query';
import {
  ArrowRight,
  ExternalLink,
  FilePenLine,
  LoaderCircle,
  Search,
} from 'lucide-react';
import { FormEvent, useState } from 'react';
import { Link, useNavigate } from 'react-router';
import type { EvaDocumentChangeState, EvaDocumentSource } from '../types';
import { appendVoiceTranscript, VoiceInput } from './voice-input';

const stateLabels: Record<EvaDocumentChangeState, string> = {
  EDITING: 'Редактирование',
  APPROVED: 'Согласовано',
  PREPARING_EVA_DRAFT: 'Запись черновика',
  EVA_DRAFT_READY: 'Черновик в EVA',
  PUBLISHING: 'Публикация',
  PUBLISHED: 'Опубликовано',
};

export function EvaChangeCreatePanel() {
  const navigate = useNavigate();
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState<EvaDocumentSource | null>(null);
  const [changeSummary, setChangeSummary] = useState('');
  const searchMutation = useMutation({
    mutationFn: () => searchEvaDocumentSources(query.trim()),
  });
  const recentQuery = useQuery({
    queryKey: ['eva-document-changes', 'recent'],
    queryFn: () => listEvaDocumentChanges(1, 8),
    retry: false,
  });
  const createMutation = useMutation({
    mutationFn: createEvaDocumentChange,
    onSuccess: (change) =>
      navigate(`${Routes.BusinessDocuments}/eva/${change.change_id}`),
  });

  const search = (event: FormEvent) => {
    event.preventDefault();
    setSelected(null);
    searchMutation.mutate();
  };

  const create = (event: FormEvent) => {
    event.preventDefault();
    if (!selected || !changeSummary.trim() || createMutation.isPending) return;
    createMutation.mutate({
      connector_id: selected.connector_id,
      document_id: selected.id,
      change_summary: changeSummary.trim(),
    });
  };

  return (
    <div
      className="min-h-0 overflow-y-auto px-6 py-7 scrollbar-auto lg:px-8"
      data-testid="eva-change-create-panel"
    >
      <div className="flex items-center gap-2 text-sm font-medium text-accent-primary">
        <FilePenLine className="size-4" />
        Доработать существующий документ
      </div>
      <h2 className="mt-3 text-xl font-semibold tracking-tight text-text-primary">
        Документ EVA
      </h2>
      <p className="mt-2 text-sm leading-6 text-text-secondary">
        Найдите опубликованный документ. Агент Раггер закрепит исходную версию и
        не изменит EVA до отдельного подтверждения.
      </p>

      <form className="mt-6" onSubmit={search}>
        <label className="block space-y-2 text-sm font-medium">
          <span>Поиск по названию, коду или тексту</span>
          <div className="flex gap-2">
            <Input
              value={query}
              maxLength={500}
              aria-label="Поиск документа EVA"
              placeholder="Например, BR-42 или переводы"
              onChange={(event) => setQuery(event.target.value)}
              suffix={
                <VoiceInput
                  label="Поисковый запрос EVA"
                  onTranscript={(transcript) =>
                    setQuery((value) =>
                      appendVoiceTranscript(value, transcript, 500),
                    )
                  }
                  testId="voice-input-eva-search"
                />
              }
            />
            <Button
              type="submit"
              variant="outline"
              loading={searchMutation.isPending}
              aria-label="Найти документ EVA"
            >
              <Search className="size-4" />
              Найти
            </Button>
          </div>
        </label>
      </form>

      {searchMutation.error && (
        <p className="mt-3 text-sm text-state-error" role="alert">
          {searchMutation.error.message}
        </p>
      )}

      {searchMutation.data && (
        <div
          className="mt-5 border-y border-border-button"
          data-testid="eva-source-results"
        >
          <div className="flex items-center justify-between py-2 text-xs text-text-secondary">
            <span>Найдено документов</span>
            <span>{searchMutation.data.items.length}</span>
          </div>
          {!searchMutation.data.items.length && (
            <p className="border-t border-border-button py-5 text-sm text-text-secondary">
              Совпадений нет. Проверьте запрос или выполните пустой поиск.
            </p>
          )}
          {searchMutation.data.items.map((source) => {
            const isSelected = selected?.id === source.id;
            return (
              <button
                key={`${source.connector_id}:${source.id}`}
                type="button"
                className={`w-full border-t px-3 py-3 text-start transition-colors ${
                  isSelected
                    ? 'border-accent-primary bg-accent-primary/5'
                    : 'border-border-button hover:bg-bg-card'
                }`}
                onClick={() => setSelected(source)}
                data-testid="eva-source-result"
              >
                <span className="flex items-center justify-between gap-3">
                  <span className="min-w-0 truncate text-sm font-medium text-text-primary">
                    {source.name}
                  </span>
                  {source.code && (
                    <span className="shrink-0 font-mono text-[11px] text-text-disabled">
                      {source.code}
                    </span>
                  )}
                </span>
                <span className="mt-1 block line-clamp-2 text-xs leading-5 text-text-secondary">
                  {source.excerpt || 'Нет текстового фрагмента'}
                </span>
                <span className="mt-1 block text-[11px] text-text-disabled">
                  {source.connector_name}
                </span>
              </button>
            );
          })}
        </div>
      )}

      {selected && (
        <form className="mt-6" onSubmit={create} data-testid="eva-change-form">
          <div className="border-s-2 border-accent-primary ps-4">
            <p className="text-xs font-medium uppercase tracking-wide text-text-secondary">
              Выбран исходник
            </p>
            <p className="mt-1 text-sm font-medium text-text-primary">
              {selected.name}
            </p>
            <p className="mt-1 break-all font-mono text-[10px] text-text-disabled">
              {selected.version}
            </p>
          </div>
          <label className="mt-5 block space-y-2 text-sm font-medium">
            <span>Что нужно изменить</span>
            <div className="relative">
              <Textarea
                value={changeSummary}
                maxLength={50000}
                autoSize={{ minRows: 5, maxRows: 12 }}
                resize="vertical"
                aria-label="Описание доработки"
                placeholder="Опишите бизнес-изменение, границы и ожидаемый результат."
                className="pe-11"
                onChange={(event) => setChangeSummary(event.target.value)}
              />
              <div className="absolute end-2 top-2">
                <VoiceInput
                  label="Доработка EVA"
                  onTranscript={(transcript) =>
                    setChangeSummary((value) =>
                      appendVoiceTranscript(value, transcript, 50000),
                    )
                  }
                  testId="voice-input-eva-change-summary"
                />
              </div>
            </div>
          </label>
          {createMutation.error && (
            <p className="mt-3 text-sm text-state-error" role="alert">
              {createMutation.error.message}
            </p>
          )}
          <div className="mt-5 flex justify-end">
            <Button
              type="submit"
              variant="accent"
              size="lg"
              loading={createMutation.isPending}
              disabled={!changeSummary.trim()}
            >
              <ArrowRight className="size-4" />
              Открыть доработку
            </Button>
          </div>
        </form>
      )}

      {!!recentQuery.data?.items.length && (
        <section
          className="mt-8 border-t border-border-button pt-5"
          aria-label="Последние доработки EVA"
        >
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-medium">Последние доработки EVA</h3>
            <span className="text-xs text-text-secondary">
              {recentQuery.data.total}
            </span>
          </div>
          <div className="mt-2">
            {recentQuery.data.items.map((change) => (
              <Link
                key={change.change_id}
                to={`${Routes.BusinessDocuments}/eva/${change.change_id}`}
                className="group flex items-center gap-3 border-b border-border-button py-3 first:border-t hover:text-accent-primary"
              >
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-medium">
                    {change.document_name}
                  </span>
                  <span className="mt-0.5 block truncate text-xs text-text-secondary">
                    {change.change_summary}
                  </span>
                </span>
                <Badge
                  variant={
                    change.workflow_state === 'PUBLISHED'
                      ? 'success'
                      : 'secondary'
                  }
                >
                  {stateLabels[change.workflow_state]}
                </Badge>
                <ExternalLink className="size-3.5 text-text-disabled group-hover:text-accent-primary" />
              </Link>
            ))}
          </div>
        </section>
      )}

      {recentQuery.isLoading && (
        <div className="mt-7 flex items-center gap-2 text-xs text-text-secondary">
          <LoaderCircle className="size-3.5 animate-spin" />
          Загружаем последние доработки
        </div>
      )}
    </div>
  );
}
