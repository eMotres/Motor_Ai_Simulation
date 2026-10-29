/**
 * Which torque ripple may be quoted (owner 2026-10-29, option 1) — the web
 * twin of `motor_ai_sim/ripple_accuracy.py`; the two must agree.
 *
 * The machine runs at its configured air-gap layers (1/side by default): enough
 * for torque, losses and heat, not always for the ripple.  When the rotor- and
 * stator-side rings disagree past the 5 % gate (`ripple_mesh_limited`) the
 * ripple is NOT ACCURATE until the engineer asks for "Refine ripple" (the same
 * point at >= 3 layers/side), whose answer rides on the summary as
 * `ripple_refined`.  The refined value wins wherever it exists.
 *
 * Dependency-free (no `import.meta.env`) so node imports the shipped file.
 */

export interface RippleRefined {
  T_ripple_pct?: number | null;
  T_ripple_pp_Nm?: number | null;
  T_avg_Nm?: number | null;
  gap_layers_per_side?: number | null;
  base_gap_layers_per_side?: number | null;
  self_check_rel_to_ripple_scale?: number | null;
  passed?: boolean | null;
  computed_at?: string | null;
}

export interface RippleSummary {
  T_ripple_pct?: number | null;
  ripple_mesh_limited?: boolean | null;
  ripple_self_check_rel?: number | null;
  gap_layers_effective?: number | null;
  ripple_refined?: RippleRefined | null;
}

export interface QuotedRipple {
  /** the ripple to quote [%], refined when present */
  value: number | null;
  /** true / false / null = no ring check on record */
  accurate: boolean | null;
  refined: boolean;
  /** gap layers/side the quoted value was solved at */
  layers: number | null;
  /** ring check of the quoted value, fraction of the ripple scale */
  ringError: number | null;
  /** the base run's own ripple, when the quoted one is refined */
  baseValue: number | null;
  baseLayers: number | null;
}

/** The layer count "Refine ripple" starts at (routes.simulation.RIPPLE_REFINE_LAYERS). */
export const RIPPLE_REFINE_LAYERS = 3;

const num = (v: unknown): number | null => {
  const x = typeof v === 'number' ? v : v == null ? NaN : Number(v);
  return Number.isFinite(x) ? x : null;
};

export function quotedRipple(s: RippleSummary | null | undefined,
                             fallback?: number | null): QuotedRipple {
  const base = num(s?.T_ripple_pct) ?? num(fallback);
  const baseLayers = num(s?.gap_layers_effective);
  const r = s?.ripple_refined;
  if (r && num(r.T_ripple_pct) != null) {
    return { value: num(r.T_ripple_pct), accurate: r.passed === true, refined: true,
             layers: num(r.gap_layers_per_side),
             ringError: num(r.self_check_rel_to_ripple_scale),
             baseValue: base, baseLayers: num(r.base_gap_layers_per_side) ?? baseLayers };
  }
  const lim = s?.ripple_mesh_limited;
  return { value: base, accurate: lim == null ? null : !lim, refined: false,
           layers: baseLayers, ringError: num(s?.ripple_self_check_rel),
           baseValue: null, baseLayers: null };
}

/** True when the card should offer "Refine ripple": a ripple whose ring check
 *  failed and that has not been refined yet. */
export function canRefineRipple(s: RippleSummary | null | undefined): boolean {
  const q = quotedRipple(s);
  return q.value != null && !q.refined && q.accurate === false;
}
