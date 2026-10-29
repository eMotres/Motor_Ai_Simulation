/**
 * ASK BEFORE LOADING ANOTHER MACHINE (owner 2026-10-29).
 *
 * «если что-то делалось с мотором и пользователь загружает другой мотор, нужно
 * его спрашивать: нужно сохранять или предыдущие вычисления».
 *
 * `guardLoad(target)` is the ONE gate in front of `dutyApply.applyDutyEverywhere`
 * (Motors ▶, the header strip's mode switch and reload, a project's open
 * buttons, the Designer).  It collects what a load would lose
 * (`lib/unsavedWork`), and when there is anything at all raises ONE dialog
 * (`components/common/LoadGuardDialog`) and waits for the answer:
 *
 *   Save and load      - the header strip's own save flows run first (the last
 *                        run + point into the duty; geometry edits as a NEW
 *                        configuration), a running job is stopped, then load;
 *   Load without saving - a running job is stopped first, then load;
 *   Cancel             - nothing happens.
 *
 * Nothing in between is silent.  With nothing at stake the promise resolves at
 * once and no dialog is shown.  The server has the same rule one level down:
 * `POST /api/family/activate` answers 409 `job_running` unless the request says
 * `cancel_running` (routes/family.py), so a caller that skips this gate still
 * cannot lose a job without being told.
 */
import { useMotorStore } from '../stores/motorStore';
import {
  buildDrift, busyJobs, markRunsSaved, noteFreshRun, stakeIsEmpty,
  type BuildDrift, type SavedBuild, type Stake,
} from './unsavedWork';

const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8001';

export interface LoadTarget { die: string; config: string; duty: string | null }

/** The ticket `applyDutyEverywhere` needs to proceed: the person has answered (or nothing was at stake). */
export interface LoadPermit { readonly asked: true }
const PERMIT: LoadPermit = { asked: true };

/** What the header strip can save on the dialog's behalf (it owns the save flows and their state). */
export interface Savers {
  /** the loaded duty can take the last run + point (a writer, a duty is loaded) */
  canSaveResults: boolean;
  /** geometry edits can be kept as a new configuration of the die */
  canSaveEdits: boolean;
  /** null = saved, text = why not */
  saveResults(): Promise<string | null>;
  saveEditsAsNewConfig(name: string): Promise<string | null>;
  /** the name offered for the new configuration */
  defaultConfigName(): string;
}

let savers: Savers | null = null;
export function registerSavers(s: Savers | null): void { savers = s; }

/** What the dialog shows. */
export interface LoadAsk {
  target: LoadTarget;
  /** the machine being left, for the title / tooltip */
  here: { die: string; config: string; duty: string | null } | null;
  stake: Stake;
  canSave: boolean;
  /** run the chosen action; resolves null when the load may go on, else the reason it may not */
  choose(choice: 'save' | 'discard'): Promise<string | null>;
  cancel(): void;
}

type Listener = (ask: LoadAsk | null) => void;
const listeners = new Set<Listener>();
export function subscribeLoadAsk(fn: Listener): () => void {
  listeners.add(fn);
  return () => { listeners.delete(fn); };
}
const publish = (ask: LoadAsk | null) => { listeners.forEach((fn) => fn(ask)); };

let asking: Promise<LoadPermit | null> | null = null;

interface FamilyCtx {
  active?: boolean; die?: string; config?: string; duty?: string | null;
  can_write?: boolean; build?: SavedBuild | null;
}

async function getJson(path: string): Promise<unknown> {
  const r = await fetch(`${API}${path}`, { cache: 'no-store' });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

/** What a load of `target` would lose right now.  Read-only GETs; a lost backend reads as "nothing at stake"
 *  (the server's own 409 is the second net for the part that matters). */
export async function assess(live: Record<string, unknown> | null | undefined,
                             opts: { released?: boolean } = {}): Promise<{ stake: Stake; ctx: FamilyCtx | null }> {
  let ctx: FamilyCtx | null = null;
  let jobs: Stake['jobs'] = [];
  try { ctx = (await getJson('/api/family/context')) as FamilyCtx; } catch { ctx = null; }
  try { jobs = busyJobs(((await getJson('/api/jobs?limit=40')) as { jobs?: unknown })?.jobs); } catch { jobs = []; }
  const loaded = !!(ctx?.active && ctx.die && ctx.config);
  // A RELEASED context ("discard and reload" was confirmed by the person) keeps only the job question.
  const edits: BuildDrift | null = loaded && !opts.released ? buildDrift(ctx?.build, live) : null;
  const unsavedRun = loaded && !opts.released && !!ctx?.duty && hasFreshRun();
  return { stake: { jobs, unsavedRun, edits }, ctx };
}

const hasFreshRun = (): boolean => {
  try { return localStorage.getItem('sim.unsavedRunAt') != null; } catch { return false; }
};

/** Stop what is running in this workspace.  The owner path has the one endpoint that knows every campaign. */
async function stopJobs(ctx: FamilyCtx | null, jobs: Stake['jobs']): Promise<string | null> {
  if (!jobs.length) return null;
  try {
    if (ctx?.can_write) {
      const r = await fetch(`${API}/api/family/cancel_running`, { method: 'POST' });
      if (!r.ok) return `HTTP ${r.status}`;
      return null;
    }
    await Promise.all(jobs.map((j) => fetch(`${API}/api/jobs/${encodeURIComponent(j.run_id)}/cancel`, { method: 'POST' })));
    return null;
  } catch (e) { return String((e as Error)?.message ?? e); }
}

/**
 * Resolve to a permit when the load may go on, or `null` when the person cancelled.
 * Reads the live geometry from the motor store.  Never throws.
 */
export function guardLoad(target: LoadTarget,
                          opts: { released?: boolean } = {}): Promise<LoadPermit | null> {
  const run = async (): Promise<LoadPermit | null> => {
    let assessed: Awaited<ReturnType<typeof assess>>;
    try {
      const live = useMotorStore.getState().geometry as unknown as Record<string, unknown>;
      assessed = await assess(live, opts);
    } catch { return PERMIT; }
    const { stake, ctx } = assessed;
    if (stakeIsEmpty(stake)) return PERMIT;
    return new Promise<LoadPermit | null>((resolve) => {
      const needsSave = stake.unsavedRun || !!stake.edits?.any;
      const canSave = needsSave && !!savers && (stake.edits?.any ? savers.canSaveEdits : savers.canSaveResults);
      const ask: LoadAsk = {
        target,
        here: ctx?.active && ctx.die && ctx.config
          ? { die: ctx.die, config: ctx.config, duty: ctx.duty ?? null } : null,
        stake,
        canSave,
        cancel: () => { publish(null); resolve(null); },
        choose: async (choice) => {
          // Running work is stopped FIRST: the save steps below re-activate the context,
          // and the server refuses that while a job runs.
          const stopped = await stopJobs(ctx, stake.jobs);
          if (stopped) return stopped;
          if (choice === 'save' && savers) {
            const why = stake.edits?.any
              ? await savers.saveEditsAsNewConfig(savers.defaultConfigName())
              : stake.unsavedRun ? await savers.saveResults() : null;
            if (why) return why;
          }
          if (choice === 'discard') markRunsSaved();
          publish(null);
          resolve(PERMIT);
          return null;
        },
      };
      publish(ask);
    });
  };
  // One dialog at a time: a second load asked while the first is open waits for it.
  const next = (asking ?? Promise.resolve(null)).then(run, run);
  asking = next.finally(() => { if (asking === next) asking = null; }) as Promise<LoadPermit | null>;
  return next;
}

/** The apply is done: whatever is on screen is now what the loaded machine stores. */
export function loadFinished(): void { markRunsSaved(); }

/** Called once from the dialog host: a finished solve in this browser is unsaved until it is saved or the machine is loaded. */
export function watchRuns(): () => void {
  const onDone = () => noteFreshRun();
  window.addEventListener('sim-transient-done', onDone);
  return () => window.removeEventListener('sim-transient-done', onDone);
}
