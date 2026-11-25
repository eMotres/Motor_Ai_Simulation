// Which 3-D end-effect factor a SWEEP POINT is scaled by (pure; the panel keeps the localStorage reads).
//
// Every point is scaled by its OWN k_flux when it carries one (`point.end3d.k_flux`: the passport value of the machine the point
// was solved for), else by the loaded machine's k as before.  The switch (Simulation's "3D" button off) shows the raw 2-D
// numbers for every point.  A value outside (0, 1.2] is not a factor.
export const validK = (v: unknown): number | null => {
  const k = Number(v);
  return v != null && Number.isFinite(k) && k > 0 && k <= 1.2 ? k : null;
};

export const pointApply3dK = (
  point: { end3d?: { k_flux?: unknown } | null; shaft_convention?: unknown } | null | undefined,
  machineK: number | null,
  switchOn: boolean,
): number | null => {
  if (!switchOn) return null;
  // A point on the shaft-side convention (src/motor_ai_sim/shaft_convention.py) is never scaled here: its shaft power,
  // efficiency and densities already carry its own factor (on the field part only, the core loss on the shaft), and an
  // optimiser candidate solved without a passport stays 2-D by decision (owner 2026-11-25), not the loaded machine's k.
  if (isMarkedPoint(point)) return null;
  return validK(point?.end3d?.k_flux) ?? machineK;
};

/** True for a sweep point on the shaft-side convention. */
export const isMarkedPoint = (point: { shaft_convention?: unknown } | null | undefined): boolean =>
  !!point && point.shaft_convention === 'fe_on_shaft_k3d_v1';

/** The fields of a marked point an applied summary must carry with its numbers (all of them, or none). */
export const SHAFT_POINT_KEYS = ['shaft_convention', 'shaft_k3d', 'torque_side', 'shaft_rotor_in_field',
  'P_torque_embedded_W', 'P_cu_ac_field_W', 'P_shaft_drag_W', 'P_shaft_drag_terms', 'P_field_W', 'P_gap_W',
  'T_field_k_Nm', 'T_shaft_Nm', 'P_elec_W', 'shaft_basis_note', 'op_mode', 'P_mag_W', 'P_shaft_W', 'P_sleeve_W'] as const;

export function shaftPointFields(point: Record<string, unknown> | null | undefined): Record<string, unknown> {
  if (!isMarkedPoint(point as any)) return {};
  const out: Record<string, unknown> = {};
  for (const k of SHAFT_POINT_KEYS) if ((point as any)[k] !== undefined) out[k] = (point as any)[k];
  return out;
}
