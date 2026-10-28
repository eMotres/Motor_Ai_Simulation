/**
 * Which motor picture an overview tile shows (owner 2026-10-28).  The picture is the catalog's own `MotorThumbnail`, fed by the
 * dies of `GET /api/family/tree` (slot / pole counts and the stored cross-section SVG): no new request, no new rendering.
 * Pure functions, no React, no window: `node --test` imports this file.
 */

export interface DieThumb { name: string; slots: number; poles: number; thumbSvg?: string }
export type DieIndex = Map<string, DieThumb>;

/** Dies are matched case-insensitively (the catalog spells a die one way, a production or project another). */
export const dieKey = (name: unknown): string => String(name ?? '').trim().toLowerCase();

const count = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) && v > 0 ? Math.round(v) : null);

/** The dies of the tree answer by name; anything without usable slot / pole counts is left out (no picture rather than a wrong one). */
export function indexDies(dies: readonly unknown[] | null | undefined): DieIndex {
  const out: DieIndex = new Map();
  for (const d of dies ?? []) {
    const x = d as { name?: unknown; slots?: unknown; poles?: unknown; thumb_svg?: unknown } | null;
    const slots = count(x?.slots), poles = count(x?.poles);
    if (!x || typeof x.name !== 'string' || !x.name || slots === null || poles === null) continue;
    out.set(dieKey(x.name), { name: x.name, slots, poles, thumbSvg: typeof x.thumb_svg === 'string' && x.thumb_svg ? x.thumb_svg : undefined });
  }
  return out;
}

export interface CardMotor extends DieThumb { label: string }

/** The motor of a production: its die, unless it is electronics (controllers have no motor picture) or the die is not in the catalog. */
export function productionCardMotor(p: { die: string; config: string; product_type?: string }, index: DieIndex | null): CardMotor | null {
  if (!index || p.product_type === 'electronics') return null;
  const die = index.get(dieKey(p.die));
  return die ? { ...die, label: `${p.die} / ${p.config}` } : null;
}

/** The motor of a project: the NEWEST linked variant (the list keeps the link order) whose die is in the catalog; none when nothing is linked. */
export function projectCardMotor(linked: ReadonlyArray<{ die: string; config: string }> | null | undefined, index: DieIndex | null): CardMotor | null {
  if (!index || !linked) return null;
  for (let i = linked.length - 1; i >= 0; i--) {
    const m = linked[i];
    const die = m?.die ? index.get(dieKey(m.die)) : undefined;
    if (die) return { ...die, label: `${m.die} / ${m.config}` };
  }
  return null;
}
