// The torque at the coupling of a run on the shaft-side convention - pure, shared by the mechanical tab and its tests.

/** |torque at the coupling| of a summary on the shaft-side convention (src/motor_ai_sim/shaft_convention.py
 *  coupling_torque): a motor's output, a generator's input, bearings and windage included when the run names them;
 *  null for an earlier run or without a power and a speed. */
export function shaftCouplingTorqueNm(s: Record<string, unknown> | null | undefined): number | null {
  if (!s || s.shaft_convention !== 'fe_on_shaft_k3d_v1') return null;
  const p = Number(s.P_mech_W), rpm = Number(s.rpm), x = Number(s.P_mech_extra_W);
  const w = 2 * Math.PI * rpm / 60;
  if (!Number.isFinite(p) || !(w > 0)) return null;
  const xx = Number.isFinite(x) ? x : 0;
  const t = (s.op_mode === 'generator' ? p + xx : p - xx) / w;
  return Number.isFinite(t) && t !== 0 ? Math.abs(t) : null;
}
