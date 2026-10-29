/**
 * WHAT WOULD BE LOST if another machine were loaded now (owner 2026-10-29).
 *
 * A 16-point sweep was running and an unsaved 14-turn geometry was on screen
 * when the owner pressed ▶ on another motor.  Nothing asked; the sweep went on
 * on the wrong machine and the geometry was gone.  Every path that loads a
 * machine now asks `lib/loadGuard.guardLoad` first, and this file is the pure
 * half of its question: given what the page and the server report, is there
 * anything at stake?
 *
 *   • a JOB running in this workspace (transient, coupled, thermal, mechanical,
 *     sweep, descent, refine ...) - `GET /api/jobs`;
 *   • the last FINISHED run was not saved to the duty (`noteFreshRun` /
 *     `markRunsSaved` keep the one bit that is not derivable: "a solve has
 *     finished since this machine was loaded or last saved");
 *   • the live GEOMETRY differs from the loaded configuration (the strip's own
 *     amber "unsaved" test: stack, turns, wire height);
 *
 * The operating point the panel holds is NOT counted: ▶ files it under the duty
 * being left and brings it back with that duty (lib/dutyLocalApply.leaveForDuty).
 *
 * Pure functions + one localStorage key (every access in try/catch: private
 * windows and blocked storage must not break a load).
 */

const KEY_FRESH_RUN = 'sim.unsavedRunAt';

const lsGet = (k: string): string | null => {
  try { return localStorage.getItem(k); } catch { return null; }
};

/** A solve finished in this browser (`sim-transient-done`): its results exist only on screen until saved. */
export function noteFreshRun(now: number = Date.now()): void {
  try { localStorage.setItem(KEY_FRESH_RUN, String(now)); } catch { /* storage blocked: the dialog just does not ask about runs */ }
}

/** The results are in the duty (or the machine was just loaded and shows its stored ones): nothing unsaved. */
export function markRunsSaved(): void {
  try { localStorage.removeItem(KEY_FRESH_RUN); } catch { /* nothing to clear */ }
}

export function hasUnsavedRun(): boolean {
  const v = lsGet(KEY_FRESH_RUN);
  return v != null && Number.isFinite(Number(v));
}

/** The saved build the server reports for the loaded configuration (`/api/family/context` -> `build`). */
export interface SavedBuild {
  stack_mm?: number | null;
  wire_height_mm?: number | null;
  turns?: number | null;
}

export interface BuildDrift {
  any: boolean;
  /** `[saved, live]` per drifted quantity */
  stack?: [number, number];
  turns?: [number, number];
  wireHeight?: [number, number];
}

const differs = (a: number, b: number): boolean =>
  Number.isFinite(a) && Number.isFinite(b) && Math.abs(a - b) > 1e-6;

/** The strip's amber "live build differs from the saved configuration" test, as a function. */
export function buildDrift(saved: SavedBuild | null | undefined,
                           live: Record<string, unknown> | null | undefined): BuildDrift {
  const out: BuildDrift = { any: false };
  if (!saved || !live) return out;
  const pairs: [keyof BuildDrift, number, number][] = [
    ['stack', Number(saved.stack_mm), Number(live.motor_length)],
    ['turns', Number(saved.turns), Number(live.num_wires_per_slot)],
    ['wireHeight', Number(saved.wire_height_mm), Number(live.wire_height)],
  ];
  for (const [k, s, l] of pairs) {
    if (differs(s, l)) { (out as unknown as Record<string, unknown>)[k] = [s, l]; out.any = true; }
  }
  return out;
}

export interface RunningJob { run_id: string; kind: string; state: string }

/** Everything that would be lost.  An empty stake asks nothing. */
export interface Stake {
  jobs: RunningJob[];
  unsavedRun: boolean;
  edits: BuildDrift | null;
}

export const stakeIsEmpty = (s: Stake): boolean =>
  s.jobs.length === 0 && !s.unsavedRun && !(s.edits && s.edits.any);

/** The jobs of `GET /api/jobs` that are still going (or waiting). */
export function busyJobs(rows: unknown): RunningJob[] {
  if (!Array.isArray(rows)) return [];
  const out: RunningJob[] = [];
  for (const r of rows) {
    const o = r as Record<string, unknown> | null;
    const state = String(o?.state ?? '');
    if (o && (state === 'running' || state === 'queued')) {
      out.push({ run_id: String(o.run_id ?? ''), kind: String(o.kind ?? ''), state });
    }
  }
  return out;
}

/** Which locale word names a job kind (`loadGuard.job.<word>`). */
export function jobWord(kind: string): 'sweep' | 'optimization' | 'simulation' | 'job' {
  const k = kind.replace(/^optimizer\./, '');
  if (k === 'scan') return 'sweep';
  if (k === 'descent' || k === 'auto' || k === 'refine') return 'optimization';
  if (k === 'transient' || k === 'coupled' || k === 'thermal' || k === 'mechanical') return 'simulation';
  return 'job';
}

/** One thing at stake: a locale key under `loadGuard.item.` and its variables. */
export interface StakeItem { key: 'running' | 'run' | 'edits'; vars: Record<string, string> }

/** The tooltip's bullets, as keys + variables (the dialog translates them). */
export function stakeItems(s: Stake, duty: string | null | undefined,
                           jobWordText: (w: ReturnType<typeof jobWord>) => string): StakeItem[] {
  const items: StakeItem[] = [];
  if (s.jobs.length) {
    const words = Array.from(new Set(s.jobs.map((j) => jobWordText(jobWord(j.kind)))));
    items.push({ key: 'running', vars: { what: words.join(', ') } });
  }
  if (s.unsavedRun) items.push({ key: 'run', vars: { duty: duty ?? '' } });
  if (s.edits && s.edits.any) {
    const bits: string[] = [];
    if (s.edits.stack) bits.push(`${s.edits.stack[0]} → ${s.edits.stack[1]} mm`);
    if (s.edits.turns) bits.push(`${s.edits.turns[0]} → ${s.edits.turns[1]}`);
    if (s.edits.wireHeight) bits.push(`${s.edits.wireHeight[0]} → ${s.edits.wireHeight[1]} mm`);
    const names: string[] = [];
    if (s.edits.stack) names.push('stack');
    if (s.edits.turns) names.push('turns');
    if (s.edits.wireHeight) names.push('wire h');
    items.push({ key: 'edits', vars: { what: names.map((n, i) => `${n} ${bits[i]}`).join(', ') } });
  }
  return items;
}
