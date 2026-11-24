/**
 * The run settings of the Sweep / Optimization / Descent panels (owner 2026-11-24).
 *
 * One small record per machine and duty, `{preset, overrides}`, kept apart from the ordinary-run settings of the Simulation and
 * Mesh tabs: nothing here reads or writes those.  The SERVER resolves the record (POST /api/optimization/run-settings/resolve)
 * into the settings the run receives (steps per period and gap rows are what the run uses; the rest is shown read-only); the panel shows exactly that answer and the run request carries the same
 * record plus the digest it showed (`expect_sig`).  Pure functions here, the hook is in useRunSettings.ts.
 */
export type RunPresetName = 'optimization' | 'simulation';
export type RunMode = 'sweep' | 'descent' | 'auto';
export interface RunRecord { preset: RunPresetName; overrides: Record<string, number | boolean> }
export const DEFAULT_RECORD: RunRecord = { preset: 'optimization', overrides: {} };

export interface RunResolved {
  preset: RunPresetName; label: string; custom: boolean; preset_label: string;
  differs: Record<string, { preset: unknown; current: unknown }>;
  settings: Record<string, any>; sources: Record<string, string>;
  clamps: Array<{ key: string; requested: unknown; used: unknown; reason: string }>;
  sig: number; token?: string; version: number; mode: RunMode; overrides: Record<string, unknown>;
  /** Not part of the run's identity: what the finalists of this run are solved with (cogging quality), when it differs. */
  finalist_note?: { min_steps: number; run_steps: number; reason: string } | null;
}

export const recordScope = (ctx: string): string => `runSettings.${ctx}`;

/**
 * The override keys the resolver accepts (the union over the three modes of `_OVERRIDABLE` in `src/motor_ai_sim/run_settings.py`;
 * the server answers 422 for any other key).  The two lists are kept equal by `tests/test_run_settings_gemini_findings.py`.
 */
export const OVERRIDE_KEYS_NUMBER = ['steps_per_period', 'gap_layers', 'mesh_size_mm', 'min_size_mm', 'n_sectors', 'coil_rel'] as const;
export const OVERRIDE_KEYS_BOOLEAN = ['rotor_eddy', 'demag'] as const;

/** Override keys a mode's resolver refuses (`_OVERRIDABLE` of the server: the descent evals read demag from the stored Simulation block). */
export const MODE_REFUSES: Record<string, string[]> = { descent: ['demag'] };

/** Validator of a stored record: a known preset and the overrides the resolver accepts (finite number / boolean by key); an
 *  obsolete or unknown key (e.g. `hi_fidelity` of an older record) is dropped silently, so the panel never answers 422 for it. */
export function validRecord(raw: unknown): RunRecord | null {
  if (!raw || typeof raw !== 'object') return null;
  const r = raw as any;
  if (r.preset !== 'optimization' && r.preset !== 'simulation') return null;
  const o: Record<string, number | boolean> = {};
  for (const [k, v] of Object.entries(r.overrides ?? {})) {
    if ((OVERRIDE_KEYS_BOOLEAN as readonly string[]).includes(k)) { if (typeof v === 'boolean') o[k] = v; }
    else if ((OVERRIDE_KEYS_NUMBER as readonly string[]).includes(k)) { if (typeof v === 'number' && Number.isFinite(v)) o[k] = v; }
  }
  return { preset: r.preset, overrides: o };
}

/** The record of ONE mode's REQUEST: the stored record keeps every override (it is shared by the modes), the request of a mode omits
 *  the ones its resolver refuses (a Descent run carries no demag; the Sweep override stays stored for the Sweep panel). */
export const forMode = (r: RunRecord, mode: RunMode): RunRecord => {
  const o = { ...r.overrides };
  for (const k of MODE_REFUSES[mode] ?? []) delete o[k];
  return { preset: r.preset, overrides: o };
};

/** A preset button: sets the preset and clears every override. */
export const choosePreset = (name: RunPresetName): RunRecord => ({ preset: name, overrides: {} });
/** Editing one setting adds an override (the run then reads custom). */
export const withOverride = (r: RunRecord, key: string, value: number | boolean): RunRecord =>
  ({ preset: r.preset, overrides: { ...r.overrides, [key]: value } });
export const withoutOverride = (r: RunRecord, key: string): RunRecord => {
  const o = { ...r.overrides }; delete o[key];
  return { preset: r.preset, overrides: o };
};

/** The request fragment of a run: the record and the digest the panel displayed. */
export const runSettingsBody = (r: RunRecord, shown: RunResolved) =>
  ({ preset: r.preset, overrides: r.overrides, expect_sig: shown.sig, ...(shown.token ? { expect_token: shown.token } : {}) });

/** The body of the Descent "Draw baseline" request: the held resolve answer's steps, gap rows, mesh, sectors, rotor eddy and
 *  element order under `run_settings` (the server resolves the same record); nothing is read from the stored Mesh tab. */
export const baselineBody = (
  base: { operating_point: unknown; current_bump_pct: number },
  r: RunRecord, shown: RunResolved,
) => ({
  ...base,
  steps_per_period: Number(shown.settings.steps_per_period),
  gap_layers: Number(shown.settings.gap_layers),
  n_sectors: Number(shown.settings.n_sectors),
  run_settings: runSettingsBody(r, shown),
});

/** Run is allowed only while the held answer belongs to the current machine, duty, mode and record. */
export const answerKey = (ctx: string, mode: RunMode, r: RunRecord): string => `${ctx}|${mode}|${JSON.stringify([r.preset, r.overrides])}`;
