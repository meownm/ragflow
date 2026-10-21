import type { SqlRevisionWorkspace } from '@/pages/business-documents/types';

type Version = SqlRevisionWorkspace['query_revisions'][number];
type Parameter = NonNullable<Version['parameters']>[number];

const valueText = (item?: Parameter) =>
  item
    ? `${item.type} · ${item.value_text ?? JSON.stringify(item.value)}`
    : 'Отсутствует';

export function SqlQueryComparison({
  current,
  selected,
}: {
  current?: Version;
  selected: Version;
}) {
  const currentParameters = new Map(
    current?.parameters?.map((item) => [item.name, item]),
  );
  const selectedParameters = new Map(
    selected.parameters?.map((item) => [item.name, item]),
  );
  const names = [
    ...new Set([...currentParameters.keys(), ...selectedParameters.keys()]),
  ].sort();
  return (
    <section className="mt-4 space-y-3" aria-label="Сравнение версий SQL">
      <h4 className="text-sm font-semibold">Сравнение с текущей версией</h4>
      {current ? (
        current.sql === selected.sql ? (
          <p className="text-xs text-text-secondary">
            Текст SQL совпадает. Значения и типы параметров сравниваются
            отдельно.
          </p>
        ) : (
          <details className="text-xs">
            <summary className="cursor-pointer">Сравнить текст SQL</summary>
            <div className="mt-2 grid gap-3 md:grid-cols-2">
              <div className="border-s-2 border-state-error ps-3">
                <p>Текущая версия</p>
                <pre className="max-h-72 overflow-auto whitespace-pre-wrap">
                  {current.sql}
                </pre>
              </div>
              <div className="border-s-2 border-state-success ps-3">
                <p>Выбранная версия</p>
                <pre className="max-h-72 overflow-auto whitespace-pre-wrap">
                  {selected.sql}
                </pre>
              </div>
            </div>
          </details>
        )
      ) : (
        <p className="text-xs text-text-secondary">
          Текущий запрос ещё не подтверждён.
        </p>
      )}
      {selected.parameters == null || current?.parameters == null ? (
        <p className="text-xs text-text-secondary">
          Для сравнения отсутствуют сохранённые типизированные параметры одной
          из версий.
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table
            className="w-full text-left text-xs"
            aria-label="Различия параметров"
          >
            <thead>
              <tr>
                <th className="p-2">Параметр</th>
                <th className="p-2">Текущая версия</th>
                <th className="p-2">Выбранная версия</th>
                <th className="p-2">Изменение</th>
              </tr>
            </thead>
            <tbody>
              {names.map((name) => {
                const before = currentParameters.get(name),
                  after = selectedParameters.get(name);
                const change = !before
                  ? 'ADDED'
                  : !after
                    ? 'REMOVED'
                    : valueText(before) === valueText(after)
                      ? 'SAME'
                      : 'CHANGED';
                return (
                  <tr
                    key={name}
                    data-change={change}
                    className={
                      change === 'ADDED'
                        ? 'text-state-success'
                        : change === 'REMOVED'
                          ? 'text-state-error'
                          : change === 'CHANGED'
                            ? 'border-s-2 border-state-warning'
                            : ''
                    }
                  >
                    <th className="p-2 font-mono">{name}</th>
                    <td className="max-w-80 break-all p-2 font-mono">
                      {valueText(before)}
                    </td>
                    <td className="max-w-80 break-all p-2 font-mono">
                      {valueText(after)}
                    </td>
                    <td className="p-2">
                      {
                        {
                          ADDED: 'Добавлен',
                          REMOVED: 'Удалён',
                          SAME: 'Совпадает',
                          CHANGED: 'Изменено',
                        }[change]
                      }
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
