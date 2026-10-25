// The Motors & results table (owner 2026-10-26): ONE group per geometry variant (die / configuration) with its duties as compact
// sub-rows, a colour accent per geometry, history snapshots grouped under their variant, and a two-state view - the latest
// current variant plus the best one, or everything.  Pure helpers, no React.
import type { ProjectMotor } from './types';

/** A fixed palette, one entry per geometry (die): mid-tone accents that read on the light and the dark theme. */
export const DIE_PALETTE = ['#3b82f6', '#10b981', '#f59e0b', '#ec4899', '#8b5cf6', '#06b6d4', '#ef4444', '#84cc16', '#f97316', '#64748b'] as const;

const hash = (text: string) => { let h = 5381; for (let i = 0; i < text.length; i++) h = ((h << 5) + h + text.charCodeAt(i)) >>> 0; return h; };
/** The accent of a geometry: a pure function of the die name, so the same die has the same colour in the Motors table and in the
 *  points-only Configure step selector (the ordinary alias and the canonical die of one geometry share the colour via `canonical_die`). */
export function dieColor(die: string | null | undefined): string {
  const key = String(die ?? '').trim().toLowerCase();
  return key ? DIE_PALETTE[hash(key) % DIE_PALETTE.length] : 'var(--text-4)';
}
export const motorColor = (motor: Pick<ProjectMotor, 'die' | 'canonical_die'>): string => dieColor(motor.canonical_die ?? motor.die);

/** One variant: die + configuration (case-insensitive, canonical die first). */
export const variantKey = (motor: Pick<ProjectMotor, 'die' | 'canonical_die' | 'config'>): string => `${String(motor.canonical_die ?? motor.die).toLowerCase()}/${String(motor.config).toLowerCase()}`;

export interface MotorGroup { key: string; die: string; config: string; live: ProjectMotor | null; history: ProjectMotor[] }
/** Variants in order of first appearance; each history snapshot sits under the variant it was taken of. */
export function groupMotorVersions(versions: ProjectMotor[]): MotorGroup[] {
  const groups = new Map<string, MotorGroup>();
  const take = (motor: ProjectMotor) => {
    const key = variantKey(motor);
    let g = groups.get(key);
    if (!g) { g = { key, die: motor.canonical_die ?? motor.die, config: motor.config, live: null, history: [] }; groups.set(key, g); }
    return g;
  };
  for (const motor of versions) if (motor.kind !== 'history') take(motor).live = motor;
  for (const motor of versions) if (motor.kind === 'history') take(motor).history.push(motor);
  return Array.from(groups.values());
}
/** The versions in table order: variant by variant, its history right after it. */
export function orderByGroup(versions: ProjectMotor[]): ProjectMotor[] {
  return groupMotorVersions(versions).flatMap((g) => [...(g.live ? [g.live] : []), ...g.history]);
}

/** Header rows a table row needs above it: the variant header when it starts a variant, the history header when it starts a snapshot. */
export function headerFlags(motors: ProjectMotor[], index: number): { group: boolean; history: boolean } {
  const motor = motors[index], previous = index > 0 ? motors[index - 1] : undefined;
  if (!motor) return { group: false, history: false };
  return {
    group: !previous || variantKey(previous) !== variantKey(motor),
    history: motor.kind === 'history' && (!previous || previous !== motor),
  };
}

export type VersionView = 'latest' | 'all';
const isCurrent = (m: ProjectMotor) => m.kind !== 'history' && m.status !== 'trial';
const finite = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);
/** A variant's average efficiency (%) over its duties that have a stored result; null when it has none. */
export function averageEfficiency(motor: ProjectMotor): number | null {
  const values = (motor.results ?? []).map((r) => {
    const c = (r as { comparison?: Record<string, unknown> }).comparison ?? {};
    return finite(c.efficiency_pct) ?? finite(r.efficiency_pct);
  }).filter((v): v is number => v !== null);
  return values.length ? values.reduce((a, b) => a + b, 0) / values.length : null;
}
/** The latest current variant (the last linked one that is not a trial) and the best one - the highest average efficiency - when
 *  that is another variant.  Everything else (trials, older variants, history) is hidden. */
export function latestAndBest(motors: ProjectMotor[]): ProjectMotor[] {
  const current = motors.filter(isCurrent);
  if (!current.length) return [];
  const latest = current[current.length - 1];
  let best: ProjectMotor | null = null, bestValue = -Infinity;
  for (const m of current) { const v = averageEfficiency(m); if (v !== null && v > bestValue) { best = m; bestValue = v; } }
  const picked = best && best !== latest ? [best, latest] : [latest];
  return motors.filter((m) => picked.includes(m));                       // keep the table's own order
}
/** What the table shows for a view: `latest` = latest + best of the current variants, `all` = every variant (trials too) with its history. */
export function versionsForView(all: ProjectMotor[], view: VersionView): ProjectMotor[] {
  return view === 'all' ? orderByGroup(all) : latestAndBest(all);
}
