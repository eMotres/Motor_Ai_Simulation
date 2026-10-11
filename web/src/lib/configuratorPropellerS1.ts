// Configure: the continuous (S1) limit with the chosen propeller (owner 2026-10-11).
//
// The number is the BACKEND's (`GET /api/propellers/{id}/s1`, motor_ai_sim.propeller_s1): the
// highest speed at which the steady winding and magnet temperatures stay inside their limits with
// that propeller as both the load and the only cooling.  This file only reads the answer and says
// what to print.  Pure functions: no fetch, no React.

export type S1Binding = 'winding' | 'magnet' | 'current' | 'voltage' | 'speed_range' | 'speed' | 'runaway';

export interface S1Limit {
  rpm: number;
  thrust_N: number;
  shaft_power_W: number;
  electrical_power_W: number | null;
  battery_power_W: number | null;
  current_A: number | null;
  T_winding_C: number | null;
  T_magnet_C: number | null;
  T_housing_C: number | null;
  air_speed_ms: number;
  binding: S1Binding;
  extrapolated: boolean;
  power_estimated: boolean;
  below_loss_grid: boolean;
  /** above the no-load voltage limit: the drive carries d-axis current at this speed */
  fw_floor?: boolean;
}

export interface S1Result {
  propeller_id: string;
  die: string;
  config: string;
  ambient_c: number;
  limit: S1Limit | null;
  no_continuous_point: boolean;
  first_failure: S1Binding | null;
  monotone: boolean;
  limits: { winding_C: number; magnet_C: number; winding_basis: string; magnet_basis: string };
  /** the motor model: a thermal calibration is present when the lumped network was fitted to FEM */
  model?: { thermal_calibration?: Record<string, unknown> | null };
}

const G0 = 9.80665;

/** The locale key of what stops the speed. */
export function s1BindingKey(b: S1Binding | null | undefined): string {
  switch (b) {
    case 'winding': return 'configurePropeller.s1BindWinding';
    case 'magnet': return 'configurePropeller.s1BindMagnet';
    case 'current': return 'configurePropeller.s1BindCurrent';
    case 'voltage': case 'speed': return 'configurePropeller.s1BindVoltage';
    case 'speed_range': return 'configurePropeller.s1BindGrid';
    case 'runaway': return 'configurePropeller.s1BindRunaway';
    default: return 'configurePropeller.s1BindUnknown';
  }
}

export type S1Line =
  | { kind: 'limit'; rpm: number; thrust_kgf: number; shaft_W: number; electrical_W: number | null;
      bindingKey: string; flagKeys: string[]; limit: S1Limit }
  | { kind: 'none'; bindingKey: string };

/** What the one Configure line says, or null when there is nothing to say (no answer yet, or an
 *  answer for another propeller / configuration than the one on screen — never a stale number). */
export function s1Line(r: S1Result | null | undefined, propId: string | null, config: string | null): S1Line | null {
  if (!r || !propId || r.propeller_id !== propId || (config != null && r.config !== config)) return null;
  if (!r.limit) return { kind: 'none', bindingKey: s1BindingKey(r.first_failure) };
  const L = r.limit;
  const flagKeys: string[] = [];
  if (L.extrapolated) flagKeys.push('configurePropeller.s1FlagExtrapolated');
  if (L.power_estimated) flagKeys.push('configurePropeller.s1FlagEstimated');
  if (L.below_loss_grid) flagKeys.push('configurePropeller.s1FlagBelowGrid');
  if (L.fw_floor) flagKeys.push('configurePropeller.s1FlagFwFloor');
  if (r.model?.thermal_calibration) flagKeys.push('configurePropeller.s1FlagCalibrated');
  if (!r.monotone) flagKeys.push('configurePropeller.s1FlagBands');
  return { kind: 'limit', rpm: L.rpm, thrust_kgf: L.thrust_N / G0, shaft_W: L.shaft_power_W,
           electrical_W: L.electrical_power_W, bindingKey: s1BindingKey(L.binding), flagKeys, limit: L };
}
