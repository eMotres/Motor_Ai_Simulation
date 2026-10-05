// Analytical lumped-parameter thermal estimate for the Configurator (NO FEM).
//
// Steady state: ALL losses leave through the outer housing surface by convection;
// the winding and magnet hot-spots sit ABOVE the housing by conduction resistances.
//
//   T_housing = T_ambient + P_total / (h · A_surface)
//   T_winding = T_housing + P_cu  · (R_slot + R_liner + R_yoke)
//   T_magnet  = T_housing + P_mag · R_airgap
//
// This is an engineering ESTIMATE (effective lumped resistances) for fast what-if
// in the configurator — NOT a substitute for the FEM thermal solve in Simulation.
// It uses the same cooling inputs (h, ambient) as the Simulation cooling panel.

export interface ThermalGeom {
  statorOD_mm: number;      // housing outer diameter
  stackLength_mm: number;   // axial length
  numSlots: number;
  slotHeight_mm: number;
  slotWidth_mm: number;     // slot width (lateral copper→tooth path)
  insulation_mm: number;    // slot-liner thickness per side
  coreThickness_mm: number; // stator back-iron (yoke)
  airGap_mm: number;
  magnetOD_mm: number;      // rotor outer diameter
}
export interface ThermalLosses {
  P_cu_W: number; P_fe_W: number; P_mag_W: number;
  /** extra heat with no known location (the PWM carrier loss): it leaves through the housing like the
   *  rest but is not put on the winding or magnet hot-spot (owner 2026-10-05: Total loss includes it) */
  P_extra_W?: number;
}
export interface ThermalCooling { h_Wm2K: number; ambient_C: number; }

export interface ThermalEstimate {
  P_total_W: number;
  A_surface_m2: number;
  h_Wm2K: number;
  ambient_C: number;
  T_housing_C: number;
  T_winding_C: number;
  T_magnet_C: number;
  dT_conv_C: number;        // housing rise above ambient
  dT_winding_C: number;     // winding rise above housing
  dT_magnet_C: number;      // magnet rise above housing
}

// Effective lumped conductivities [W/m·K] (account for composites/anisotropy):
const K_IRON = 25;    // laminated steel, in-plane / radial
const K_SLOT = 1.4;   // impregnated copper bundle, cross-slot effective
const K_INS  = 0.2;   // insulation (Nomex / polyimide)
const K_GAP  = 0.06;  // air gap, rotation-enhanced effective conductivity

const mm = (x: number) => Math.max(0, x || 0) / 1000;

export function estimateThermal(g: ThermalGeom, l: ThermalLosses, c: ThermalCooling): ThermalEstimate {
  const P_cu = Math.max(0, l.P_cu_W || 0);
  const P_total = P_cu + Math.max(0, l.P_fe_W || 0) + Math.max(0, l.P_mag_W || 0) + Math.max(0, l.P_extra_W || 0);

  const D = mm(g.statorOD_mm) || 0.1;
  const L = mm(g.stackLength_mm) || 0.03;
  const h = Math.max(1, c.h_Wm2K || 7);
  const Tamb = c.ambient_C || 0;

  // Convective surface = lateral cylinder + two end faces.
  const A_cyl = Math.PI * D * L;
  const A_surface = A_cyl + 2 * Math.PI * (D / 2) ** 2;

  const dT_conv = P_total / (h * A_surface);
  const T_housing = Tamb + dT_conv;

  // Winding hot-spot: copper loss conducts LATERALLY (≈ half slot width) through the
  // impregnated bundle to the tooth-facing slot walls, then liner → yoke → housing.
  const A_slot = Math.max(1e-4, (g.numSlots || 12) * 2 * mm(g.slotHeight_mm) * L); // two tooth-facing walls / slot
  const R_slot = (mm(g.slotWidth_mm) * 0.5) / (K_SLOT * A_slot);
  const R_ins  = mm(g.insulation_mm) / (K_INS * A_slot);
  const R_yoke = mm(g.coreThickness_mm) / (K_IRON * Math.max(1e-4, A_cyl));
  const dT_winding = P_cu * (R_slot + R_ins + R_yoke);
  const T_winding = T_housing + dT_winding;

  // Magnet: rotor eddy loss crosses the air gap to the stator / housing.
  const A_gap = Math.max(1e-4, Math.PI * mm(g.magnetOD_mm) * L);
  const R_gap = mm(g.airGap_mm) / (K_GAP * A_gap);
  const dT_magnet = Math.max(0, l.P_mag_W || 0) * R_gap;
  const T_magnet = T_housing + dT_magnet;

  return {
    P_total_W: P_total, A_surface_m2: A_surface, h_Wm2K: h, ambient_C: Tamb,
    T_housing_C: T_housing, T_winding_C: T_winding, T_magnet_C: T_magnet,
    dT_conv_C: dT_conv, dT_winding_C: dT_winding, dT_magnet_C: dT_magnet,
  };
}

// ── STILL AIR (the robot-joint cooling, `robotics`) ───────────────────────────────────────
// A mirror of the backend's `cooling_models.outer_still` (Churchill–Chu on the housing diameter,
// film properties at ½(T_wall + T_air), PLUS radiation at the housing's emissivity to the air
// temperature, view factor 1) — no floor, the correlation is the still-air one itself.  The film
// depends on the wall temperature, so `estimateThermalStill` iterates it against the estimate.
const STEFAN_BOLTZMANN = 5.670374419e-8;
export const ROBOTICS_EMISSIVITY = 0.9;      // the robotics mode's default (thermal_duty_cycle)

/** h_conv + h_rad [W/m²K] of a horizontal cylinder (housing) in still air. */
export function outerStillH(tWall_C: number, tAir_C: number, d_m: number, emissivity = ROBOTICS_EMISSIVITY): { h: number; h_conv: number; h_rad: number } {
  const d = Math.max(d_m || 0, 1e-4);
  const tFilm = 0.5 * (tWall_C + tAir_C);
  const r = Math.max(tFilm + 273.15, 100) / 273.15;
  const k = 0.0242 * r ** 0.83, nu = 1.33e-5 * r ** 1.75, pr = 0.707;
  const beta = 1 / Math.max(tFilm + 273.15, 1);
  const dt = Math.abs(tWall_C - tAir_C);
  const ra = 9.81 * beta * dt * d ** 3 / Math.max(nu * (nu / pr), 1e-30);
  const nuD = (0.60 + 0.387 * Math.max(ra, 0) ** (1 / 6) / (1 + (0.559 / pr) ** (9 / 16)) ** (8 / 27)) ** 2;
  const h_conv = nuD * k / d;
  const tw = tWall_C + 273.15, te = tAir_C + 273.15;
  const e = Math.min(Math.max(emissivity, 0), 1);
  const h_rad = e * STEFAN_BOLTZMANN * (tw * tw + te * te) * (tw + te);
  return { h: h_conv + h_rad, h_conv, h_rad };
}

/** `estimateThermal` with the still-air + radiation film evaluated at the housing temperature it
 *  produces (fixed point, damped; converges in a few passes because h ∝ ~ΔT^0.15). */
export function estimateThermalStill(g: ThermalGeom, l: ThermalLosses, ambient_C: number, emissivity = ROBOTICS_EMISSIVITY): ThermalEstimate {
  const D = mm(g.statorOD_mm) || 0.1;
  let tW = ambient_C + 30;
  let est = estimateThermal(g, l, { h_Wm2K: outerStillH(tW, ambient_C, D, emissivity).h, ambient_C });
  for (let i = 0; i < 40; i++) {
    const next = 0.5 * tW + 0.5 * est.T_housing_C;
    if (Math.abs(next - tW) < 0.01) break;
    tW = next;
    est = estimateThermal(g, l, { h_Wm2K: outerStillH(tW, ambient_C, D, emissivity).h, ambient_C });
  }
  return est;
}
