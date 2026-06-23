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
export interface ThermalLosses { P_cu_W: number; P_fe_W: number; P_mag_W: number; }
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
const K_INS  = 0.2;   // slot liner (Nomex / polyimide)
const K_GAP  = 0.06;  // air gap, rotation-enhanced effective conductivity

const mm = (x: number) => Math.max(0, x || 0) / 1000;

export function estimateThermal(g: ThermalGeom, l: ThermalLosses, c: ThermalCooling): ThermalEstimate {
  const P_cu = Math.max(0, l.P_cu_W || 0);
  const P_total = P_cu + Math.max(0, l.P_fe_W || 0) + Math.max(0, l.P_mag_W || 0);

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
