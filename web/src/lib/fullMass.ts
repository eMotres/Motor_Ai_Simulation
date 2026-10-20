// THE MASS A USER SEES IS THE FULL MOTOR MASS (owner 2026-10-20: «везде полная масса»).
//
// A transient run weighs only the electromagnetic parts + the shaft tube of the geometry it solved (`mass_total_kg`,
// "active mass").  The SERVER adds the rest of the loaded configuration (holders, rings, bearings, screws, board ... =
// `mass_non_active_kg`, from `modules.motor_mass.mass_summary`) and the per-mass figures divided by the full mass.  This file
// only CHOOSES between what the server sent - it computes no mass and no ratio of its own:
//   * `mass_full_kg` present and `mass_partial` not true  -> the full mass, the per-mass figures of the full mass;
//   * otherwise (no CAD build, nothing loaded, or a run stored before this existed) -> the ACTIVE mass, labelled as such:
//     "Active mass (no CAD build)" when the server said the full mass is not available, plain "Active mass" for a run that
//     carries no stamp at all.  Never an unlabelled "Mass".
// Pure: no React, no fetch.

/** The fields of a run summary / a saved Compare row that carry the masses. */
export interface MassSummary {
  mass_total_kg?: number | null;       // EM parts + shaft tube of the simulated geometry (the optimizer's basis)
  mass_active_kg?: number | null;      // EM parts alone
  mass_full_kg?: number | null;        // server: mass_total_kg + the non-active parts of the loaded configuration
  mass_non_active_kg?: number | null;
  mass_partial?: boolean | null;       // server: true = no full mass (no CAD build / nothing loaded)
  mass_partial_reason?: string | null;
  mass_basis?: string | null;          // "die / configuration" the non-active parts were taken from
  mass_scope?: string | null;          // "full motor" | "frameless kit"
  torque_per_mass_Nm_kg?: number | null;
  power_per_mass_W_kg?: number | null;
  Km_per_mass_Nm_sqrtW_kg?: number | null;
  loss_density_W_kg?: number | null;
  torque_per_mass_full_Nm_kg?: number | null;
  power_per_mass_full_W_kg?: number | null;
  Km_per_mass_full_Nm_sqrtW_kg?: number | null;
  loss_density_full_W_kg?: number | null;
}

export type MassKind = 'full' | 'active-no-cad' | 'active';

const num = (v: unknown): number | null => {
  if (v === null || v === undefined || v === '') return null;
  const x = Number(v);
  return Number.isFinite(x) ? x : null;
};

/** What the run's mass IS: the server's full mass, the active mass of a configuration without a CAD build, or the active
 *  mass of a run that carries no full-mass stamp. */
export function massKind(s: MassSummary | null | undefined): MassKind {
  const full = num(s?.mass_full_kg);
  if (s && s.mass_partial !== true && full != null && full > 0) return 'full';
  return s?.mass_partial === true ? 'active-no-cad' : 'active';
}

/** The mass to print as "the" mass of the run, and the active mass for the secondary place. */
export function shownMass(s: MassSummary | null | undefined): { kg: number | null; activeKg: number | null; kind: MassKind } {
  const kind = massKind(s);
  const active = num(s?.mass_total_kg) ?? num(s?.mass_active_kg);
  return { kg: kind === 'full' ? num(s?.mass_full_kg) : active, activeKg: active, kind };
}

export type PerMassKey = 'torque' | 'power' | 'km' | 'loss';
const FULL_KEY: Record<PerMassKey, keyof MassSummary> = {
  torque: 'torque_per_mass_full_Nm_kg', power: 'power_per_mass_full_W_kg',
  km: 'Km_per_mass_full_Nm_sqrtW_kg', loss: 'loss_density_full_W_kg',
};
const ACTIVE_KEY: Record<PerMassKey, keyof MassSummary> = {
  torque: 'torque_per_mass_Nm_kg', power: 'power_per_mass_W_kg',
  km: 'Km_per_mass_Nm_sqrtW_kg', loss: 'loss_density_W_kg',
};

/** A per-mass figure over the FULL mass when the server gave one, else over the active mass (`full` says which). */
export function perMass(s: MassSummary | null | undefined, k: PerMassKey): { value: number | null; full: boolean } {
  if (massKind(s) === 'full') {
    const v = num(s?.[FULL_KEY[k]]);
    if (v != null) return { value: v, full: true };
  }
  return { value: num(s?.[ACTIVE_KEY[k]]), full: false };
}
