// The machine's 3-D end-effect applied to a run summary - ONE function for every panel that draws the Configure tiles
// (the Electromagnetic tab's card and the stored point's panel), so the same run reads the same number in both.
//
// Flux-proportional numbers are rescaled by k_flux, the torque numbers (torque, rotor power, the densities, Kt, Km) by the MEASURED
// k_T when this machine has one, else by k_flux.  Losses are kept 2-D (conservative: the true efficiency sits at or above the
// shown one); ratios (ripple, koefs, saturation droop) are k-invariant and stay untouched.  KV = rpm/V rises as V falls.
// Pure: no React, no fetch.  Moved out of SummaryTable.tsx unchanged (owner 2026-09-30 for the factors, 2026-09-13 for the delta
// line values); the battery-charge block stays there because only that card has one.

/** The summary fields the correction reads and rewrites (every one optional: a field the run lacks is left as it is). */
export interface Apply3dFields {
  T_em_avg_Nm?: number | null;
  P_mech_W?: number | null;
  P_loss_total_W?: number | null;
  efficiency?: number | null;
  torque_per_mass_Nm_kg?: number | null;
  power_per_mass_W_kg?: number | null;
  torque_per_mass_full_Nm_kg?: number | null;
  power_per_mass_full_W_kg?: number | null;
  V_phase_peak_V?: number | null;
  V_phase_rms_V?: number | null;
  V_line_peak_V?: number | null;
  V_line_rms_V?: number | null;
  V_line_peak_solved_V?: number | null;
  V_line_rms_solved_V?: number | null;
  V1_LL_V?: number | null;
  KV_rpm_per_V_line?: number | null;
  KV_noload_rpm_per_V_line?: number | null;
  psi_pm_Wb?: number | null;
  Km_Nm_sqrtW?: number | null;
  Km_per_mass_Nm_sqrtW_kg?: number | null;
  Km_per_mass_full_Nm_sqrtW_kg?: number | null;
  end3d?: { k_flux?: number | null; k_T?: number | null } | null;
}

export interface Apply3dFactors {
  /** the flux factor (voltages, KV, ψ_PM); null = the machine has no 3-D factor */
  k: number | null;
  /** the torque factor: the measured k_T, else the flux factor */
  kq: number | null;
  /** which of the two the torque numbers carry */
  basis: '3-D' | '3-D flux';
}

export function apply3dFactors(summary: Apply3dFields): Apply3dFactors {
  const k = summary.end3d?.k_flux ?? null;
  const kTraw = summary.end3d?.k_T;
  const kTmeas = kTraw != null && kTraw > 0.5 && kTraw <= 1.2 ? kTraw : null;
  return { k, kq: kTmeas ?? k, basis: kTmeas != null ? '3-D' : '3-D flux' };
}

const mul = (v: number | null | undefined, f: number): number | null | undefined => (v != null ? v * f : v);

/** The summary with the 3-D correction applied to its flux- and torque-proportional fields (the input when there is no factor). */
export function apply3dToSummary<T extends Apply3dFields>(summary: T): T {
  const { k, kq: kqRaw } = apply3dFactors(summary);
  if (k == null) return summary;
  const kq = kqRaw ?? k;
  const Pm = summary.P_mech_W != null ? summary.P_mech_W * kq : summary.P_mech_W;
  return {
    ...summary,
    T_em_avg_Nm: mul(summary.T_em_avg_Nm, kq),
    P_mech_W: Pm,
    torque_per_mass_Nm_kg: mul(summary.torque_per_mass_Nm_kg, kq),
    power_per_mass_W_kg: mul(summary.power_per_mass_W_kg, kq),
    // the same torque factor on the per-mass figures over the FULL mass (the server's divisor, not ours)
    torque_per_mass_full_Nm_kg: mul(summary.torque_per_mass_full_Nm_kg, kq),
    power_per_mass_full_W_kg: mul(summary.power_per_mass_full_W_kg, kq),
    V_phase_peak_V: mul(summary.V_phase_peak_V, k),
    V_phase_rms_V: mul(summary.V_phase_rms_V, k),
    V_line_peak_V: mul(summary.V_line_peak_V, k),
    V_line_rms_V: mul(summary.V_line_rms_V, k),
    // the delta tiles read the SOLVER's line values - flux-proportional like every voltage above
    V_line_peak_solved_V: mul(summary.V_line_peak_solved_V, k),
    V_line_rms_solved_V: mul(summary.V_line_rms_solved_V, k),
    V1_LL_V: mul(summary.V1_LL_V, k),
    KV_rpm_per_V_line: summary.KV_rpm_per_V_line != null ? summary.KV_rpm_per_V_line / k : summary.KV_rpm_per_V_line,
    KV_noload_rpm_per_V_line: summary.KV_noload_rpm_per_V_line != null
      ? summary.KV_noload_rpm_per_V_line / k : summary.KV_noload_rpm_per_V_line,
    psi_pm_Wb: mul(summary.psi_pm_Wb, k),
    // Km and Km/mass are NOT flux-scaled: they carry the torque factor, like the torque
    Km_Nm_sqrtW: mul(summary.Km_Nm_sqrtW, kq),
    Km_per_mass_Nm_sqrtW_kg: mul(summary.Km_per_mass_Nm_sqrtW_kg, kq),
    Km_per_mass_full_Nm_sqrtW_kg: mul(summary.Km_per_mass_full_Nm_sqrtW_kg, kq),
    efficiency: (Pm != null && Pm > 0 && summary.P_loss_total_W != null && summary.P_loss_total_W > 0)
      ? Pm / (Pm + summary.P_loss_total_W) : summary.efficiency,
  };
}
