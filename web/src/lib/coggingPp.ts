/** The Sweep's opt-in "Cogging pp": the cogging torque peak-to-peak [N*m] of a geometry's own no-load solve.
 *  Pure functions (`node --test` can import this file).  A point that asked for it always carries the key
 *  `cogging_pp_Nm`: a number, or null = MISSING (the loaded values of the point stay). */
import { coggingDigits } from './coggingTile';

export interface CoggingPp {
  asked: boolean;          // the sweep measured it (the key is on the point)
  value?: number;          // finite N*m, absent = missing
  final: boolean;          // a successful >= 120-step, 20 C measurement; else it is for ranking only
  error?: string;
  steps?: number;          // steps per period the solver really used
  tempC?: number;          // the resolved magnet temperature
  gapLayers?: number;
  sectors?: number;
  meshMm?: number;
  demagAtLoad: boolean;    // the loaded points run with demagnetisation ON (the value is still demag-off, full strength)
  basis?: string;          // always the explicit no-load / full-strength basis when measured
}

const fin = (v: unknown): number | undefined => (typeof v === 'number' && Number.isFinite(v) ? v : undefined);

export function coggingOf(p: any): CoggingPp {
  const asked = !!p && Object.prototype.hasOwnProperty.call(p, 'cogging_pp_Nm');
  if (!asked) return { asked: false, final: false, demagAtLoad: false };
  const value = fin(p.cogging_pp_Nm);
  return {
    asked, demagAtLoad: p.cogging_demag_at_load === true, basis: typeof p.cogging_basis === 'string' ? p.cogging_basis : undefined, value, final: value !== undefined && p.cogging_final === true,
    error: typeof p.cogging_error === 'string' ? p.cogging_error : undefined,
    steps: fin(p.cogging_steps_per_period), tempC: fin(p.cogging_magnet_temp_c),
    gapLayers: fin(p.cogging_gap_layers), sectors: fin(p.cogging_n_sectors), meshMm: fin(p.cogging_mesh_mm),
  };
}

/** The value as shown: digits, "-" when missing, and the ranking-only label on every sub-final value. */
export function coggingText(c: CoggingPp, rankingLabel: string): string {
  if (c.value === undefined) return '-';
  const v = c.value.toFixed(coggingDigits(c.value));
  return c.final ? v : `${v} (${rankingLabel})`;
}

/** The settings two values must share to be compared (steps actually used, temperature, mesh / gap / sectors). */
export function coggingSettingsKey(c: CoggingPp): string {
  return [c.steps, c.tempC, c.meshMm, c.gapLayers, c.sectors, c.basis, c.demagAtLoad ? 'd' : 'n'].map(x => (x === undefined ? '?' : String(x))).join('|');
}

/** Numbers-only provenance that rides with the value into the applied-design metrics (and so into Compare). */
export function coggingMetrics(c: CoggingPp): Record<string, number> {
  if (c.value === undefined) return {};
  const m: Record<string, number> = { cogging_pp_Nm: c.value, cogging_final: c.final ? 1 : 0,
    cogging_basis_code: 1,                          // 1 = no-load, full-strength magnets (demag at load not applied)
    cogging_demag_at_load: c.demagAtLoad ? 1 : 0 };
  if (c.steps !== undefined) m.cogging_steps = c.steps;
  if (c.tempC !== undefined) m.cogging_temp_c = c.tempC;
  if (c.meshMm !== undefined) m.cogging_mesh_mm = c.meshMm;
  if (c.gapLayers !== undefined) m.cogging_gap_layers = c.gapLayers;
  if (c.sectors !== undefined) m.cogging_sectors = c.sectors;
  return m;
}

/** The hover text of a Cogging pp value: why it is missing, or its ranking-only status, and ALWAYS the basis when the
 *  loaded points have demagnetisation on.  `tr` is the translator of the simulation namespace. */
export function coggingTip(c: CoggingPp, tr: (key: string) => string): string | undefined {
  if (c.value === undefined) return c.error || tr('coggingPpMissing');
  const parts: string[] = [];
  if (!c.final) parts.push(tr('coggingPpRankingTip'));
  if (c.demagAtLoad) parts.push(tr('coggingPpBasis'));
  return parts.length ? parts.join(' ') : undefined;
}
