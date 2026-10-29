export function cellText(value: unknown, format?: string) {
  if (value === null) return 'NULL';
  if (typeof value === 'boolean') return value ? 'Да' : 'Нет';
  if (
    format &&
    format !== 'raw' &&
    typeof value !== 'object' &&
    value !== '' &&
    Number.isFinite(Number(value))
  ) {
    // Formatting must not silently change a high-precision decimal string.
    if (
      typeof value === 'string' &&
      value.replace(/[^0-9]/g, '').replace(/^0+/, '').length > 15
    )
      return value;
    return new Intl.NumberFormat(
      'ru-RU',
      format === 'currency'
        ? { style: 'currency', currency: 'RUB', maximumFractionDigits: 2 }
        : format === 'percent'
          ? { style: 'percent', maximumFractionDigits: 2 }
          : { maximumFractionDigits: 6 },
    ).format(Number(value));
  }
  return typeof value === 'object'
    ? JSON.stringify(value)
    : String(value ?? '');
}
