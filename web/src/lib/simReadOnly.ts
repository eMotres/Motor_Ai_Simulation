/**
 * The pure half of the READ-ONLY Electromagnetic Simulation view that project members get (owner 2026-11-04: «эти точки
 * показываем всем, кто работает над проектом … без возможности запуска для всех, кроме меня пока»).
 *
 * The live Simulation tab restores a point into the workbench (global stores, localStorage, `/api/family/activate`), which
 * writes server context and is the owner's.  A member's view must not touch any of it, so it reads two plain GET answers of
 * the project (`/api/projects/{id}/motors/sim` and `.../sim/run`, routes/development) and draws the same components from
 * them.  Nothing here fetches or writes.
 */
import type { StoredRunMeta } from './dutyRuns.ts';
import { drivesOf, type TreeCfg, type TreeDuty } from './simPoints.ts';

/** `GET /api/projects/{id}/motors/sim` — the motor's computed points, shaped like a configuration row of the family tree. */
export interface SimStoredPoints extends TreeCfg {
  die: string;
  config: string;
}

/** `GET /api/projects/{id}/motors/sim/run` — one stored run: the summary card and the waveforms (signatures and meshes stripped). */
export interface SimStoredRun {
  duty: string;
  drive: string;
  recorded_at?: string | null;
  stale?: boolean;
  summary?: Record<string, unknown> | null;
  payload?: Record<string, unknown> | null;
}

/** The chips of one point's stored sources (Sine, PWM, …), the primary first — what StoredRunSelector shows in the live tab. */
export function runMetasOf(d: TreeDuty | null | undefined): StoredRunMeta[] {
  if (!d) return [];
  const order = drivesOf(d);
  const rows = (d.runs ?? []) as StoredRunMeta[];
  return order
    .map((drive) => rows.find((r) => r.drive === drive))
    .filter((r): r is StoredRunMeta => !!r);
}

/** The point to open first: the asked one when it exists, else the configuration's default, else the first. */
export function initialPoint(cfg: TreeCfg | null | undefined, asked?: string | null): string | null {
  const names = (cfg?.duties ?? []).map((d) => d.name);
  if (asked && names.includes(asked)) return asked;
  if (cfg?.default_duty && names.includes(cfg.default_duty)) return cfg.default_duty;
  return names[0] ?? null;
}

/** The excitation to show for a point: the one on screen before when the new point has it stored, else the primary. */
export function driveFor(d: TreeDuty | null | undefined, previous?: string | null): string | null {
  const drives = d ? drivesOf(d) : [];
  if (!drives.length) return null;
  if (previous && drives.includes(previous)) return previous;
  return d?.primary_drive && drives.includes(d.primary_drive) ? d.primary_drive : drives[0];
}

/** May the view offer a "View simulation" door for this project-motor row?  Staff who run the live tab keep their own door. */
export function canOpenReadOnlySimulation(opts: { staff: boolean; hasPoint: boolean }): boolean {
  return !opts.staff && opts.hasPoint;
}
