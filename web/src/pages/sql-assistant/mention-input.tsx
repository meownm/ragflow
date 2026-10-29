import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import type { AssistantReference } from '@/interfaces/database/sql-assistant';
import {
  forwardRef,
  useId,
  useState,
  type ComponentProps,
  type KeyboardEvent,
} from 'react';

export const referenceKey = (item: AssistantReference) =>
  item.kind === 'TABLE'
    ? { kind: item.kind, id: item.id }
    : {
        kind: item.kind,
        id: item.id,
        version: item.version,
        content_hash: item.content_hash,
      };

export const MentionInput = forwardRef<
  HTMLTextAreaElement,
  Omit<ComponentProps<typeof Textarea>, 'value' | 'onChange'> & {
    value: string;
    onChange: (text: string) => void;
    references: AssistantReference[];
    onReferences: (references: AssistantReference[]) => void;
    options: AssistantReference[];
    loading?: boolean;
    error?: boolean;
    onSearch?: (query: string, open: boolean) => void;
  }
>(
  (
    {
      value,
      onChange,
      references,
      onReferences,
      options,
      loading,
      error,
      onSearch,
      ...props
    },
    ref,
  ) => {
    const [open, setOpen] = useState(false);
    const [search, setSearch] = useState('');
    const [active, setActive] = useState(0);
    const [token, setToken] = useState<{ start: number; end: number } | null>(
      null,
    );
    const listId = useId();
    const filtered = options
      .filter(
        (item) =>
          item.title.toLocaleLowerCase().includes(search.toLocaleLowerCase()) &&
          !references.some(
            (selected) =>
              selected.kind === item.kind && selected.id === item.id,
          ),
      )
      .slice(0, 20);
    const update = (query: string, show: boolean) => {
      setOpen(show);
      setSearch(query);
      setActive(0);
      onSearch?.(query, show);
    };
    const select = (item: AssistantReference) => {
      if (references.length >= 10) return;
      onReferences([...references, item]);
      onChange(
        token
          ? value.slice(0, token.start) +
              `@${item.title} ` +
              value.slice(token.end)
          : value.trimEnd() + ` @${item.title} `,
      );
      setToken(null);
      update('', false);
    };
    const onKey = (event: KeyboardEvent<HTMLElement>) => {
      if (!open) return;
      if (event.key === 'Escape') {
        event.preventDefault();
        update('', false);
      }
      if (['ArrowDown', 'ArrowUp'].includes(event.key)) {
        event.preventDefault();
        setActive(
          (index) =>
            (index +
              (event.key === 'ArrowDown' ? 1 : -1) +
              Math.max(1, filtered.length)) %
            Math.max(1, filtered.length),
        );
      }
      if (event.key === 'Enter') {
        event.preventDefault();
        if (filtered[active] && !loading && !error) select(filtered[active]);
      }
    };
    return (
      <>
        <Textarea
          {...props}
          ref={ref}
          role="combobox"
          aria-autocomplete="list"
          aria-haspopup="listbox"
          value={value}
          aria-controls={open ? listId : undefined}
          aria-expanded={open}
          aria-activedescendant={
            open && filtered[active] ? `${listId}-${active}` : undefined
          }
          onChange={(event) => {
            const text = event.target.value;
            onChange(text);
            const match = /(?:^|\s)@([^@\n]*)$/.exec(
              text.slice(0, event.target.selectionStart),
            );
            setToken(
              match
                ? {
                    start: event.target.selectionStart - match[1].length - 1,
                    end: event.target.selectionStart,
                  }
                : null,
            );
            update(match?.[1] || '', Boolean(match));
          }}
          onKeyDown={onKey}
        />
        <Button
          type="button"
          variant="ghost"
          size="sm"
          disabled={props.disabled || references.length >= 10}
          onClick={() => {
            setToken(null);
            update('', !open);
          }}
        >
          Упомянуть объект @
        </Button>
        {open && (
          <div className="my-2 rounded-lg border border-border-button p-2">
            <Input
              aria-label="Поиск упоминания"
              value={search}
              onChange={(event) => update(event.target.value, true)}
              onKeyDown={onKey}
            />
            {loading ? (
              <p role="status">Ищем доступные объекты…</p>
            ) : error ? (
              <p role="alert">
                Объекты недоступны. Повторите поиск после проверки подключения и
                прав.
              </p>
            ) : (
              <ul
                id={listId}
                role="listbox"
                aria-label="Доступные объекты"
                className="max-h-56 overflow-y-auto"
              >
                {filtered.map((item, index) => (
                  <li
                    key={`${item.kind}:${item.id}`}
                    id={`${listId}-${index}`}
                    role="option"
                    aria-selected={active === index}
                  >
                    <button
                      type="button"
                      className="w-full break-words p-2 text-left hover:bg-bg-card"
                      onClick={() => select(item)}
                    >
                      {item.kind === 'TABLE' ? 'Таблица' : 'Определение'} ·{' '}
                      {item.title}
                      {item.kind === 'DEFINITION' &&
                        ` · версия ${item.version}`}
                    </button>
                  </li>
                ))}
                {!filtered.length && (
                  <li>Совпадений в доступных объектах нет.</li>
                )}
              </ul>
            )}
          </div>
        )}
        {references.length > 0 && (
          <ul
            aria-label="Выбранные упоминания"
            className="my-2 flex flex-wrap gap-2 text-xs"
          >
            {references.map((item) => (
              <li
                key={`${item.kind}:${item.id}`}
                className="min-w-0 break-all rounded border border-border-button p-1"
              >
                {item.title}{' '}
                <button
                  type="button"
                  className="underline"
                  aria-label={`Убрать упоминание ${item.title}`}
                  onClick={() =>
                    onReferences(
                      references.filter((selected) => selected !== item),
                    )
                  }
                >
                  Убрать
                </button>
              </li>
            ))}
          </ul>
        )}
      </>
    );
  },
);
MentionInput.displayName = 'MentionInput';
