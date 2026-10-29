/**
 * The Simulation tab's MAGNET TEMPERATURE field, as text -> number
 * (owner 2026-10-29).  Blank = the magnet card exactly as quoted (its own
 * temperature).  Dependency-free so node imports the shipped file.
 */

/** The Magnet temperature field's text as a number [°C], or null for "the
 *  card as quoted" (blank) — and null for text that is not a number, which the
 *  field itself flags in red (`magnetTempError`) rather than sending.  A decimal
 *  comma is a decimal point (owner 2026-10-29: "90,5" used to vanish). */
export function parseMagnetTempC(raw: unknown): number | null {
  const s = String(raw ?? '').trim().replace(',', '.');
  if (s === '') return null;
  const v = Number(s);
  return Number.isFinite(v) ? v : null;
}

/** Why the field's text cannot be used, or null when it can (blank included). */
export function magnetTempError(raw: unknown): string | null {
  const s = String(raw ?? '').trim();
  if (s === '') return null;
  const v = parseMagnetTempC(s);
  if (v == null) return 'not a number';
  if (v < -60 || v > 350) return 'outside -60 … 350 °C';
  return null;
}
