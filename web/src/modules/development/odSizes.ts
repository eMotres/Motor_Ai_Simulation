/** The outer-diameter choice of the specification (4_1, owner 2026-11-25): "existing" (the customer ticks the existing sizes he accepts; the
 *  calculation starts from an existing size and is ready much sooner) or "custom" (his own diameter, much longer to compute); the
 *  maximum diameter stays a hard limit in both.  Pure functions: `node --test` imports this file. */
export interface ExistingSize { od_mm: number; slots: number; poles: number; stack_mm: number[] }
export interface AcceptedSize { od_mm: number; slots: number; poles: number }
export type OdMode = 'existing' | 'custom';

/** A stored answer without the choice reads as "custom" (every version written before the choice existed). */
export function odModeOf(value: unknown): OdMode {
  const mode = value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>).od_mode : undefined;
  return mode === 'existing' ? 'existing' : 'custom';
}

export const sizeKey = (s: AcceptedSize): string => `${s.od_mm}|${s.slots}|${s.poles}`;
const num = (v: unknown): number | undefined => (typeof v === 'number' && Number.isFinite(v) ? v : undefined);

/** The preferred diameter as a [low, high] pair: a single number is both bounds, a stored range its two bounds. */
export function preferredBounds(v: unknown): { min?: number; max?: number } {
  if (typeof v === 'number') return Number.isFinite(v) ? { min: v, max: v } : {};
  if (v && typeof v === 'object') { const r = v as Record<string, unknown>; return { min: num(r.minimum), max: num(r.maximum) }; }
  return {};
}

/** The accepted sizes as stored: only well-formed {od_mm, slots, poles} entries. */
export function acceptedOf(v: unknown): AcceptedSize[] {
  if (!Array.isArray(v)) return [];
  return v.flatMap((e) => { const r = e as Record<string, unknown>; const od = num(r?.od_mm), sl = num(r?.slots), po = num(r?.poles); return od !== undefined && sl !== undefined && po !== undefined ? [{ od_mm: od, slots: sl, poles: po }] : []; });
}

/** Default selection: every listed size within the customer's maximum diameter (the server already leaves the larger ones out). */
export function defaultAccepted(sizes: ExistingSize[], maxOd?: number): AcceptedSize[] {
  return sizes.filter((s) => maxOd === undefined || s.od_mm <= maxOd).map(({ od_mm, slots, poles }) => ({ od_mm, slots, poles }));
}

export function toggleSize(accepted: AcceptedSize[], size: AcceptedSize, on: boolean): AcceptedSize[] {
  const rest = accepted.filter((a) => sizeKey(a) !== sizeKey(size));
  return on ? [...rest, { od_mm: size.od_mm, slots: size.slots, poles: size.poles }] : rest;
}

/** The text of a size row: "Ø 80 mm, 12 slots / 14 poles, stack 10, 15 mm". */
export function sizeText(s: ExistingSize, tr: (key: string, args?: Record<string, unknown>) => string): string {
  return tr('odSizes.row', { od: s.od_mm, slots: s.slots, poles: s.poles }) + (s.stack_mm.length ? tr('odSizes.stack', { stacks: s.stack_mm.join(', ') }) : '');
}
