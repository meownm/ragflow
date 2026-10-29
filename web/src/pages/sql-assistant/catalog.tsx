import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { useAssistantCatalog } from '@/hooks/use-sql-assistant-request';
import type { CatalogEntity } from '@/pages/openmetadata/types';
import { Search } from 'lucide-react';
import { useState } from 'react';

export function AssistantCatalog({
  actor,
  onUse,
}: {
  actor: string;
  onUse: (entity: CatalogEntity) => void;
}) {
  const [input, setInput] = useState('');
  const [query, setQuery] = useState('');
  const result = useAssistantCatalog(actor, query);
  return (
    <section className="sa-catalog">
      <h1>Каталог данных</h1>
      <p className="sa-description">
        Найдите таблицу, изучите поля и используйте её в вопросе. Права на
        метаданные и исполнение SQL проверяются отдельно.
      </p>
      <form
        className="flex gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          setQuery(input);
        }}
      >
        <Input
          aria-label="Поиск в каталоге"
          value={input}
          onChange={(event) => setInput(event.target.value)}
          placeholder="Таблица, поле или бизнес-термин"
          maxLength={2000}
        />
        <Button type="submit">
          <Search className="size-4" />
          Найти
        </Button>
      </form>
      {result.isPending ? (
        <p role="status">Ищем разрешённые метаданные…</p>
      ) : result.isError ? (
        <div role="alert">
          <p>Каталог недоступен. Проверьте права или повторите поиск позже.</p>
          <Button variant="outline" onClick={() => void result.refetch()}>
            Повторить
          </Button>
        </div>
      ) : (
        <>
          {result.data.freshness && (
            <p className="text-sm text-text-secondary">
              Снимок каталога:{' '}
              {result.data.freshness.snapshot_at || 'время неизвестно'}
              {result.data.freshness.stale ? ' · требует обновления' : ''}
            </p>
          )}
          {result.data.warnings?.map((warning) => (
            <p key={warning} role="status">
              {warning}
            </p>
          ))}
          {!result.data.entities.length && (
            <p>
              В доступном каталоге совпадений нет. Уточните название; область
              поиска не расширяется за пределы ваших прав.
            </p>
          )}
          {result.data.entities.map((entity) => (
            <article key={entity.id} className="sa-catalog-entity">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <h2>{entity.name || entity.technical_name}</h2>
                  <p className="break-all font-mono text-xs text-text-secondary">
                    {entity.fqn}
                  </p>
                </div>
                <Button variant="outline" onClick={() => onUse(entity)}>
                  Использовать в вопросе
                </Button>
              </div>
              <p>{entity.description || 'Описание не заполнено.'}</p>
              <p className="text-sm text-text-secondary">
                Владелец: {entity.owners.join(', ') || 'не указан'} · Полей:{' '}
                {entity.column_count} · Версия: {entity.version ?? 'неизвестна'}
              </p>
              <details>
                <summary>Поля и ограничения</summary>
                <div className="sa-table-scroll">
                  <table>
                    <thead>
                      <tr>
                        <th>Поле</th>
                        <th>Тип</th>
                        <th>Описание</th>
                        <th>Ограничение</th>
                      </tr>
                    </thead>
                    <tbody>
                      {entity.column_details?.map((column) => (
                        <tr key={column.fqn}>
                          <td>{column.name}</td>
                          <td>{column.data_type}</td>
                          <td>{column.description || '—'}</td>
                          <td>{column.constraint || '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <p className="text-xs text-text-secondary">
                  Происхождение данных не подтверждает допустимость соединения.
                  Ключи и кратность проверяются при подготовке запроса.
                </p>
              </details>
            </article>
          ))}
        </>
      )}
    </section>
  );
}
