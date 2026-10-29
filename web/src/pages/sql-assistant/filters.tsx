import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import type {
  AssistantAggregateFilter,
  AssistantFilterTree,
  AssistantQuery,
} from '@/interfaces/database/sql-assistant';

type Column = { id: string; label: string; data_type: string };
type Predicate = AssistantQuery['filters'][number] | AssistantAggregateFilter;
const Operators = {
  eq: 'Равно',
  ne: 'Не равно',
  gt: 'Больше',
  gte: 'Не меньше',
  lt: 'Меньше',
  lte: 'Не больше',
  like: 'По шаблону',
  ilike: 'По шаблону без регистра',
  in: 'В списке',
  not_in: 'Не в списке',
  is_null: 'Не заполнено',
  is_not_null: 'Заполнено',
};

function parameterType(
  type: string,
  operator: string,
): Predicate['parameter_type'] {
  const scalar = /int|serial/i.test(type)
    ? 'integer'
    : /numeric|decimal|float|double|real|money/i.test(type)
      ? 'decimal'
      : /timestamp/i.test(type)
        ? 'datetime'
        : /^date$/i.test(type)
          ? 'date'
          : /bool/i.test(type)
            ? 'boolean'
            : 'text';
  return operator === 'in' || operator === 'not_in'
    ? scalar === 'integer' || scalar === 'decimal'
      ? 'integer_list'
      : 'text_list'
    : scalar;
}

function remove(
  tree: AssistantFilterTree,
  ids: Set<string>,
): AssistantFilterTree | null {
  if ('filter_id' in tree) return ids.has(tree.filter_id) ? null : tree;
  const children = tree.children
    .map((child) => remove(child, ids))
    .filter((child): child is AssistantFilterTree => child !== null);
  return children.length ? { ...tree, children } : null;
}

export function AssistantFilters({
  query,
  columns,
  scope,
  disabled,
  structureLocked = false,
  mutableParameters,
  onChange,
}: {
  query: AssistantQuery;
  columns: Column[];
  scope: 'WHERE' | 'HAVING';
  disabled: boolean;
  structureLocked?: boolean;
  mutableParameters?: string[];
  onChange: (query: AssistantQuery) => void;
}) {
  const aggregate = scope === 'HAVING';
  const locked = disabled || structureLocked;
  const filters: Predicate[] = aggregate
    ? query.aggregate_filters || []
    : query.filters;
  const tree = (aggregate
    ? query.aggregate_filter_tree
    : query.row_filter_tree) || {
    operator: 'AND' as const,
    children: filters.map((filter) => ({ filter_id: filter.id })),
  };
  const root =
    'filter_id' in tree ? { operator: 'AND' as const, children: [tree] } : tree;
  const targets = aggregate
    ? query.select.flatMap((item) =>
        ['column', 'date_bucket'].includes(item.kind)
          ? []
          : [
              {
                id: item.id,
                label: item.alias,
                data_type: ['count', 'count_distinct'].includes(item.kind)
                  ? 'BIGINT'
                  : columns.find((column) => column.id === item.column_id)
                      ?.data_type || '',
              },
            ],
      )
    : columns;
  const commit = (
    nextFilters: Predicate[],
    nextTree: AssistantFilterTree | null,
  ) => {
    onChange(
      aggregate
        ? {
            ...query,
            aggregate_filters: nextFilters as AssistantAggregateFilter[],
            aggregate_filter_tree: nextTree,
          }
        : {
            ...query,
            filters: nextFilters as AssistantQuery['filters'],
            row_filter_tree: nextTree,
          },
    );
  };
  const updateTree = (
    path: number[],
    replace: (node: AssistantFilterTree) => AssistantFilterTree,
  ) => {
    const visit = (
      node: AssistantFilterTree,
      at: number,
    ): AssistantFilterTree => {
      if (at === path.length) return replace(node);
      if ('filter_id' in node) return node;
      return {
        ...node,
        children: node.children.map((child, index) =>
          index === path[at] ? visit(child, at + 1) : child,
        ),
      };
    };
    return visit(root, 0);
  };
  const add = (path: number[], asGroup: boolean) => {
    if (!targets.length) return;
    const id = crypto.randomUUID().replaceAll('-', '').slice(0, 12);
    const predicate = {
      id: `filter_${id}`,
      operator: 'eq' as const,
      parameter_name: `p_${id}`,
      parameter_type: parameterType(targets[0].data_type, 'eq'),
      parameter_value: '',
      description: 'Условие пользователя',
      confirmed: true,
      decision: 'user' as const,
      ...(aggregate
        ? { select_item_id: targets[0].id }
        : { column_id: targets[0].id }),
    };
    const leaf = { filter_id: predicate.id };
    commit(
      [...filters, predicate],
      updateTree(path, (node) =>
        'children' in node
          ? {
              ...node,
              children: [
                ...node.children,
                asGroup ? { operator: 'OR', children: [leaf] } : leaf,
              ],
            }
          : node,
      ),
    );
  };
  const drop = (ids: Set<string>) =>
    commit(
      filters.filter((filter) => !ids.has(filter.id)),
      remove(root, ids),
    );
  const replaceFilter = (next: Predicate) =>
    commit(
      filters.map((filter) => (filter.id === next.id ? next : filter)),
      root,
    );
  const render = (node: AssistantFilterTree, path: number[]) => {
    const label = `${scope} ${[1, ...path.map((value) => value + 1)].join('.')}`;
    if ('filter_id' in node) {
      const filter = filters.find((item) => item.id === node.filter_id);
      if (!filter)
        return (
          <p role="alert" key={label}>
            Условие недоступно. Вернитесь к исходному черновику.
          </p>
        );
      const targetId =
        'column_id' in filter ? filter.column_id : filter.select_item_id;
      const target = targets.find((item) => item.id === targetId);
      const valueLocked =
        disabled ||
        (structureLocked &&
          !mutableParameters?.includes(filter.parameter_name));
      const type = parameterType(target?.data_type || '', 'eq');
      const operators = Object.entries(Operators).filter(([operator]) => {
        if (['like', 'ilike'].includes(operator)) return type === 'text';
        if (['in', 'not_in'].includes(operator))
          return type === 'text' || type === 'integer';
        return true;
      });
      return (
        <div key={filter.id} className="sa-result-tools">
          <label>
            {aggregate ? 'Агрегат' : 'Поле'}
            <select
              aria-label={`Поле ${label}`}
              value={targetId}
              disabled={locked}
              onChange={(event) => {
                const selected = targets.find(
                  (item) => item.id === event.target.value,
                )!;
                replaceFilter({
                  ...filter,
                  ...(aggregate
                    ? { select_item_id: selected.id }
                    : { column_id: selected.id }),
                  parameter_type: parameterType(
                    selected.data_type,
                    filter.operator,
                  ),
                });
              }}
            >
              {!target && (
                <option value={targetId}>
                  Поле исключено — выберите другое
                </option>
              )}
              {targets.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.label}
                </option>
              ))}
            </select>
          </label>
          <label>
            Условие
            <select
              aria-label={`Условие ${label}`}
              value={filter.operator}
              disabled={locked}
              onChange={(event) =>
                replaceFilter({
                  ...filter,
                  operator: event.target.value as Predicate['operator'],
                  parameter_type: parameterType(
                    target?.data_type || '',
                    event.target.value,
                  ),
                })
              }
            >
              {!operators.some(([value]) => value === filter.operator) && (
                <option value={filter.operator} disabled>
                  Выберите условие для этого типа
                </option>
              )}
              {operators.map(([value, name]) => (
                <option key={value} value={value}>
                  {name}
                </option>
              ))}
            </select>
          </label>
          <label>
            Значение · {filter.parameter_type}
            {type === 'boolean' ? (
              <select
                aria-label={`Значение ${label}`}
                value={filter.parameter_value}
                disabled={valueLocked || filter.operator.startsWith('is_')}
                onChange={(event) =>
                  replaceFilter({
                    ...filter,
                    parameter_value: event.target.value,
                  })
                }
              >
                <option value="">Выберите значение</option>
                <option value="true">Да</option>
                <option value="false">Нет</option>
              </select>
            ) : (
              <Input
                aria-label={`Значение ${label}`}
                value={filter.parameter_value}
                disabled={valueLocked || filter.operator.startsWith('is_')}
                onChange={(event) =>
                  replaceFilter({
                    ...filter,
                    parameter_value: event.target.value,
                  })
                }
                placeholder={
                  type === 'datetime'
                    ? '2026-10-01T00:00:00+03:00'
                    : type === 'date'
                      ? '2026-10-01'
                      : undefined
                }
              />
            )}
          </label>
          <Button
            variant="ghost"
            disabled={locked}
            onClick={() => drop(new Set([filter.id]))}
            aria-label={`Убрать условие ${label}`}
          >
            Убрать
          </Button>
        </div>
      );
    }
    const ids = (branch: AssistantFilterTree): string[] =>
      'filter_id' in branch ? [branch.filter_id] : branch.children.flatMap(ids);
    return (
      <fieldset className="sa-filter-group" key={label}>
        <legend>Группа {label}</legend>
        <div className="sa-result-tools">
          <label>
            Совпадение условий
            <select
              aria-label={`Связь группы ${label}`}
              value={node.operator}
              disabled={locked}
              onChange={(event) =>
                commit(
                  filters,
                  updateTree(path, (item) =>
                    'children' in item
                      ? {
                          ...item,
                          operator: event.target.value as 'AND' | 'OR',
                        }
                      : item,
                  ),
                )
              }
            >
              <option value="AND">Все условия (И)</option>
              <option value="OR">Хотя бы одно (ИЛИ)</option>
            </select>
          </label>
          {path.length > 0 && (
            <Button
              variant="ghost"
              disabled={locked}
              onClick={() => drop(new Set(ids(node)))}
            >
              Удалить группу и условия
            </Button>
          )}
        </div>
        {node.children.map((child, index) => render(child, [...path, index]))}
        <div className="sa-result-tools">
          <Button
            variant="outline"
            disabled={
              locked ||
              !targets.length ||
              query.filters.length + (query.aggregate_filters?.length || 0) >=
                50
            }
            onClick={() => add(path, false)}
          >
            Добавить условие {label}
          </Button>
          <Button
            variant="ghost"
            disabled={
              locked ||
              !targets.length ||
              query.filters.length + (query.aggregate_filters?.length || 0) >=
                50 ||
              path.length >= 4
            }
            onClick={() => add(path, true)}
          >
            Добавить группу {label}
          </Button>
        </div>
      </fieldset>
    );
  };
  return (
    <section>
      <h3 className="mb-3 font-medium">
        {aggregate
          ? 'Фильтры агрегатов · после группировки (HAVING)'
          : 'Фильтры исходных данных · до агрегации (WHERE)'}
      </h3>
      {aggregate && !targets.length && (
        <p className="text-sm text-text-secondary">
          Добавьте агрегат в состав расчёта, чтобы задать условие над его
          значением.
        </p>
      )}
      {render(root, [])}
      <p className="mt-2 text-xs text-text-secondary">
        Условия меняют расчёт по источнику. Для даты и времени укажите
        ISO-значение; часовой пояс задаётся смещением, например +03:00.
      </p>
    </section>
  );
}
