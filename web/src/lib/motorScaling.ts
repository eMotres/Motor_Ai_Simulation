// Instant analytical "passport" scaling for the simple tuner (no FEM).
//
// A PASSPORT is one base FEM result of a reference motor + its inputs. Changing
// lamination length, turns, or wire thickness rescales torque / back-EMF /
// resistance / losses / efficiency INSTANTLY — the magnet field is fixed by the
// (unchanged) cross-section, so at first order these scale exactly:
//
//   length  L  : torque, EMF, iron, magnet loss, mass ∝ L ; R = R_active·L + R_end
//   turns   N  : torque, EMF ∝ N ; R ∝ N           (wire cross-section unchanged)
//   wire    h  : wire_width is FIXED, so area ∝ wire_height(h) ; R ∝ 1/h ; Imax ∝ h
//   connect nP : 4 coils/phase re-wired (4S→nP1, 2P·2S→nP2, 4P→nP4) — pure
//                electrical voltage↔current trade: series count = 4/nP, so
//                torque,EMF ∝ nP0/nP ; R ∝ (nP0/nP)² ; Imax ∝ nP/nP0
//   voltage    : terminal V = back-EMF + load drop.  The drop (mostly the
//                synchronous reactance X·I, X = ωL, L ∝ turns²·L_stack·series²)
//                is pinned at the loaded base point (Vload0−Vemf0) and rescaled,
//                so V matches FEM at base and scales physically off it.
//
// v1 is LINEAR. Deferred (see docs/MULTI_USER_PLAN.md): non-linear copper AC /
// proximity loss vs wire thickness, and coil-to-magnet-distance loss effects.

export interface Passport {
  N0: number;          // base turns (num_wires_per_slot)
  L0_mm: number;       // base lamination length (motor_length)
  wireH0_mm: number;   // base wire thickness (wire_height); wire_width fixed
  I0_A: number;        // base phase current (rms)
  rpm0: number;        // base speed
  nP0: number;         // base parallel paths (4S→1, 2P·2S→2, 4P→4); 4 coils/phase
  T0_Nm: number;       // base torque at (I0, rpm0)
  Vemf0_peak_V: number;// base back-EMF peak at (N0, L0, rpm0)   (no-load terminal V)
  Vload0_peak_V?: number; // base LOADED terminal-V peak at (I0, rpm0); enables the reactive-drop model
  R0_ohm: number;      // base phase resistance
  endWindFrac: number; // 0..1 fraction of R0 that is end-winding (does NOT scale with L)
  Pfe0_W: number;      // base iron (core) loss
  Pmag0_W: number;     // base magnet eddy loss
  mass0_kg: number;    // base active mass
  // FEM speed-sweep curves at the base operating point (I0, γ, L0): iron and
  // magnet+shaft eddy loss vs rpm.  When present, scaleMotor interpolates these
  // (× length factor) instead of the analytical f^1.5 / f² laws.  Assumed ~current-
  // independent (loss dominated by the magnet flux); a 2D I×rpm map is future work.
  speed?: { rpm: number[]; Pfe_W: number[]; Pmag_W: number[] };
}

export interface Knobs {
  N: number;       // turns
  L_mm: number;    // lamination length
  wireH_mm: number;// wire thickness
  nP: number;      // parallel paths (winding connection: 4S→1, 2P·2S→2, 4P→4)
  I_A: number;     // phase current (rms)
  rpm: number;     // speed
}

export interface ScaledResult {
  T_Nm: number;
  P_mech_W: number;
  Vphase_peak_V: number;
  R_ohm: number;
  P_cu_W: number;
  P_fe_W: number;
  P_mag_W: number;
  P_loss_W: number;
  efficiency: number;   // 0..1
  mass_kg: number;
  torque_per_mass: number;
}

/** Linear interpolation of ys at x over sorted xs; extrapolates beyond the ends
 *  using the end segment's slope (loss floored at 0). */
function interp(xs: number[], ys: number[], x: number): number {
  const n = xs.length;
  if (n === 0) return 0;
  if (n === 1) return ys[0];
  if (x <= xs[0]) {
    const s = (ys[1] - ys[0]) / (xs[1] - xs[0]);
    return Math.max(0, ys[0] + s * (x - xs[0]));
  }
  if (x >= xs[n - 1]) {
    const s = (ys[n - 1] - ys[n - 2]) / (xs[n - 1] - xs[n - 2]);
    return Math.max(0, ys[n - 1] + s * (x - xs[n - 1]));
  }
  let i = 1;
  while (i < n && xs[i] < x) i++;
  const t = (x - xs[i - 1]) / (xs[i] - xs[i - 1]);
  return ys[i - 1] + t * (ys[i] - ys[i - 1]);
}

/** Instant analytical scaling of a passport to a new (length, turns, wire, I, rpm). */
export function scaleMotor(p: Passport, k: Knobs): ScaledResult {
  const fN = p.N0 ? k.N / p.N0 : 1;
  const fL = p.L0_mm ? k.L_mm / p.L0_mm : 1;
  const fH = p.wireH0_mm ? k.wireH_mm / p.wireH0_mm : 1;
  const fI = p.I0_A ? k.I_A / p.I0_A : 1;
  const fRpm = p.rpm0 ? k.rpm / p.rpm0 : 1;
  // Winding connection: 4 coils/phase re-wired. Series count = 4/nP, so torque &
  // back-EMF ∝ nP0/nP and resistance ∝ (nP0/nP)². Pure electrical, no field change.
  const fConn = p.nP0 && k.nP ? p.nP0 / k.nP : 1;

  // Torque ∝ N·L (magnet flux linkage), ∝ I at the same load angle, ∝ series count.
  const T = p.T0_Nm * fN * fL * fI * fConn;

  // Resistance: active copper ∝ L, end-winding ~const; ∝ N (more turns of same
  // wire); ∝ 1/area = 1/wire_height (wire_width fixed); ∝ (nP0/nP)² (connection).
  const R = ((1 - p.endWindFrac) * fL + p.endWindFrac) * p.R0_ohm * fN / fH * fConn * fConn;

  // Back-EMF (no-load) ∝ N·L·rpm·(series count).
  const Vemf = p.Vemf0_peak_V * fN * fL * fRpm * fConn;
  // Loaded terminal voltage = EMF + load drop.  The drop is dominated by the
  // synchronous reactance X·I (X = ωL, L ∝ turns²·L_stack·series²); its value at
  // the loaded base point, (Vload0 − Vemf0), is rescaled by fI·fRpm·fN²·fL·fConn².
  // Falls back to the resistive drop R·I when no loaded base voltage is supplied.
  const Vdrop0 = p.Vload0_peak_V && p.Vload0_peak_V > p.Vemf0_peak_V
    ? p.Vload0_peak_V - p.Vemf0_peak_V : 0;
  const Vdrop = Vdrop0 > 0
    ? Vdrop0 * fI * fRpm * fN * fN * fL * fConn * fConn
    : R * k.I_A * Math.SQRT2;
  const Vphase = Vemf + Vdrop;

  const omega = (2 * Math.PI * k.rpm) / 60;
  const P_mech = T * omega;

  const P_cu = 3 * k.I_A * k.I_A * R;             // 3-phase I²R
  // Iron + magnet loss: FEM speed-curve (interpolated over rpm, × length factor)
  // when the passport carries one, else the analytical f^1.5 / f² laws.
  const sp = p.speed;
  const hasCurve = !!(sp && sp.rpm.length >= 2);
  const P_fe = hasCurve ? interp(sp!.rpm, sp!.Pfe_W, k.rpm) * fL : p.Pfe0_W * fL * Math.pow(fRpm, 1.5);
  const P_mag = hasCurve ? interp(sp!.rpm, sp!.Pmag_W, k.rpm) * fL : p.Pmag0_W * fL * fRpm * fRpm;
  const P_loss = P_cu + P_fe + P_mag;

  const eff = P_mech > 0 ? P_mech / (P_mech + P_loss) : 0;
  const mass = p.mass0_kg * fL;

  return {
    T_Nm: T, P_mech_W: P_mech, Vphase_peak_V: Vphase, R_ohm: R,
    P_cu_W: P_cu, P_fe_W: P_fe, P_mag_W: P_mag, P_loss_W: P_loss,
    efficiency: eff, mass_kg: mass,
    torque_per_mass: mass > 0 ? T / mass : 0,
  };
}

/** Largest phase current the winding can carry, scaled by conductor area
 *  (wire_height; wire_width fixed) and parallel paths (more paths share the
 *  phase current, so the phase can carry more). Lets the tuner cap I to the wire. */
export function maxCurrent(p: Passport, k: Knobs): number {
  const fH = p.wireH0_mm ? k.wireH_mm / p.wireH0_mm : 1;
  const fConnI = p.nP0 && k.nP ? k.nP / p.nP0 : 1;
  return p.I0_A * fH * fConnI;   // I_max ∝ conductor area (wire_height) × parallel paths
}
