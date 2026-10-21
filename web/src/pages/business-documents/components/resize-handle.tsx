import { cn } from '@/lib/utils';
import { useRef } from 'react';

export function ResizeHandle({
  direction,
  value,
  min,
  max,
  onChange,
  onReset,
  label,
  testId,
}: {
  direction: 'width' | 'height';
  value: number;
  min: number;
  max: number;
  onChange: (value: number) => void;
  onReset: () => void;
  label: string;
  testId: string;
}) {
  const drag = useRef<{ pointer: number; start: number; value: number } | null>(
    null,
  );
  const vertical = direction === 'width';
  const resize = (next: number) => onChange(Math.min(max, Math.max(min, next)));
  return (
    <div
      role="separator"
      aria-label={label}
      aria-orientation={vertical ? 'vertical' : 'horizontal'}
      aria-valuemin={min}
      aria-valuemax={max}
      aria-valuenow={value}
      tabIndex={0}
      data-testid={testId}
      className={cn(
        'group relative z-10 flex touch-none select-none items-center justify-center outline-none hover:bg-accent-primary/10 focus-visible:bg-accent-primary/10',
        vertical
          ? 'row-span-2 w-2 cursor-col-resize border-x border-border-button max-lg:hidden'
          : 'h-3 w-full shrink-0 cursor-row-resize',
      )}
      onPointerDown={(event) => {
        if (event.button !== 0) return;
        event.preventDefault();
        drag.current = {
          pointer: event.pointerId,
          start: vertical ? event.clientX : event.clientY,
          value,
        };
        event.currentTarget.setPointerCapture(event.pointerId);
      }}
      onPointerMove={(event) => {
        const current = drag.current;
        if (!current || current.pointer !== event.pointerId) return;
        resize(
          current.value +
            current.start -
            (vertical ? event.clientX : event.clientY),
        );
      }}
      onPointerUp={(event) => {
        if (drag.current?.pointer !== event.pointerId) return;
        drag.current = null;
        event.currentTarget.releasePointerCapture(event.pointerId);
      }}
      onPointerCancel={() => {
        drag.current = null;
      }}
      onLostPointerCapture={() => {
        drag.current = null;
      }}
      onDoubleClick={onReset}
      onKeyDown={(event) => {
        if (event.key === 'Home') {
          event.preventDefault();
          onReset();
          return;
        }
        const increase = vertical ? 'ArrowLeft' : 'ArrowUp';
        const decrease = vertical ? 'ArrowRight' : 'ArrowDown';
        if (event.key !== increase && event.key !== decrease) return;
        event.preventDefault();
        resize(value + (event.key === increase ? 24 : -24));
      }}
    >
      <span
        className={cn(
          'rounded-full bg-text-disabled/50 group-hover:bg-accent-primary group-focus-visible:bg-accent-primary',
          vertical ? 'h-8 w-[3px]' : 'h-[3px] w-7',
        )}
      />
    </div>
  );
}
