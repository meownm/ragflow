import type { Section } from '@/services/linked-document-service';

export const documentInputClass =
  'w-full rounded-md border border-border-button bg-bg-base px-3 py-2 text-sm';
export const sectionTitle = (section: Section) =>
  section.text.split('\n')[0].replace(/^#+\s*/, '') || 'Введение';

export function Choice({
  label,
  value,
  options,
  onChange,
  disabled = false,
}: {
  label: string;
  value: string;
  options: Record<string, string>;
  onChange(value: string): void;
  disabled?: boolean;
}) {
  return (
    <label className="block space-y-1 text-sm">
      <span>{label}</span>
      <select
        className={documentInputClass}
        aria-label={label}
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
      >
        {Object.entries(options).map(([key, title]) => (
          <option key={key} value={key}>
            {title}
          </option>
        ))}
      </select>
    </label>
  );
}
