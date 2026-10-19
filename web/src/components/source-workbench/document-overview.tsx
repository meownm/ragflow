import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  documentTypes,
  type DocumentOverviewData,
} from '@/services/linked-document-service';
import { useState } from 'react';

const states: Record<string, string> = {
  current: 'Актуален',
  draft: 'Рабочая редакция',
  needs_review: 'Требуется проверка',
  awaiting_decision: 'Ожидает согласования',
  preparing: 'Подготовка предложений',
  blocked: 'Нужен разбор',
  cycle: 'Цикл связей',
  waiting_upstream: 'Ожидает изменения оснований',
  needs_implementation: 'Ожидает реализации',
};
const actions: Record<string, string> = {
  text: 'Открыть документ',
  sources: 'Проверить основания',
  changes: 'Посмотреть предложения',
  implementation: 'Подтвердить реализацию',
};

export function DocumentOverview({
  data,
  disabled,
  open,
}: {
  data: DocumentOverviewData;
  disabled: boolean;
  open(id: string, action?: string): void;
}) {
  const [filter, setFilter] = useState('attention');
  const [query, setQuery] = useState('');
  const rows = data.items.filter(
    (item) =>
      (filter === 'all' || item.state !== 'current') &&
      (filter !== 'mine' || item.mine) &&
      item.title.toLocaleLowerCase().includes(query.toLocaleLowerCase()),
  );
  const ready = data.readiness;
  return (
    <section aria-label="Обзор актуализации" className="mb-6 space-y-3">
      <h2 className="font-semibold">Документы пространства</h2>
      <div
        aria-live="polite"
        className="rounded border border-border-button p-3 text-sm"
      >
        {!ready.configured ? (
          'Комплект не настроен. Владелец может отметить обязательные документы в их свойствах.'
        ) : (
          <>
            <p>
              Документы согласованы: {ready.documents_agreed ? 'да' : 'нет'}
            </p>
            <p>
              Сопровождение завершено:{' '}
              {ready.maintenance_complete ? 'да' : 'нет'}
            </p>
            <p>Обязательных документов: {ready.required_count}</p>
          </>
        )}
        {ready.blockers.map((reason, index) => (
          <button
            key={`${reason.document_id}-${index}`}
            disabled={disabled}
            className="mt-1 block text-start underline"
            onClick={() => open(reason.document_id, reason.action)}
          >
            {data.items.find((item) => item.id === reason.document_id)?.title}:{' '}
            {reason.message}
          </button>
        ))}
      </div>
      <div className="flex flex-wrap gap-2">
        <label className="text-sm">
          Показать{' '}
          <select
            aria-label="Фильтр документов"
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
            className="rounded border border-border-button bg-bg-base p-2"
          >
            <option value="attention">Требуют внимания</option>
            <option value="mine">Мои задачи</option>
            <option value="all">Все</option>
          </select>
        </label>
        <Input
          aria-label="Найти документ по названию"
          placeholder="Название документа"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
      </div>
      {!rows.length ? (
        <div className="text-sm">
          <p>
            {data.items.length
              ? 'Нет документов по выбранному фильтру.'
              : 'Создайте документ или подключите источник.'}
          </p>
          {filter !== 'all' && (
            <Button
              variant="outline"
              onClick={() => {
                setFilter('all');
                setQuery('');
              }}
            >
              Показать все документы
            </Button>
          )}
        </div>
      ) : (
        <ul className="grid gap-3 lg:grid-cols-2">
          {rows.map((item) => (
            <li
              key={item.id}
              className="min-w-0 rounded border border-border-button p-3 text-sm"
            >
              <p className="font-medium">{item.title}</p>
              <p>
                {documentTypes[item.document_type]} ·{' '}
                {states[item.state] || item.state}
              </p>
              <p>Ответственный: {item.responsible_name}</p>
              {item.reasons.map((reason, index) => (
                <p key={index} className="mt-1 text-text-secondary">
                  {reason.message}
                </p>
              ))}
              <Button
                aria-label={`${item.title}: ${actions[item.action] || actions.text}`}
                className="mt-2 h-auto whitespace-normal"
                disabled={disabled}
                onClick={() => open(item.id, item.action)}
              >
                {actions[item.action] || actions.text}
              </Button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
