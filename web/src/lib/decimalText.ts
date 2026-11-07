/** Typed text -> a number, for every number box of the specification form (owner 2026-11-06: a European types `0,35`).  The comma counts
 *  as the decimal point; nothing is guessed beyond that.  Pure, no imports: `node --test` imports this file. */

/** A number, 'empty' (cleared) or 'partial' (a half-typed "-", "." or "1e", or text that is no number: nothing is written yet). */
export function parseDecimal(text: string): number | 'empty' | 'partial' {
  const s = text.trim().replace(',', '.');
  if (s === '') return 'empty';
  if (!/^[+-]?(\d+\.?\d*|\.\d+)(e[+-]?\d+)?$/i.test(s)) return 'partial';
  const n = Number(s);
  return Number.isFinite(n) ? n : 'partial';
}

/** The number a text means, NaN while it means none (empty or partial). */
export function decimalNumber(text: string): number {
  const parsed = parseDecimal(text);
  return typeof parsed === 'number' ? parsed : NaN;
}
