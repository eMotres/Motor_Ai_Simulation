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

/** The grade's maximum working temperature when the field's number is ABOVE it
 *  (owner 2026-10-30: «не больше той, для которой они предназначены»), else
 *  null — blank, not a number, or no limit known.  The server refuses the same
 *  value (routes.simulation._typed_magnet_temp_guard). */
export function magnetTempOverLimit(raw: unknown, maxC: number | null | undefined): number | null {
  const v = parseMagnetTempC(raw);
  const m = Number(maxC);
  if (v == null || maxC == null || !Number.isFinite(m)) return null;
  return v > m + 1e-9 ? m : null;
}

/** What the field SHOWS (owner 2026-10-30: «меню так и не работает» — an empty
 *  field read as broken).  Like the coil temperature, it always carries the
 *  number the run will use: the typed text when there is one, else the
 *  assigned card's own temperature (20 °C for a library card, 150 for a former
 *  `N52UH_150C`), marked `isCard` so it is drawn grey.  Showing the card's
 *  number writes nothing: blank still sends no magnet_temp_c (the card as
 *  quoted, the same solve and the same cache key as before). */
export function magnetTempShown(raw: unknown, cardC: number | null | undefined,
                                ): { text: string; isCard: boolean } {
  const s = String(raw ?? '');
  if (s.trim() !== '') return { text: s, isCard: false };
  const c = Number(cardC);
  return cardC != null && Number.isFinite(c)
    ? { text: String(Math.round(c * 10) / 10), isCard: true }
    : { text: '', isCard: false };
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

/** The magnet temperature a duty's saved snapshot states (`sim.magnetTempC`,
 *  the panel's text), or '' — "the card as quoted" — when it states none.  A
 *  duty load writes this into the field BEFORE the duty's own local overlay, so
 *  a duty never inherits the previous duty's temperature (lib/dutyLocalApply). */
export function dutyMagnetTempC(duty: { mesh?: unknown } | null | undefined): string {
  const m = (duty?.mesh ?? null) as Record<string, unknown> | null;
  const v = m && typeof m === 'object' ? m['sim.magnetTempC'] : undefined;
  return v == null ? '' : String(v).trim();
}
