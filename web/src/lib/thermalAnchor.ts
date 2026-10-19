// Configure's lumped thermal network ANCHORED to a duty's coupled FEM record (owner 2026-10-19: one model, anchored to
// the server's FEM truth).  Where no calibrated S1 network applies (still air / robotics, typed air) the server fits the
// network's three paths to the duty's stored coupled record (motor_ai_sim.configure_anchor, sent by configure_load) and
// the page evaluates it for every knob move: the page's own losses scaled by the record/page ratio at the record's point
// (the stored point IS the record), the housing conductance following the film.  Same arithmetic as
// configure_anchor.anchored_temperatures (parity: tests/test_configure_anchor.py).
//
// Pure, no imports: web/src/lib/__tests__/thermalAnchor.test.mjs imports this file.

export interface ThermalAnchor {
  schema: string;
  duty: string;
  cooling_mode: string;
  air_speed_ms: number;
  ambient_C: number;
  point: { rpm: number; I_A: number };
  drive: string;
  record: { T_winding_C: number; T_magnet_C: number; T_housing_C: number; P_total_W: number; P_cu_W: number; P_rotor_W: number };
  G_housing_W_K: number; R_winding_K_W: number; R_gap_K_W: number;
  computed_at?: string | null;
}

export interface LossRatios { cu: number; rotor: number; total: number }

/** record / page at the record's point, per path (1 where the page has nothing to scale) */
export function anchorLossRatios(a: ThermalAnchor, pageAtPoint: { P_cu_W: number; P_rotor_W: number; P_total_W: number }): LossRatios {
  const r = (rec: number, page: number) => (page > 1e-9 && rec > 0 ? rec / page : 1);
  return { cu: r(a.record.P_cu_W, pageAtPoint.P_cu_W), rotor: r(a.record.P_rotor_W, pageAtPoint.P_rotor_W),
           total: r(a.record.P_total_W, pageAtPoint.P_total_W) };
}

/** configure_anchor.anchored_temperatures */
export function anchoredTemperatures(a: ThermalAnchor, pCu: number, pRotor: number, pTotal: number, h: number, hRef: number,
                                     ambientC: number, k: LossRatios = { cu: 1, rotor: 1, total: 1 }, pExtra = 0):
  { housing: number; winding: number; magnet: number } {
  const gH = a.G_housing_W_K * (Math.max(h, 1e-9) / Math.max(hRef, 1e-9));
  const xH = (k.total * pTotal + pExtra) / Math.max(gH, 1e-12);
  const tH = ambientC + xH;
  return { housing: tH, winding: tH + (k.cu * pCu + pExtra) * a.R_winding_K_W, magnet: tH + k.rotor * pRotor * a.R_gap_K_W };
}
