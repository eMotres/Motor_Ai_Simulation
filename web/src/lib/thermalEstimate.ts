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
  /** extra motor heat leaving through the housing. */
  P_extra_W?: number;
  /** portion of P_extra_W attributed to the stator/winding hot spot. */
  P_extra_stator_W?: number;
}
export interface ThermalCooling { h_Wm2K: number; ambient_C: number; }

/** Configure's motor heat attribution. PWM carrier loss is motor heat assigned to the stator/winding;
 * controller losses are outside this object. Null means the operating point or a required motor loss is unknown. */
export function configureMotorHeat(
  mode: 'sine' | 'pwm', pointAccepted: boolean,
  base: { P_cu_W: number; P_fe_W: number; P_mag_W: number; stator_W: number; rotor_W: number } | null,
  extraMotorLoss_W: number | null,
): { stator_W: number; rotor_W: number; totalMotor_W: number; thermalLosses: ThermalLosses } | null {
  if (!pointAccepted || !base || Object.values(base).some((v) => !Number.isFinite(v) || v < 0)
    || (mode === 'pwm' && (extraMotorLoss_W == null || !Number.isFinite(extraMotorLoss_W) || extraMotorLoss_W < 0))) return null;
  const extra = mode === 'sine' ? 0 : extraMotorLoss_W as number;
  const stator_W = base.stator_W + extra;
  const rotor_W = base.rotor_W;
  return { stator_W, rotor_W, totalMotor_W: stator_W + rotor_W,
    thermalLosses: { P_cu_W: base.P_cu_W, P_fe_W: base.P_fe_W, P_mag_W: base.P_mag_W,
      P_extra_W: extra, P_extra_stator_W: extra } };
}

/** ONE operating point's temperatures and verdict, exactly as the Configure tiles and the red
 *  "overheats" line judge it: the motor heat of the drive in use (`configureMotorHeat`: PWM extra
 *  loss in the stator, controller loss NOT motor heat) through `estimateThermal`, judged on the
 *  winding and the magnet.  `heat` / `thermal` are null when the point or its cooling is unknown
 *  (then nothing is shown and nothing overheats).  The rated-speed cap evaluates this same function
 *  at other speeds, so the two can never disagree. */
export function configurePointThermal(
  mode: 'sine' | 'pwm', pointAccepted: boolean,
  base: { P_cu_W: number; P_fe_W: number; P_mag_W: number; stator_W: number; rotor_W: number } | null,
  extraMotorLoss_W: number | null,
  cooling: ThermalCooling | null, geom: ThermalGeom,
  limits: { winding_C: number; magnet_C: number },
): { heat: ReturnType<typeof configureMotorHeat>; thermal: ThermalEstimate | null; over: boolean;
     windingOver: boolean; magnetOver: boolean } {
  const heat = configureMotorHeat(mode, pointAccepted, base, extraMotorLoss_W);
  const thermal = heat && cooling ? estimateThermal(geom, heat.thermalLosses, cooling) : null;
  const windingOver = !!thermal && thermal.T_winding_C > limits.winding_C;
  const magnetOver = !!thermal && thermal.T_magnet_C > limits.magnet_C;
  return { heat, thermal, over: windingOver || magnetOver, windingOver, magnetOver };
}

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
  const P_stator_winding = P_cu + Math.max(0, l.P_extra_stator_W || 0);
  const dT_winding = P_stator_winding * (R_slot + R_ins + R_yoke);
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
