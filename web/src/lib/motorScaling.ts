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
//
// v1 is LINEAR. Deferred (see docs/MULTI_USER_PLAN.md): non-linear copper AC /
// proximity loss vs wire thickness, and coil-to-magnet-distance loss effects.

export interface Passport {
  N0: number;          // base turns (num_wires_per_slot)
  L0_mm: number;       // base lamination length (motor_length)
  wireH0_mm: number;   // base wire thickness (wire_height); wire_width fixed
  I0_A: number;        // base phase current (rms)
  rpm0: number;        // base speed
  T0_Nm: number;       // base torque at (I0, rpm0)
  Vemf0_peak_V: number;// base back-EMF peak at (N0, L0, rpm0)   (terminal V = EMF + R·I)
  R0_ohm: number;      // base phase resistance
  endWindFrac: number; // 0..1 fraction of R0 that is end-winding (does NOT scale with L)
  Pfe0_W: number;      // base iron (core) loss
  Pmag0_W: number;     // base magnet eddy loss
  mass0_kg: number;    // base active mass
}

export interface Knobs {
  N: number;       // turns
  L_mm: number;    // lamination length
  wireH_mm: number;// wire thickness
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

/** Instant analytical scaling of a passport to a new (length, turns, wire, I, rpm). */
export function scaleMotor(p: Passport, k: Knobs): ScaledResult {
  const fN = p.N0 ? k.N / p.N0 : 1;
  const fL = p.L0_mm ? k.L_mm / p.L0_mm : 1;
  const fH = p.wireH0_mm ? k.wireH_mm / p.wireH0_mm : 1;
  const fI = p.I0_A ? k.I_A / p.I0_A : 1;
  const fRpm = p.rpm0 ? k.rpm / p.rpm0 : 1;

  // Torque ∝ N·L (magnet flux linkage), and ∝ I at the same load angle.
  const T = p.T0_Nm * fN * fL * fI;

  // Resistance: active copper ∝ L, end-winding ~const; ∝ N (more turns of same
  // wire); ∝ 1/area = 1/wire_height (wire_width fixed).
  const R = ((1 - p.endWindFrac) * fL + p.endWindFrac) * p.R0_ohm * fN / fH;

  // Back-EMF ∝ N·L·rpm ; terminal phase voltage ≈ EMF + R·I (peak).
  const Vemf = p.Vemf0_peak_V * fN * fL * fRpm;
  const Vphase = Vemf + R * k.I_A * Math.SQRT2;   // R·I as a peak addend

  const omega = (2 * Math.PI * k.rpm) / 60;
  const P_mech = T * omega;

  const P_cu = 3 * k.I_A * k.I_A * R;             // 3-phase I²R
  const P_fe = p.Pfe0_W * fL * Math.pow(fRpm, 1.5); // Bertotti ~ f^1.5 (B fixed)
  const P_mag = p.Pmag0_W * fL * fRpm * fRpm;       // eddy ~ f²
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
 *  (wire_height; wire_width fixed). Lets the tuner cap I to the wire. */
export function maxCurrent(p: Passport, k: Knobs): number {
  const fH = p.wireH0_mm ? k.wireH_mm / p.wireH0_mm : 1;
  return p.I0_A * fH;   // I_max ∝ conductor area ∝ wire_height
}
