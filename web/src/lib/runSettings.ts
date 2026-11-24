/**
 * The run settings of the Sweep / Optimization / Descent panels (owner 2026-11-24, one place for the settings).
 *
 * The panels have no settings of their own: a run uses the values the Mesh and Simulation tabs hold at the moment of Run
 * (steps per period, gap rows, wire cell, mesh sizes, sectors, demag).  They are sent as `{preset: 'tab', overrides}`; the SERVER
 * (POST /api/optimization/run-settings/resolve, src/motor_ai_sim/run_settings.py) labels them by match against the Optimization and
 * Simulation presets (else `custom`), applies the bounds and answers with the digest and the resolution token; the panel shows that
 * answer in one read-only line and the run request carries the same values plus `expect_sig` / `expect_token`.  Pure functions
 * here, the hook is in useRunSettings.ts.
 */
export type RunMode = 'sweep' | 'descent' | 'auto';

/** What the request carries: the visible values, resolved by the server under the preset name 'tab'. */
export interface RunRequest { preset: 'tab'; overrides: Record<string, number | boolean> }

export interface RunResolved {
  preset: string; label: string; custom: boolean; preset_label: string; matched?: string | null;
  differs: Record<string, { preset: unknown; current: unknown }>;
  settings: Record<string, any>; sources: Record<string, string>;
  clamps: Array<{ key: string; requested: unknown; used: unknown; reason: string }>;
  sig: number; sig_scope?: number; token?: string; version: number; mode: RunMode; overrides: Record<string, unknown>;
  geometry?: { num_slots?: number | null; num_poles?: number | null };
  /** Not part of the run's identity: what the finalists of this run are solved with (cogging quality), when it differs. */
  finalist_note?: { min_steps: number; run_steps: number; reason: string } | null;
}

/** The values of the tabs this module needs (by the request names; a missing one is absent). */
export interface TabState {
  steps?: number; gap_layers?: number; mesh_size_mm?: number; min_size_mm?: number; n_sectors?: number; demag?: boolean;
  component_mesh?: Record<string, number>;
}

/** The wire cell of the Mesh tab: a missing key is the canonical 1h (the Mesh tab's own reading). */
export const wireCellOf = (m: Record<string, number> | undefined): number =>
  (m && typeof m.coil_rel === 'number' && m.coil_rel > 0 ? m.coil_rel : 1);

const NEEDED: Array<[keyof TabState, string]> = [
  ['steps', 'steps per period'], ['gap_layers', 'gap rows'], ['mesh_size_mm', 'mesh size'], ['min_size_mm', 'min mesh size'],
  ['n_sectors', 'sectors'],
];

/**
 * The request of one run from the tab values, or `null` with the names of the values the tabs do not hold (the line then reads
 * "unknown" and Run is disabled: nothing is filled in silently).  The demag switch of a Descent run is the machine's: not sent.
 */
export function tabRequest(cur: TabState, mode: RunMode): { request: RunRequest | null; missing: string[] } {
  const missing = NEEDED.filter(([k]) => typeof cur[k] !== 'number' || !Number.isFinite(cur[k] as number)).map(([, n]) => n);
  if (missing.length) return { request: null, missing };
  const o: Record<string, number | boolean> = {
    steps_per_period: cur.steps as number, gap_layers: cur.gap_layers as number, mesh_size_mm: cur.mesh_size_mm as number,
    min_size_mm: cur.min_size_mm as number, n_sectors: cur.n_sectors as number, coil_rel: wireCellOf(cur.component_mesh),
  };
  if (mode !== 'descent' && typeof cur.demag === 'boolean') o.demag = cur.demag;
  return { request: { preset: 'tab', overrides: o }, missing: [] };
}

/** Two requests are the same values (key order does not matter). */
export const sameRequest = (a: RunRequest | null, b: RunRequest | null): boolean => {
  if (!a || !b) return a === b;
  const ka = Object.keys(a.overrides).sort(), kb = Object.keys(b.overrides).sort();
  return ka.length === kb.length && ka.every((k, i) => k === kb[i] && a.overrides[k] === b.overrides[k]);
};

/** The request fragment of a run: the values and the digest / token the panel displayed. */
export const runSettingsBody = (r: RunRequest, shown: RunResolved) =>
  ({ preset: r.preset, overrides: r.overrides, expect_sig: shown.sig, ...(shown.token ? { expect_token: shown.token } : {}) });

/** The body of the Descent "Draw baseline" request: the held resolve answer's steps, gap rows, mesh, sectors, rotor eddy and
 *  element order under `run_settings` (the server resolves the same values). */
export const baselineBody = (
  base: { operating_point: unknown; current_bump_pct: number },
  r: RunRequest, shown: RunResolved,
) => ({
  ...base,
  steps_per_period: Number(shown.settings.steps_per_period),
  gap_layers: Number(shown.settings.gap_layers),
  n_sectors: Number(shown.settings.n_sectors),
  run_settings: runSettingsBody(r, shown),
});

/** Run is allowed only while the held answer belongs to the current machine, duty, mode and values. */
export const answerKey = (ctx: string, mode: RunMode, r: RunRequest | null): string => `${ctx}|${mode}|${JSON.stringify(r ? r.overrides : null)}`;

/** The one sentence of the line, from the SERVER answer: "Optimization preset v2 · 48 steps · 1 gap row · wire cell 2h" or
 *  "custom: 108 steps, 1 gap row, wire cell 1h".  Words come from the caller (translated). */
export function lineText(
  r: RunResolved,
  w: { preset: (label: string, version: number) => string; custom: string; steps: (n: number) => string;
       gap: (n: number) => string; wire: (n: number) => string },
): string {
  const st = r.settings;
  const parts = [w.steps(Number(st.steps_per_period)), w.gap(Number(st.gap_layers)), w.wire(Number(st.coil_rel ?? 1))];
  return r.custom ? `${w.custom}: ${parts.join(', ')}` : `${w.preset(r.preset_label, r.version)} · ${parts.join(' · ')}`;
}

/** The line is a warning unless the run is the Optimization preset (colour AND the word). */
export const notOptimization = (r: RunResolved): boolean => r.label !== 'optimization';
