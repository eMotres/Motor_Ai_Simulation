// One duty, several EXCITATIONS — remembering the runs instead of re-solving.
//
// A duty is one operating point (I, rpm, γ).  Running it on the sinusoidal
// current source and running it on the PWM inverter answer two different
// questions about that same point, and the PWM one costs twelve minutes.  Until
// 2026-09-02 the duty held exactly one snapshot, so saving after a PWM run
// overwrote the sine result the catalog shows AND restored `sim.drive =
// pwm_voltage` the next time the duty was loaded — after which every Run was a
// PWM solve (user: "кто опять включил расчёт на 12 минут?").
//
// Now the yaml keeps one entry per drive under `runs:` with its waveforms in a
// gzip sidecar (routes/family.py), ▶ fetches them ALL in one call, and this
// module is where they live on the client:
//
//   • the PAYLOADS stay in memory for the session — three 48-step transients
//     are megabytes, which is exactly what localStorage is not for;
//   • a small DESCRIPTION of each run (drive, when, staleness, carrier, steps)
//     is persisted, so after F5 the selector still knows what this duty has and
//     can fetch the one the user clicks;
//   • WHICH run is on screen is persisted per duty, defaulting to the primary.
//
// Applying a run writes its settings and its payload exactly where a finished
// solve would have written them, so the charts, the summary card and the
// charging card show it with nothing recomputed.

const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8001';

export type Drive = 'current' | 'voltage' | 'pwm_voltage' | 'bldc_current'
                  | 'custom_current';

/** What the catalog tree and the persisted list say about a stored run. */
export interface StoredRunMeta {
  drive: string;
  primary?: boolean;
  recorded_at?: string | null;
  stale?: boolean;
  ripple_pct?: number | null;
  f_switch_hz?: number | null;
  steps?: number | null;
  assignment_sig?: string | null;
  has_payload?: boolean;
}

/** A stored run, whole — what /duty_runs returns. */
export interface StoredRun extends StoredRunMeta {
  settings?: Record<string, unknown> | null;
  summary?: Record<string, unknown> | null;
  result?: Record<string, unknown> | null;
  payload?: Record<string, unknown> | null;
}

/** The short label a chip shows.  Deliberately the words an engineer uses
 *  about the SOURCE, not the backend's identifier. */
export const DRIVE_LABEL: Record<string, string> = {
  current: 'Sine',
  voltage: 'V',
  pwm_voltage: 'PWM',
  bldc_current: 'BLDC',
  custom_current: 'I(θ)',
};

export const driveLabel = (d: string): string => DRIVE_LABEL[d] ?? d;

const META_KEY = 'family.dutyRuns';     // { key, at, runs: StoredRunMeta[] }
const SEL_KEY  = 'family.dutyRunPick';  // { [dutyKey]: drive }
const MAX_PICKS = 48;

// Session-lived payload cache: dutyKey -> drive -> run.  Never persisted.
const mem = new Map<string, Map<string, StoredRun>>();

/** Fired whenever the list, the cache or the pick moves — the selector and
 *  anything else that shows "which run is on screen" listen to it. */
export const RUNS_EVENT = 'duty-runs-changed';
function announce(): void {
  try { window.dispatchEvent(new CustomEvent(RUNS_EVENT)); } catch { /* SSR */ }
}

// ── the list (persisted description) ─────────────────────────────────────────

function readMeta(): { key?: string; runs?: StoredRunMeta[] } {
  try { return JSON.parse(localStorage.getItem(META_KEY) || '{}'); }
  catch { return {}; }
}

/** ▶ loaded a duty: remember what it has and cache the payloads it came with. */
export function setDutyRuns(key: string | null, runs: StoredRun[]): void {
  if (!key) return;
  const box = new Map<string, StoredRun>();
  for (const r of runs) if (r?.drive) box.set(r.drive, r);
  mem.set(key, box);
  const meta: StoredRunMeta[] = runs.map(r => ({
    drive: r.drive, primary: r.primary, recorded_at: r.recorded_at,
    stale: r.stale, assignment_sig: r.assignment_sig,
    ripple_pct: r.ripple_pct
      ?? (r.result?.ripple_pct as number | undefined)
      ?? (r.summary?.T_ripple_pct as number | undefined) ?? null,
    f_switch_hz: (r.settings?.['sim.fSwitch'] as number | undefined) ?? null,
    steps: (r.settings?.['sim.stepsPP'] as number | undefined) ?? null,
    has_payload: !!r.payload,
  }));
  try { localStorage.setItem(META_KEY, JSON.stringify({ key, at: Date.now(), runs: meta })); }
  catch { /* quota — the in-memory copy still drives this session */ }
  announce();
}

/** What this duty has, or [] when it is not the duty ▶ last loaded.  Keyed, so
 *  a stale list from another duty can never be shown against this one. */
export function dutyRuns(key: string | null): StoredRunMeta[] {
  if (!key) return [];
  const m = readMeta();
  if (m.key !== key || !Array.isArray(m.runs)) return [];
  return m.runs;
}

/** No duty is loaded (a my-motors copy, a preset): the selector must go away
 *  rather than offer runs belonging to a machine that is no longer on screen. */
export function clearDutyRuns(): void {
  mem.clear();
  try { localStorage.removeItem(META_KEY); } catch { /* nothing to clear */ }
  announce();
}

// ── which run is on screen ───────────────────────────────────────────────────

export function pickedRun(key: string | null): string | null {
  if (!key) return null;
  try {
    const m = JSON.parse(localStorage.getItem(SEL_KEY) || '{}');
    const v = m?.[key];
    return typeof v === 'string' ? v : null;
  } catch { return null; }
}

export function setPickedRun(key: string | null, drive: string): void {
  if (!key) return;
  try {
    const m = JSON.parse(localStorage.getItem(SEL_KEY) || '{}') as Record<string, string>;
    m[key] = drive;
    const names = Object.keys(m);
    // Bounded like every other per-duty memory — the oldest picks go first.
    if (names.length > MAX_PICKS) for (const n of names.slice(0, names.length - MAX_PICKS)) delete m[n];
    localStorage.setItem(SEL_KEY, JSON.stringify(m));
  } catch { /* quota — the pick is a convenience */ }
  announce();
}

// ── fetching ─────────────────────────────────────────────────────────────────

/** Every stored run of a duty, payloads and all — the one call ▶ makes. */
export async function fetchDutyRuns(die: string, cfg: string, duty: string):
    Promise<{ runs: StoredRun[]; primary_drive: string }> {
  const r = await fetch(`${API}/api/family/duty_runs/${encodeURIComponent(die)}/`
    + `${encodeURIComponent(cfg)}/${encodeURIComponent(duty)}`, { cache: 'no-store' });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail ?? `HTTP ${r.status}`);
  const j = await r.json();
  return { runs: Array.isArray(j?.runs) ? j.runs : [],
           primary_drive: String(j?.primary_drive || 'current') };
}

/** ONE stored run.  Served from the session cache when ▶ already brought it,
 *  fetched otherwise (which is what happens after a page reload). */
export async function getDutyRun(key: string | null, die: string, cfg: string,
                                 duty: string, drive: string): Promise<StoredRun | null> {
  const cached = key ? mem.get(key)?.get(drive) : undefined;
  if (cached?.payload) return cached;
  const r = await fetch(`${API}/api/family/duty_run/${encodeURIComponent(die)}/`
    + `${encodeURIComponent(cfg)}/${encodeURIComponent(duty)}/${encodeURIComponent(drive)}`,
    { cache: 'no-store' });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail ?? `HTTP ${r.status}`);
  const run = await r.json() as StoredRun;
  if (key) {
    const box = mem.get(key) ?? new Map<string, StoredRun>();
    box.set(drive, run);
    mem.set(key, box);
  }
  return run;
}

// ── showing one ──────────────────────────────────────────────────────────────

// Never written back into the panel.  `runNonce` is the RUN TRIGGER — restoring
// it makes the panel start a solve by itself (2026-08-25) — and the result
// caches are written below from the run's own payload, not from its settings.
// `sim.vBus` belongs to the MACHINE's battery, not to a saved run.
const NEVER = new Set(['sim.runNonce', 'sim.lastTransient', 'sim.lastSummary',
                       'sim.viewSummary', 'sim.vBus']);

/**
 * Put a stored run on screen — settings, waveforms and summary — exactly where
 * a finished solve would have put them, and tell the panels to re-read.
 * Nothing is solved and nothing is written to the server.
 *
 * `settings: false` restores the RESULT only.  That is what ▶ needs: the duty
 * load has already written the panel state through its own precedence chain
 * (the duty's snapshot, then the die's remembered mesh, then the user's local
 * overlay — FamilyCatalog.applyDuty), and overwriting that with the run's own
 * copy would quietly undo the die mesh memory and the user's unsaved edits.
 * The run SELECTOR passes true, because switching sine → PWM is precisely a
 * request to put that run's drive and carrier back on the panel.
 */
export function applyStoredRun(run: StoredRun, opts?: { settings?: boolean }): void {
  if (!run) return;
  // 1) the panel settings the run was solved with (drive, steps, carrier, …)
  if (opts?.settings !== false) {
    try {
      for (const [k, v] of Object.entries(run.settings ?? {})) {
        const key = k.includes('.') ? k : `mesh.${k}`;
        if (NEVER.has(key)) continue;
        localStorage.setItem(key, JSON.stringify(v));
      }
    } catch { /* quota — the charts below still show the run */ }
  }
  // 2) the waveforms, in the place TransientCharts restores from on mount…
  try {
    if (run.payload) localStorage.setItem('sim.lastTransient', JSON.stringify(run.payload));
  } catch { /* quota */ }
  const summary = (run.summary ?? (run.payload?.summary as Record<string, unknown> | undefined)) || null;
  try {
    if (summary) localStorage.setItem('sim.lastSummary', JSON.stringify(summary));
  } catch { /* quota */ }
  // 3) …and the live nudges.  `sim-transient-restored` is the same-tab twin of
  //    the cross-tab `storage` adoption TransientCharts already does: the
  //    charts must switch WITHOUT a reload and without a solve.
  try {
    window.dispatchEvent(new CustomEvent('sim-settings-restored'));
    if (run.payload) {
      window.dispatchEvent(new CustomEvent('sim-transient-restored',
        { detail: { payload: run.payload } }));
    }
    if (summary) {
      window.dispatchEvent(new CustomEvent('sim-apply-summary', { detail: { summary } }));
    }
  } catch { /* SSR/no-window */ }
}
