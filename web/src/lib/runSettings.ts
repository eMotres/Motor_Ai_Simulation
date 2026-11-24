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
  sig: number; version: number; mode: RunMode; overrides: Record<string, unknown>;
}

export const recordScope = (ctx: string): string => `runSettings.${ctx}`;

/** Validator of a stored record: a known preset and finite number / boolean overrides; anything else is dropped. */
export function validRecord(raw: unknown): RunRecord | null {
  if (!raw || typeof raw !== 'object') return null;
  const r = raw as any;
  if (r.preset !== 'optimization' && r.preset !== 'simulation') return null;
  const o: Record<string, number | boolean> = {};
  for (const [k, v] of Object.entries(r.overrides ?? {})) {
    if (typeof v === 'boolean' || (typeof v === 'number' && Number.isFinite(v))) o[k] = v;
  }
  return { preset: r.preset, overrides: o };
}

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
  ({ preset: r.preset, overrides: r.overrides, expect_sig: shown.sig });

/** Run is allowed only while the held answer belongs to the current machine, duty, mode and record. */
export const answerKey = (ctx: string, mode: RunMode, r: RunRecord): string => `${ctx}|${mode}|${JSON.stringify([r.preset, r.overrides])}`;
