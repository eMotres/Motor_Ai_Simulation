// THE thermal model of a configuration, evaluated in the browser (owner 2026-10-19: "one model only, the server is
// the truth").  This is motor_ai_sim.propeller_s1.thermal_at line for line: the passport's losses at (rpm, I) - the
// loss grid (linear in current, power law between speeds), the copper at its own temperature, bearings + windage, the
// drive variant's PWM extra loss - through the calibrated lumped network (housing film factor, the winding-to-air and
// rotor-to-air paths, the gap factor), iterated in the winding temperature.  The server sends the model once per load
// (configure_load `thermal_model`, propeller_s1.configure_thermal_model); every knob move is evaluated here, at once.
// Parity with the server: tests/test_configure_thermal_parity.py.
//
// Pure, no imports: web/src/lib/__tests__/s1Thermal.test.mjs imports this file.

type Table = (number | null)[][];

export interface S1ThermalModel {
  schema: string;
  die?: string | null; config?: string | null;
  R_phase_ohm: number; R_temp_C: number;
  /** the load side (propeller_s1.load_point): torque map x torque_factor x the flux law of the magnets */
  torque_factor: number; flux_temp_coeff_per_K: number; magnet_ref_temp_C: number; I_cap_A: number;
  grid: { rpm: number[]; I_A: number[]; T_Nm: Table; loss_W: Record<string, Table>; I_floor_A?: number[] };
  mech_W?: { rpm: number[]; W: number[] } | null;
  drives: Record<string, { rpm: number[]; I_A: number[]; pwm_extra_W: Table | null }>;
  thermal_geom: { statorOD_mm: number; stackLength_mm: number; numSlots: number; slotHeight_mm: number;
    slotWidth_mm: number; insulation_mm: number; coreThickness_mm: number; airGap_mm: number; magnetOD_mm: number };
  calibration: { housing_film_factor: number; winding_air_area_m2: number; rotor_air_area_m2: number;
    gap_resistance_factor: number };
  calibrated: boolean;
  slipstream_factor: number | null;
  fit_domain?: [number, number] | null;
  limits: { winding_C: number; magnet_C: number };
}

export interface S1ThermalPoint {
  T_winding_C: number; T_magnet_C: number; T_housing_C: number;
  losses_W: { cu_dc: number; cu_ac: number; fe_stator: number; fe_rotor: number; rotor_eddy: number;
    pwm_extra: number; mech: number; motor_total: number };
  iterations: number;
}

const ALPHA_CU = 0.00393;
const T_CU_REF_C = 20.0;
const RUNAWAY_C = 600.0;
const FIXED_POINT_TOL_K = 0.01;
const FIXED_POINT_MAX_IT = 400;
const K_IRON = 25.0, K_SLOT = 1.4, K_INS = 0.2, K_GAP = 0.06;
const LOSS_GROUPS = ['cu_ac', 'fe_stator', 'fe_rotor', 'rotor_eddy', 'pwm_extra'] as const;

function bracket(xs: readonly number[], x: number): [number, number] {
  let lo = 0, hi = xs.length - 1;
  while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (xs[mid] <= x) lo = mid; else hi = mid; }
  return [lo, hi];
}

/** propeller_s1._along_current: `zeroAtZero` for the torque (towards 0 at I = 0 / at the floor), else losses */
function alongCurrent(row: readonly (number | null)[], cur: readonly number[], I: number, floor = 0,
                      zeroAtZero = false): number | null {
  if (I > cur[cur.length - 1] + 1e-9) return null;
  let k0 = 0;
  let base = 0;
  if (floor > 0) {
    if (I < floor - 1e-9) return null;
    k0 = row.findIndex((y) => y != null);
    if (k0 < 0) return null;
    base = floor;
  }
  if (I <= cur[k0]) {
    const y0 = row[k0];
    if (y0 == null) return null;
    if (zeroAtZero) return cur[k0] > base ? (y0 * (I - base)) / (cur[k0] - base) : y0;
    const y1 = k0 + 1 < row.length ? row[k0 + 1] : null;
    if (y1 == null) return Math.max(y0, 0);
    return Math.max(y0 + ((y1 - y0) * (I - cur[k0])) / (cur[k0 + 1] - cur[k0]), 0);
  }
  const [lo, hi] = bracket(cur, I);
  const a = row[lo], b = row[hi];
  if (a == null || b == null) return null;
  return a + ((b - a) * (I - cur[lo])) / (cur[hi] - cur[lo]);
}

/** propeller_s1._along_speed with the power law (losses) */
function alongSpeed(x0: number, y0: number, x1: number, y1: number, x: number): number {
  if (x0 > 0 && x1 > 0 && y0 > 0 && y1 > 0 && x > 0) {
    const k = Math.log(y1 / y0) / Math.log(x1 / x0);
    return y0 * (x / x0) ** k;
  }
  return y0 + ((y1 - y0) * (x - x0)) / (x1 - x0);
}

/** propeller_s1.grid_at, kind "loss" */
export function lossAt(rpmAx: readonly number[], curAx: readonly number[], table: Table, rpm: number, I: number,
                       floors?: readonly number[] | null): number | null {
  if (rpm > rpmAx[rpmAx.length - 1] + 1e-6 || rpm < 0) return null;
  const f = floors && floors.length ? floors : rpmAx.map(() => 0);
  const below = rpm < rpmAx[0];
  const [i0, i1] = below ? [0, 1] : bracket(rpmAx, rpm);
  const y0 = alongCurrent(table[i0], curAx, I, f[i0]);
  if (!below && rpm === rpmAx[i0]) return y0 == null ? null : Math.max(y0, 0);
  const y1 = alongCurrent(table[i1], curAx, I, f[i1]);
  if (y0 == null || y1 == null) return null;
  return Math.max(alongSpeed(rpmAx[i0], y0, rpmAx[i1], y1, rpm), 0);
}

function mechW(m: S1ThermalModel, rpm: number): number {
  const t = m.mech_W;
  if (!t || !t.rpm?.length) return 0;
  const xs = t.rpm, ys = t.W;
  if (rpm <= 0) return 0;
  if (rpm <= xs[0]) return xs[0] > 0 ? (ys[0] * rpm) / xs[0] : ys[0];
  if (rpm >= xs[xs.length - 1]) { const n = xs.length; return alongSpeed(xs[n - 2], ys[n - 2], xs[n - 1], ys[n - 1], rpm); }
  const [lo, hi] = bracket(xs, rpm);
  return alongSpeed(xs[lo], ys[lo], xs[hi], ys[hi], rpm);
}

export function resistance(m: S1ThermalModel, tW: number): number {
  return (m.R_phase_ohm * (1 + ALPHA_CU * (tW - T_CU_REF_C))) / (1 + ALPHA_CU * (m.R_temp_C - T_CU_REF_C));
}

/** propeller_s1.losses: null when a group the grid has gives no value here */
export function s1Losses(m: S1ThermalModel, rpm: number, I: number, tW: number, driveId: string | null):
  S1ThermalPoint['losses_W'] | null {
  const g = m.grid;
  const r = resistance(m, tW);
  const out: Record<string, number> = { cu_dc: 3 * I * I * r };
  for (const k of LOSS_GROUPS) {
    const t = g.loss_W[k];
    if (!t) { out[k] = 0; continue; }
    const v = lossAt(g.rpm, g.I_A, t, rpm, I, g.I_floor_A);
    if (v == null) return null;
    out[k] = v;
  }
  const d = driveId ? m.drives[driveId] : undefined;
  if (!g.loss_W.pwm_extra && d?.pwm_extra_W) {
    const v = lossAt(d.rpm, d.I_A, d.pwm_extra_W, rpm, I);
    out.pwm_extra = v == null ? 0 : v;                     // not computed there: 0, as the server counts it
  }
  out.cu_ac *= resistance(m, m.R_temp_C) / r;
  out.mech = mechW(m, rpm);
  out.motor_total = out.cu_dc + out.cu_ac + out.fe_stator + out.fe_rotor + out.rotor_eddy + out.pwm_extra + out.mech;
  return out as S1ThermalPoint['losses_W'];
}

/** propeller_s1.temperatures: the calibrated three-node network (housing, winding, rotor) */
export function networkTemperatures(geom: S1ThermalModel['thermal_geom'], pCu: number, pRotor: number, pTotal: number,
                                    h: number, ambientC: number, cal: S1ThermalModel['calibration']):
  { housing: number; winding: number; magnet: number } {
  const mm = (x: number) => Math.max(0, Number(x) || 0) / 1000;
  const D = mm(geom.statorOD_mm) || 0.1;
  const L = mm(geom.stackLength_mm) || 0.03;
  const hh = Math.max(1, h);
  const aCyl = Math.PI * D * L;
  const aSurf = aCyl + 2 * Math.PI * (D / 2) ** 2;
  const aSlot = Math.max(1e-4, (geom.numSlots || 12) * 2 * mm(geom.slotHeight_mm) * L);
  const rW = (mm(geom.slotWidth_mm) * 0.5) / (K_SLOT * aSlot) + mm(geom.insulation_mm) / (K_INS * aSlot)
    + mm(geom.coreThickness_mm) / (K_IRON * Math.max(1e-4, aCyl));
  const aGap = Math.max(1e-4, Math.PI * mm(geom.magnetOD_mm) * L);
  const rGap = cal.gap_resistance_factor * mm(geom.airGap_mm) / (K_GAP * aGap);
  const gH = cal.housing_film_factor * hh * aSurf;
  const gWa = cal.winding_air_area_m2 * hh;
  const gRa = cal.rotor_air_area_m2 * hh;
  const gW = 1 / rW, gR = 1 / rGap;
  const pH = pTotal - pCu - pRotor;
  const a11 = gH + gW + gR, a12 = -gW, a13 = -gR;
  const a21 = -gW, a22 = gW + gWa;
  const a31 = -gR, a33 = gR + gRa;
  const s = a11 - (a12 * a21) / a22 - (a13 * a31) / a33;
  const xH = (pH - (a12 * pCu) / a22 - (a13 * pRotor) / a33) / s;
  const xW = (pCu - a21 * xH) / a22;
  const xR = (pRotor - a31 * xH) / a33;
  return { housing: ambientC + xH, winding: ambientC + xW, magnet: ambientC + xR };
}

/** propeller_s1.thermal_at: the steady temperatures at the knobs' (rpm, I) with the film `h` of the air over the
 *  housing.  null = the loss grid has no value here, a runaway or no settled state (nothing is shown). */
export function s1ThermalAt(m: S1ThermalModel, rpm: number, I: number, h: number, ambientC: number,
                            driveId: string | null): S1ThermalPoint | null {
  const cal = m.calibration;
  let tW = ambientC;
  let damping = 1, lastRes = Infinity;
  for (let it = 1; it <= FIXED_POINT_MAX_IT; it++) {
    const ls = s1Losses(m, rpm, I, tW, driveId);
    if (!ls) return null;
    const th = networkTemperatures(m.thermal_geom, ls.cu_dc + ls.cu_ac, ls.fe_rotor + ls.rotor_eddy, ls.motor_total, h, ambientC, cal);
    const res = Math.abs(th.winding - tW);
    if (res > lastRes * 0.999 && it > 3) damping = Math.max(0.05, damping * 0.5);
    lastRes = res;
    tW += damping * (th.winding - tW);
    if (tW > RUNAWAY_C) return null;
    if (res < FIXED_POINT_TOL_K) {
      const l2 = s1Losses(m, rpm, I, tW, driveId);
      if (!l2) return null;
      const t2 = networkTemperatures(m.thermal_geom, l2.cu_dc + l2.cu_ac, l2.fe_rotor + l2.rotor_eddy, l2.motor_total, h, ambientC, cal);
      return { T_winding_C: t2.winding, T_magnet_C: t2.magnet, T_housing_C: t2.housing, losses_W: l2, iterations: it };
    }
  }
  return null;
}

// ── the load side: propeller_s1.load_point (Configure's current in propeller mode) ─────────────────────────────

/** propeller_s1.grid_at, kind "torque": linear in speed, held below the first speed, 0 at I = 0 (or at the floor) */
function torqueGridAt(m: S1ThermalModel, rpm: number, I: number): number | null {
  const ax = m.grid.rpm, cur = m.grid.I_A, t = m.grid.T_Nm;
  if (rpm > ax[ax.length - 1] + 1e-6 || rpm < 0) return null;
  const f = m.grid.I_floor_A && m.grid.I_floor_A.length ? m.grid.I_floor_A : ax.map(() => 0);
  const below = rpm < ax[0];
  const [i0, i1] = below ? [0, 1] : bracket(ax, rpm);
  const y0 = alongCurrent(t[i0], cur, I, f[i0], true);
  if (below || rpm === ax[i0]) return y0;
  const y1 = alongCurrent(t[i1], cur, I, f[i1], true);
  if (y0 == null || y1 == null) return null;
  return y0 + ((y1 - y0) * (rpm - ax[i0])) / (ax[i1] - ax[i0]);
}

function floorCurrent(m: S1ThermalModel, rpm: number): number {
  const fl = m.grid.I_floor_A, ax = m.grid.rpm;
  if (!fl || !fl.length) return 0;
  if (rpm <= ax[0]) return fl[0];
  if (rpm >= ax[ax.length - 1]) return fl[fl.length - 1];
  const [i0, i1] = bracket(ax, rpm);
  return rpm === ax[i0] ? fl[i0] : Math.max(fl[i0], fl[i1]);
}

const fluxFactor = (m: S1ThermalModel, tM: number) =>
  Math.max(1 + m.flux_temp_coeff_per_K * (tM - m.magnet_ref_temp_C), 0.05);

function emTorque(m: S1ThermalModel, rpm: number, I: number, tM: number): number | null {
  const t = torqueGridAt(m, rpm, I);
  return t == null ? null : m.torque_factor * t * fluxFactor(m, tM);
}

/** propeller_s1.current_for_torque: the SMALLEST current that makes `need` (null = none up to the cap) */
export function s1CurrentForTorque(m: S1ThermalModel, rpm: number, need: number, tM: number): number | null {
  const cap = m.I_cap_A;
  const fl = floorCurrent(m, rpm);
  if (need <= 0 && fl <= 0) return 0;
  if (fl >= cap) return null;
  const pts = [fl, ...m.grid.I_A.filter((i) => fl < i && i < cap), cap];
  const vals = [fl > 0 ? emTorque(m, rpm, fl, tM) : 0, ...pts.slice(1).map((i) => emTorque(m, rpm, i, tM))];
  if (fl > 0 && vals[0] != null && need <= (vals[0] as number)) return fl;
  for (let k = 1; k < pts.length; k++) {
    const a = vals[k - 1], b = vals[k];
    if (a == null || b == null || !(a < need && need <= b)) continue;
    let lo = pts[k - 1], hi = pts[k];
    for (let n = 0; n < 60; n++) {
      const mid = 0.5 * (lo + hi);
      const t = emTorque(m, rpm, mid, tM);
      if (t == null || t < need) lo = mid; else hi = mid;
    }
    return 0.5 * (lo + hi);
  }
  return null;
}

export interface S1LoadPoint extends S1ThermalPoint { current_A: number; torque_em_Nm: number }

/** propeller_s1.load_point: the S1 fixed point at one speed for a known load torque - the current from the torque map
 *  at the magnets' own temperature, the losses, the calibrated network.  null = no current makes it / no loss data /
 *  no steady state (the page then refuses the point). */
export function s1LoadPoint(m: S1ThermalModel, rpm: number, qLoad: number, h: number, ambientC: number,
                            driveId: string | null): S1LoadPoint | null {
  if (!m.grid?.T_Nm?.length || !(m.I_cap_A > 0)) return null;      // a model without its load side: not used
  const w = (2 * Math.PI * rpm) / 60;
  const qEm = qLoad + (w > 0 ? mechW(m, rpm) / w : 0);
  const solve = (damping0: number, maxIt: number): { state: string; I?: number } => {
    let tW = ambientC, tM = ambientC, damping = damping0, lastRes = Infinity;
    for (let it = 1; it <= maxIt; it++) {
      const I = s1CurrentForTorque(m, rpm, qEm, tM);
      if (I == null || I > m.I_cap_A + 1e-9) return { state: 'no_current' };
      const ls = s1Losses(m, rpm, I, tW, driveId);
      if (!ls) return { state: 'loss_data' };
      const th = networkTemperatures(m.thermal_geom, ls.cu_dc + ls.cu_ac, ls.fe_rotor + ls.rotor_eddy, ls.motor_total,
        h, ambientC, m.calibration);
      const res = Math.max(Math.abs(th.winding - tW), Math.abs(th.magnet - tM));
      if (res > lastRes * 0.999 && it > 3) damping = Math.max(0.05, damping * 0.5);
      lastRes = res;
      tW += damping * (th.winding - tW);
      tM += damping * (th.magnet - tM);
      if (tW > RUNAWAY_C || tM > RUNAWAY_C) return { state: 'runaway' };
      if (res < FIXED_POINT_TOL_K) return { state: 'converged', I };
    }
    return { state: 'nonconverged' };
  };
  let fp = solve(1, FIXED_POINT_MAX_IT);
  if (fp.state === 'nonconverged') { const f2 = solve(0.2, 3 * FIXED_POINT_MAX_IT); if (f2.state !== 'nonconverged') fp = f2; }
  if (fp.state !== 'converged' || fp.I == null) return null;
  const t = s1ThermalAt(m, rpm, fp.I, h, ambientC, driveId);
  return t ? { ...t, current_A: fp.I, torque_em_Nm: qEm } : null;
}
