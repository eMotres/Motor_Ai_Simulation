/**
 * What a sweep RAN with: ONE reader for the result on screen, the saved-sweeps list, Apply and Compare (owner 2026-11-24).
 *
 * Scope: the STEPS PER PERIOD and the GAP ROWS only.  The label is recomputed from the stamp (`mesh_preset`, written by the server
 * from the SOLVED points): a preset's name only when that pair equals the preset's, `custom` when it does not, `mixed` when the
 * solved points disagree.  `sig` is the digest of the (steps, gap rows) pair; it is null for a legacy sweep (stored before the
 * stamp), a mixed one, one whose solve disagreed with its request, one that no solve verified, or one with a null value.  Two
 * sweeps are ranked together only when BOTH digests are known and equal: unknown never matches anything.
 *
 * Since the Optimization preset moved its wire cell to 2h (preset version 2, owner 2026-11-24) the digest also covers the preset
 * VERSION and the WIRE CELL (`coil_rel`) the run used; a stamp without them (`sig_scope` other than 2) has no digest, so a sweep
 * stored under version 1 is never ranked with a version 2 one.  Stored sweeps are not rewritten.
 */
export interface SweepSettings {
  preset: string | null;          // 'optimization' | 'simulation' | 'custom' | null (legacy: no stamp)
  steps: number | null;
  gapLayers: number | null;
  custom: boolean;
  mixed: boolean;
  sig: number | null;
  presetVersion: number | null;
  coilRel: number | null;
  source: 'preset' | 'stored_request';
}

const numOrNull = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);
const sigOrNull = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) && v > 0 ? v : null);

/** The settings of a list row (`settings` from the server), or of a result carrying `mesh_preset` / `steps_per_period`. */
export function sweepSettingsOf(x: any): SweepSettings {
  const s = x?.settings;
  if (s && typeof s === 'object') {
    const mixed = s.mixed === true;
    const custom = s.custom === true || mixed;
    const es = s.effective_source && typeof s.effective_source === 'object' ? s.effective_source : null;
    const bothSolved = !!es && es.steps_per_period === 'solved' && es.gap_layers === 'solved' && (!(('coil_rel' in es) || s.coil_rel != null) || es.coil_rel === 'solved');
    return { preset: s.source === 'preset' ? (custom ? 'custom' : (typeof s.preset === 'string' ? s.preset : null)) : null,
             steps: numOrNull(s.steps_per_period), gapLayers: numOrNull(s.gap_layers), custom, mixed,
             sig: s.source === 'preset' && !mixed && bothSolved && s.sig_scope === 2 && numOrNull(s.steps_per_period) !== null
               && numOrNull(s.gap_layers) !== null ? sigOrNull(s.sig) : null,
             presetVersion: numOrNull(s.version), coilRel: numOrNull(s.coil_rel),
             source: s.source === 'preset' ? 'preset' : 'stored_request' };
  }
  const mp = x?.mesh_preset;
  const eff = mp?.effective;
  if (eff && typeof eff === 'object' && 'steps_per_period' in eff) {
    const srcs = mp.effective_source && typeof mp.effective_source === 'object' ? Object.values(mp.effective_source) : [];
    const mixed = mp.mixed === true || srcs.includes('mixed');
    const custom = mp.custom === true || mixed || mp.solved_differs === true;
    const verified = mp.effective_source?.steps_per_period === 'solved' && mp.effective_source?.gap_layers === 'solved'
      && (!(('coil_rel' in (mp.effective_source ?? {})) || eff.coil_rel != null || mp.coil_rel != null) || mp.effective_source.coil_rel === 'solved');
    const sig = mixed || mp.solved_differs === true || !verified || numOrNull(eff.steps_per_period) === null
      || numOrNull(eff.gap_layers) === null || mp.sig_scope !== 2 ? null : sigOrNull(mp.sig);
    return { preset: custom ? 'custom' : (typeof mp.name === 'string' ? mp.name : null),
             steps: numOrNull(eff.steps_per_period), gapLayers: numOrNull(eff.gap_layers), custom, mixed, sig,
             presetVersion: numOrNull(mp.version), coilRel: numOrNull(eff.coil_rel ?? mp.coil_rel), source: 'preset' };
  }
  return { preset: null, steps: numOrNull(x?.steps_per_period), gapLayers: numOrNull(x?.scan_params?.gap_layers),
           custom: false, mixed: false, sig: null,
           presetVersion: null, coilRel: null, source: 'stored_request' };
}

/** "Optimization v2 · 48 steps · 1 gap row · wire 2h", "custom · 108 steps · 2 gap rows", "mixed", or "108 steps · 1 gap row" (legacy). */
export function sweepSettingsLabel(s: SweepSettings, presetLabel?: string): string {
  if (s.mixed) return 'mixed';
  const parts: string[] = [];
  if (s.preset) {
    const nm = s.preset === 'custom' ? 'custom' : (presetLabel ?? s.preset.charAt(0).toUpperCase() + s.preset.slice(1));
    parts.push(s.presetVersion !== null ? `${nm} v${s.presetVersion}` : nm);
  }
  if (s.steps !== null) parts.push(`${s.steps} steps`);
  if (s.gapLayers !== null) parts.push(`${s.gapLayers} gap ${s.gapLayers === 1 ? 'row' : 'rows'}`);
  if (s.coilRel !== null) parts.push(`wire ${s.coilRel}h`);
  return parts.join(' · ');
}

/** Two sweeps are the same settings only when BOTH digests are known and equal (fail closed on unknown / legacy / mixed). */
export function sweepSettingsMismatch(a: SweepSettings, b: SweepSettings): boolean {
  return a.sig === null || b.sig === null || a.sig !== b.sig;
}

/** Numbers-only provenance that rides with an applied sweep point into the saved metrics (and so into Compare):
 *  the sweep origin always, the digest only when known (a mixed / legacy / disagreeing sweep never carries one). */
export function sweepSettingsMetrics(s: SweepSettings): Record<string, number> {
  const m: Record<string, number> = { sweep_origin: 1 };
  if (s.sig !== null) { m.sweep_sig = s.sig; m.sweep_sig_scope = 2; }
  if (s.steps !== null && !s.mixed) m.sweep_steps = s.steps;
  if (s.gapLayers !== null && !s.mixed) m.sweep_gap_layers = s.gapLayers;
  return m;
}

/** A saved row came from a sweep point: the origin mark of this change, or (a legacy applied point) the sweep-only metric
 *  `raw_samples_per_cogging_cycle` that only the sweep's Apply wrote. */
export const isSweepRow = (r: Record<string, unknown> | undefined | null): boolean =>
  !!r && (Number((r as any).sweep_origin) === 1 || Number.isFinite(Number((r as any).raw_samples_per_cogging_cycle)) &&
          (r as any).raw_samples_per_cogging_cycle !== null && (r as any).raw_samples_per_cogging_cycle !== undefined);

/** Saved results are ranked together only when the steps-and-gap identity is KNOWN and equal.  Checked BEFORE any count
 *  shortcut: one sweep row with a null / zero / non-numeric digest (unknown, mixed, legacy) is not comparable with anything, a
 *  sweep row beside an ordinary (non-sweep) row is not either, and two sweep rows need equal digests. */
export function sweepMetricsComparable(rows: Array<Record<string, unknown> | undefined | null>): boolean {
  const valid = rows.filter(r => !!r);
  const sweep = valid.filter(isSweepRow);
  if (!sweep.length) return true;
  if (sweep.length !== valid.length) return false;
  if (sweep.some(r => Number((r as any).sweep_sig_scope) !== 2)) return false;   // a digest stored without the scope is unknown
  const sigs = sweep.map(r => (r as any).sweep_sig);
  if (sigs.some(v => typeof v !== 'number' || !Number.isFinite(v) || v <= 0)) return false;
  return new Set(sigs as number[]).size === 1;
}
