/**
 * WHAT THE SWEEP TAB MAY SHOW AS ITS RESULT.
 *
 * Owner, 2026-10-29: after a sweep run the tab showed a stale result whose
 * points carried no point ids.  Three paths fed the chart without asking whose
 * result it was:
 *
 *   - the live poll stored every partial progress batch as `{ points }` - no
 *     run id, no machine - under the same localStorage key as the finished
 *     result, so the next page load restored the half-run as "the last sweep";
 *   - the mount effect drew that browser copy BEFORE the server answered, and a
 *     later mismatch only cleared it when the server happened to hold a result;
 *   - a result restored from `.last_scan.json` was shown exactly like a fresh one.
 *
 * The rule, in one place: a result is drawn only if (1) every point has its id
 * (`geom_id` + `op_index` - what Apply and the table key on), (2) it names its
 * run, and then either (3a) it IS the run this panel is waiting for, or (3b) it
 * is shown as the PREVIOUS run, labelled with the machine it belongs to.  A
 * previous run of a machine other than the loaded one is shown only when the
 * user asked for it (reopening a saved sweep).
 *
 * Pure (no DOM, no fetch) so `node --test` loads it directly.
 */

export interface MachineStamp {
  fingerprint?: string;
  die?: string;
  config?: string;
  duty?: string;
  unattributed?: boolean;
  declared?: boolean;
  swept?: string[];
}

export type RestoreWhy =
  | 'empty'          // nothing to draw
  | 'no-ids'         // some point has no geom_id / op_index
  | 'no-run-id'      // the result does not say which run it is
  | 'other-run'      // not the run this panel is waiting for
  | 'foreign-machine'// another machine, and nobody asked for it
  | 'unstamped';     // the result names no machine at all

export type RestoreVerdict =
  | { show: false; why: RestoreWhy }
  | {
      show: true;
      /** 'current' = the run this panel started / adopted; 'previous' = history. */
      kind: 'current' | 'previous';
      /** "die / config / duty" of the machine the result belongs to. */
      machine: string;
      /** The result's machine is not the one loaded now. */
      foreign: boolean;
    };

export interface RestoreContext {
  /** The run id this panel is waiting for ('' = none: restoring on mount). */
  wantRunId: string;
  /** `machine_now` of `GET /scan/progress`. */
  machineNow?: MachineStamp | null;
  /** The user asked for this one by name (reopened a saved sweep). */
  userChose?: boolean;
}

const isInt = (v: unknown): boolean => typeof v === 'number' && Number.isInteger(v);

/** Does every point carry the id Apply and the table key on? */
export function pointsHaveIds(points: unknown): boolean {
  if (!Array.isArray(points) || points.length === 0) return false;
  return points.every((p: any) => p && typeof p === 'object' && isInt(p.geom_id) && isInt(p.op_index));
}

export function machineLabel(m?: MachineStamp | null): string {
  const parts = [m?.die, m?.config, m?.duty].filter(Boolean);
  return parts.length ? parts.join(' / ') : 'machine unknown';
}

export function sameMachine(res: { machine?: MachineStamp } | null | undefined,
                            now?: MachineStamp | null): boolean {
  const a = res?.machine?.fingerprint;
  const b = now?.fingerprint;
  return !!a && !!b && String(a) === String(b);
}

/** Decide whether `res` may be drawn, and how it must be labelled. */
export function judgeResult(res: any, ctx: RestoreContext): RestoreVerdict {
  if (!res || !Array.isArray(res.points) || res.points.length === 0) return { show: false, why: 'empty' };
  if (!pointsHaveIds(res.points)) return { show: false, why: 'no-ids' };
  const runId = String(res.run_id ?? '');
  if (!runId) return { show: false, why: 'no-run-id' };
  const machine = machineLabel(res.machine);
  const same = sameMachine(res, ctx.machineNow);
  if (ctx.wantRunId) {
    if (runId !== ctx.wantRunId) return { show: false, why: 'other-run' };
    // The run this panel is waiting for: it must also be for the machine on
    // screen (the backend stops a run whose machine changed, but a result that
    // still disagrees is not an answer to the question asked).
    // (An in-flight batch is exempt: the server stops a run whose machine
    //  changed, and the batch's own stamp is the start-of-run one.)
    if (!same && !res.partial) return { show: false, why: 'foreign-machine' };
    return { show: true, kind: 'current', machine, foreign: false };
  }
  // History: nothing was asked for.
  if (!res.machine || res.machine.unattributed || !res.machine.fingerprint) {
    // A chart nobody can attribute is worth less than an empty one - unless the
    // user opened this very save, which says "machine unknown" on its banner.
    if (!ctx.userChose) return { show: false, why: 'unstamped' };
    return { show: true, kind: 'previous', machine, foreign: true };
  }
  if (!same && !ctx.userChose) return { show: false, why: 'foreign-machine' };
  return { show: true, kind: 'previous', machine, foreign: !same };
}

/** The banner above a chart that is history, not the answer to a Run. */
export function previousRunLabel(v: Extract<RestoreVerdict, { show: true }>): string {
  return v.foreign
    ? `Previous run of ${v.machine} — not the machine loaded now`
    : `Previous run of ${v.machine}`;
}

/** Why a stored result is not drawn - one sentence for the error line. */
export function notShownText(why: RestoreWhy, res: any): string {
  const m = machineLabel(res?.machine);
  switch (why) {
    case 'no-ids': return 'the stored sweep has points without ids, so it cannot be attributed to a run — not shown';
    case 'no-run-id': return 'the stored sweep does not name its run — not shown';
    case 'unstamped': return 'the stored sweep does not name its machine — not shown';
    case 'foreign-machine': return `the stored sweep was computed on ${m} — not this machine, so it is not shown`;
    case 'other-run': return 'the result on the server belongs to another run — not shown';
    case 'empty': return 'the sweep stopped before any point finished — earlier sweeps are under Saved sweeps';
    default: return 'nothing to show';
  }
}

/** The in-flight points of a RUNNING sweep, wrapped so they name their run and
 *  machine (the progress payload carries both).  Never persisted: a partial is
 *  not a result. */
export function partialFromProgress(st: any): any {
  return {
    points: Array.isArray(st?.points) ? st.points : [],
    run_id: String(st?.run_id ?? ''),
    machine: st?.machine ?? undefined,
    partial: true,
  };
}

/** Only a finished, attributable result may be written to the browser. */
export function isPersistable(res: any): boolean {
  return !!res && !res.partial && !!String(res.run_id ?? '') && pointsHaveIds(res.points)
    && !!res.machine?.fingerprint;
}

export const STORE_KEY = 'sweepStudy.lastResult';
const STORE_VERSION = 2;

export function packForStore(res: any): string {
  return JSON.stringify({ v: STORE_VERSION, result: res });
}

/** The browser's copy, or null for anything not written by this version
 *  (the old format stored partial `{ points }` batches). */
export function unpackFromStore(raw: string | null): any | null {
  if (!raw) return null;
  try {
    const o = JSON.parse(raw);
    return o && o.v === STORE_VERSION && o.result ? o.result : null;
  } catch { return null; }
}
