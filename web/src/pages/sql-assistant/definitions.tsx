import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { useAssistantDefinitions } from '@/hooks/use-sql-assistant-request';
import type {
  AssistantDefinition,
  AssistantQuery,
  AssistantSession,
  DefinitionFreshness,
} from '@/interfaces/database/sql-assistant';
import dayjs from 'dayjs';
import { useState } from 'react';
import { assistantTables } from './builder';
import type { SendCommand } from './workspace';

const FreshnessLabels: Record<DefinitionFreshness, string> = {
  CURRENT: 'Актуальная версия',
  SUPERSEDED: 'Есть новая версия',
  RETIRED: 'Определение отозвано',
  METADATA_CHANGED: 'Схема источника изменилась',
  UNKNOWN: 'Актуальность не удалось проверить',
  NOT_APPLICABLE: 'Определение не выбрано',
};

export function DefinitionStatus({
  value,
}: {
  value: Pick<
    AssistantSession,
    'definition_status' | 'definition' | 'definition_freshness'
  >;
}) {
  return (
    <>
      {value.definition_status === 'DEFINITION_VALIDATED' &&
      value.definition ? (
        <>
          {value.definition.title} · версия {value.definition.version}
          <br />
          {FreshnessLabels[value.definition_freshness || 'UNKNOWN']}
        </>
      ) : (
        <>
          Бизнес-смысл не подтверждён
          {value.definition_freshness === 'UNKNOWN' && (
            <>
              <br />
              {FreshnessLabels.UNKNOWN}
            </>
          )}
        </>
      )}
    </>
  );
}

export function AssistantDefinitions({
  actor,
  session,
  send,
  busy,
  localChanges,
}: {
  actor: string;
  session: AssistantSession;
  send: SendCommand;
  busy: boolean;
  localChanges: boolean;
}) {
  const registry = useAssistantDefinitions(actor, session);
  const [selected, setSelected] = useState('');
  const canEdit =
    session.project.capabilities.edit !== false && !session.project.archived_at;
  const definitions = registry.data?.definitions || [];
  const choice = definitions.find((item) => item.definition_id === selected);
  const mode = session.draft?.mode || session.mode;
  const apply = async () => {
    if (choice)
      await send('use_definition', {
        definition_id: choice.definition_id,
        version: choice.version,
      });
  };
  const run = (operation: () => Promise<unknown>) =>
    void operation().catch(() => undefined);
  const raw = session.structured_query || session.project.artifacts?.query;
  const query =
    raw && !('mode' in raw) && Array.isArray(raw.select)
      ? (raw as AssistantQuery)
      : null;
  return (
    <section className="sa-panel" aria-label="Режим расчёта и определения">
      <h2>Режим расчёта</h2>
      <p className="mb-3 text-sm">
        {mode === 'AGREED'
          ? 'По согласованному определению'
          : 'Исследование аналитика'}
        {session.draft && ' · черновик'}
      </p>
      <p className="mb-4 text-sm text-text-secondary">
        По определению доступны утверждённая формула и выбранные параметры.
        Ручной SQL и изменение формулы создают исследовательскую версию; прошлые
        результаты сохраняют свой статус.
      </p>
      {registry.isPending && session.project.artifact_ids.schema && (
        <p role="status">Проверяем доступные определения…</p>
      )}
      {registry.isError && (
        <p role="alert">
          Не удалось проверить реестр определений. Обновите состояние;
          согласованный запуск недоступен.
        </p>
      )}
      {registry.isSuccess && definitions.length === 0 && (
        <p className="mb-3 text-sm">
          Для выбранной схемы пока нет утверждённых определений.
        </p>
      )}
      <div className="sa-result-tools">
        <label>
          Согласованное определение
          <select
            aria-label="Согласованное определение"
            value={selected}
            onChange={(event) => setSelected(event.target.value)}
            disabled={busy || !canEdit || localChanges}
          >
            <option value="">Выберите определение</option>
            {definitions.map((item) => (
              <option key={item.definition_id} value={item.definition_id}>
                {item.title} · версия {item.version} ·{' '}
                {FreshnessLabels[item.freshness]}
              </option>
            ))}
          </select>
        </label>
        <Button
          variant="outline"
          disabled={
            busy ||
            !canEdit ||
            localChanges ||
            Boolean(session.draft) ||
            choice?.freshness !== 'CURRENT'
          }
          onClick={() => run(apply)}
        >
          Создать черновик по определению
        </Button>
        <Button
          variant="ghost"
          disabled={
            busy ||
            !canEdit ||
            localChanges ||
            mode === 'EXPLORATORY' ||
            !session.project.artifact_ids.query
          }
          onClick={() => run(() => send('start_exploration'))}
        >
          Перейти к исследовательской правке
        </Button>
      </div>
      {localChanges && (
        <p className="text-sm">
          Сохраните или отмените введённые изменения перед сменой режима.
        </p>
      )}
      {session.draft && (
        <p className="text-xs text-text-secondary">
          Примените или отмените текущий черновик перед выбором другого
          определения.
        </p>
      )}
      {choice && <DefinitionDetails definition={choice} />}
      {registry.data?.can_publish && query && (
        <PublishDefinition
          key={`${session.project.artifact_ids.query}:${session.draft?.revision || 0}`}
          session={session}
          query={query}
          definitions={definitions}
          send={send}
          busy={busy || localChanges || Boolean(session.draft)}
        />
      )}
    </section>
  );
}

export function DefinitionDetails({
  definition,
}: {
  definition: AssistantDefinition;
}) {
  return (
    <details className="mt-3">
      <summary>Смысл, правила и версия определения</summary>
      <p className="my-2 text-sm">{definition.description}</p>
      <p className="text-xs text-text-secondary">
        Версия {definition.version} · утверждено{' '}
        {dayjs(definition.approved_at).format('DD.MM.YYYY HH:mm')} ·{' '}
        {FreshnessLabels[definition.freshness]}
      </p>
      <p className="my-2 text-sm">
        Часовой пояс: {definition.contract.timezone}. Временное поле:{' '}
        {definition.contract.time_column_id || 'Не используется'}. Повторная
        агрегация сохранённой выдачи запрещена.
      </p>
      {definition.contract.metrics.map((item) => (
        <p className="text-sm" key={item.select_item_id}>
          {item.select_item_id} · {item.unit} ·{' '}
          {item.null_policy === 'EXCLUDE_NULLS_ZERO_IF_EMPTY'
            ? 'NULL не считаются; пустой набор даёт 0'
            : 'NULL пропускаются; пустой набор даёт NULL'}
        </p>
      ))}
    </details>
  );
}

function PublishDefinition({
  session,
  query,
  definitions,
  send,
  busy,
}: {
  session: AssistantSession;
  query: AssistantQuery;
  definitions: AssistantDefinition[];
  send: SendCommand;
  busy: boolean;
}) {
  const [target, setTarget] = useState('');
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [units, setUnits] = useState<Record<string, string>>({});
  const [timezone, setTimezone] = useState(query.timezone || 'UTC');
  const [mutable, setMutable] = useState<string[]>([]);
  const temporal = new Set(
    query.filters
      .filter((item) => ['date', 'datetime'].includes(item.parameter_type))
      .map((item) => item.column_id),
  );
  query.select
    .filter((item) => item.kind === 'date_bucket')
    .forEach((item) => temporal.add(item.column_id));
  const [timeColumn, setTimeColumn] = useState(
    temporal.size === 1 ? [...temporal][0] : '',
  );
  const columns = assistantTables(session).flatMap((table) => table.columns);
  const metrics = query.select.filter(
    (item) => !['column', 'date_bucket'].includes(item.kind),
  );
  const selected = definitions.find((item) => item.definition_id === target);
  const submit = async () => {
    await send('publish_definition', {
      definition_id: selected?.definition_id || null,
      expected_version: selected?.version || 0,
      title,
      description,
      query: { ...query, timezone },
      base_query_artifact_id: session.project.artifact_ids.query,
      contract: {
        grain: query.select
          .filter((item) => ['column', 'date_bucket'].includes(item.kind))
          .map((item) => item.id),
        metrics: metrics.map((item) => ({
          select_item_id: item.id,
          unit: units[item.id],
          null_policy: ['count', 'count_distinct'].includes(item.kind)
            ? 'EXCLUDE_NULLS_ZERO_IF_EMPTY'
            : 'IGNORE_NULLS_NULL_IF_EMPTY',
          reaggregation: 'FORBIDDEN',
        })),
        time_column_id: timeColumn || null,
        timezone,
        mutable_parameters: mutable,
      },
    });
    setTitle('');
    setDescription('');
  };
  return (
    <details className="mt-4">
      <summary>Администратору: утвердить текущую формулу</summary>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void submit().catch(() => undefined);
        }}
        className="space-y-3 mt-3"
      >
        <p className="text-sm">
          Утверждается текущий принятый расчёт с его полями, группировкой, JOIN
          и условиями. Выберите изменяемые параметры; остальные условия
          останутся фиксированными. Новая версия сохраняет предыдущие
          определения.
        </p>
        <label className="block">
          Определение для публикации
          <select
            aria-label="Определение для публикации"
            value={target}
            onChange={(event) => setTarget(event.target.value)}
            disabled={busy}
          >
            <option value="">Новое определение</option>
            {definitions.map((item) => (
              <option key={item.definition_id} value={item.definition_id}>
                {item.title} · новая версия после {item.version}
              </option>
            ))}
          </select>
        </label>
        <label className="block">
          Название
          <Input
            aria-label="Название определения"
            required
            maxLength={200}
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            disabled={busy}
          />
        </label>
        <label className="block">
          Смысл показателей и основания утверждения
          <Textarea
            aria-label="Смысл определения"
            required
            maxLength={2000}
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            disabled={busy}
          />
        </label>
        {metrics.map((item) => (
          <label className="block" key={item.id}>
            Единица · {item.alias}
            <Input
              aria-label={`Единица ${item.alias}`}
              value={units[item.id] || ''}
              maxLength={100}
              required
              disabled={busy}
              onChange={(event) =>
                setUnits({ ...units, [item.id]: event.target.value })
              }
            />
          </label>
        ))}
        <div className="sa-result-tools">
          <label>
            Часовой пояс IANA
            <Input
              aria-label="Часовой пояс определения"
              value={timezone}
              required
              disabled={busy}
              onChange={(event) => setTimezone(event.target.value)}
            />
          </label>
          <label>
            Временное поле
            <select
              aria-label="Временное поле определения"
              value={timeColumn}
              disabled={busy}
              onChange={(event) => setTimeColumn(event.target.value)}
            >
              <option value="">Не используется</option>
              {columns
                .filter((item) => /date|timestamp/i.test(item.data_type))
                .map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name}
                  </option>
                ))}
            </select>
          </label>
        </div>
        <fieldset>
          <legend className="text-sm mb-2">Разрешить изменение значений</legend>
          {query.filters
            .filter((item) => !item.operator.startsWith('is_'))
            .map((item) => (
              <label
                key={item.id}
                className="flex items-center gap-2 my-2 text-sm"
              >
                <input
                  type="checkbox"
                  checked={mutable.includes(item.parameter_name)}
                  disabled={busy}
                  onChange={(event) =>
                    setMutable(
                      event.target.checked
                        ? [...mutable, item.parameter_name]
                        : mutable.filter(
                            (name) => name !== item.parameter_name,
                          ),
                    )
                  }
                />
                {item.description} · {item.column_id.split('.').at(-1)} ·{' '}
                {item.parameter_value}
              </label>
            ))}
        </fieldset>
        <p className="text-xs text-text-secondary">
          COUNT не учитывает NULL и даёт 0 на пустом наборе. SUM/AVG/MIN/MAX
          пропускают NULL и дают NULL на пустом наборе. Поддерживаемые JOIN
          должны иметь уникальный правый ключ в каталоге.
        </p>
        <Button
          disabled={
            busy ||
            !metrics.length ||
            metrics.some((item) => !units[item.id]?.trim()) ||
            !title.trim() ||
            !description.trim()
          }
          type="submit"
        >
          {selected ? 'Утвердить новую версию' : 'Утвердить определение'}
        </Button>
        {selected && (
          <Button
            type="button"
            className="ml-3"
            variant="ghost"
            disabled={busy || selected.freshness === 'RETIRED'}
            onClick={() =>
              void send('retire_definition', {
                definition_id: selected.definition_id,
                expected_version: selected.version,
              }).catch(() => undefined)
            }
          >
            Отозвать определение
          </Button>
        )}
      </form>
    </details>
  );
}
