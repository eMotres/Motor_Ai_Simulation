// Locale-aware number / date formatting (docs/I18N.md).
//
// Rules:
//   * digits, grouping and the decimal mark follow the interface locale (Intl);
//   * UNIT SYMBOLS and PHYSICAL-QUANTITY SYMBOLS are never translated or
//     transliterated: "N·m", "kW", "rpm", "°C", "A_rms", "T_j", "Br", "HcJ"
//     read the same in every language — they are the engineering vocabulary
//     a Chinese supplier's datasheet uses too;
//   * SI spacing: one (narrow no-break) space between the number and the unit,
//     none before "%" and "°" (and "°C" keeps its space per SI, "25 °C").

export function formatNumber(value: number | null | undefined, locale: string,
  opts: { digits?: number; minDigits?: number; grouping?: boolean } = {}): string {
  if (value == null || !Number.isFinite(value)) return '—';
  const { digits = 2, minDigits = 0, grouping = true } = opts;
  return new Intl.NumberFormat(locale, {
    maximumFractionDigits: digits,
    minimumFractionDigits: Math.min(minDigits, digits),
    useGrouping: grouping,
  }).format(value);
}

/** "12.5 kW" — the unit symbol is passed through untouched. */
export function formatQuantity(value: number | null | undefined, unit: string, locale: string,
  opts: { digits?: number; minDigits?: number; grouping?: boolean } = {}): string {
  const n = formatNumber(value, locale, opts);
  if (n === '—' || !unit) return n;
  const tight = unit === '%' || unit === '°' || unit === '′' || unit === '″';
  return tight ? `${n}${unit}` : `${n} ${unit}`;
}

export function formatPercent(fraction: number | null | undefined, locale: string, digits = 1): string {
  if (fraction == null || !Number.isFinite(fraction)) return '—';
  return new Intl.NumberFormat(locale, { style: 'percent', maximumFractionDigits: digits }).format(fraction);
}

export function formatDateTime(d: Date | string | number | null | undefined, locale: string,
  opts: Intl.DateTimeFormatOptions = { dateStyle: 'medium', timeStyle: 'short' }): string {
  if (d == null) return '—';
  const date = d instanceof Date ? d : new Date(d);
  if (Number.isNaN(date.getTime())) return '—';
  return new Intl.DateTimeFormat(locale, opts).format(date);
}
