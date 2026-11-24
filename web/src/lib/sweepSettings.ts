/**
 * What a sweep RAN with (owner 2026-11-23: optimisation runs on the Optimization preset).
 *
 * A new sweep stamps its preset and the effective steps / gap rows (`mesh_preset.effective`); a sweep stored before that
 * stamp is labelled from its own stored request, so a 108-step sweep and a 48-step sweep are never mixed silently.
 */
export interface SweepSettings {
  preset: string | null;
  steps: number | null;
  gapLayers: number | null;
  source: 'preset' | 'stored_request';
}

const numOrNull = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);

/** The settings of a list row (`settings` from the server) or of a result carrying `mesh_preset` / `steps_per_period`. */
export function sweepSettingsOf(x: any): SweepSettings {
  const s = x?.settings;
  if (s && typeof s === 'object') {
    return { preset: typeof s.preset === 'string' ? s.preset : null, steps: numOrNull(s.steps_per_period),
             gapLayers: numOrNull(s.gap_layers), source: s.source === 'preset' ? 'preset' : 'stored_request' };
  }
  const mp = x?.mesh_preset;
  const eff = mp?.effective;
  if (eff && numOrNull(eff.steps_per_period) !== null) {
    return { preset: typeof mp.name === 'string' ? mp.name : null, steps: numOrNull(eff.steps_per_period),
             gapLayers: numOrNull(eff.gap_layers), source: 'preset' };
  }
  return { preset: null, steps: numOrNull(x?.steps_per_period), gapLayers: numOrNull(x?.scan_params?.gap_layers),
           source: 'stored_request' };
}

/** "Optimization · 48 steps · 1 gap row", or "108 steps · 1 gap row" for a sweep without a preset stamp. */
export function sweepSettingsLabel(s: SweepSettings, presetLabel?: string): string {
  const parts: string[] = [];
  if (s.preset) parts.push(s.preset === 'custom' ? 'custom' : (presetLabel ?? s.preset.charAt(0).toUpperCase() + s.preset.slice(1)));
  if (s.steps !== null) parts.push(`${s.steps} steps`);
  if (s.gapLayers !== null) parts.push(`${s.gapLayers} gap ${s.gapLayers === 1 ? 'row' : 'rows'}`);
  return parts.join(' · ');
}

/** True when two sweeps ran at different steps or gap rows (an unknown value never matches a known one). */
export function sweepSettingsMismatch(a: SweepSettings, b: SweepSettings): boolean {
  return a.steps !== b.steps || a.gapLayers !== b.gapLayers;
}

/** 1 = Optimization preset, 2 = Simulation preset, 0 = custom or no preset stamp (a sweep stored before the stamp). */
export const sweepPresetCode = (s: SweepSettings): number =>
  (s.preset === 'optimization' ? 1 : s.preset === 'simulation' ? 2 : 0);

/** Numbers-only provenance that rides with an applied sweep point into the saved metrics (and so into Compare). */
export function sweepSettingsMetrics(s: SweepSettings): Record<string, number> {
  const m: Record<string, number> = { sweep_preset_code: sweepPresetCode(s) };
  if (s.steps !== null) m.sweep_steps = s.steps;
  if (s.gapLayers !== null) m.sweep_gap_layers = s.gapLayers;
  return m;
}

/** Saved results compare only when every one that carries sweep settings carries KNOWN, equal ones (steps, gap rows,
 *  preset). A row without them is not a sweep row and is ignored; a sweep row with an unknown value matches nothing. */
export function sweepMetricsComparable(rows: Array<Record<string, unknown> | undefined | null>): boolean {
  const have = rows.filter(r => r && ('sweep_steps' in r || 'sweep_gap_layers' in r || 'sweep_preset_code' in r));
  if (have.length < 2) return true;
  const keys = have.map(r => {
    const st = Number((r as any).sweep_steps), gl = Number((r as any).sweep_gap_layers), pc = Number((r as any).sweep_preset_code);
    if (![st, gl, pc].every(Number.isFinite)) return null;
    return `${st}|${gl}|${pc}`;
  });
  return !keys.includes(null) && new Set(keys).size === 1;
}
