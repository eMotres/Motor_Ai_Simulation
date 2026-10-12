// Configure: the continuous (S1) limit with the chosen propeller (owner 2026-10-11).
//
// The number is the BACKEND's (`GET /api/propellers/{id}/s1`, motor_ai_sim.propeller_s1): the
// highest speed at which the steady winding and magnet temperatures stay inside their limits with
// that propeller as both the load and the only cooling.  This file only reads the answer and says
// what to print.  Pure functions: no fetch, no React.

export type S1Binding = 'winding' | 'magnet' | 'current' | 'voltage' | 'speed_range' | 'speed' | 'runaway'
  | 'controller' | 'battery_current' | 'demag' | 'loss_data' | 'nonconverged';

/** Why a scan that still passes stopped (the limit is then a LOWER bound). */
export type S1RangeEnd = 'propeller_data' | 'motor_speed_grid' | 'requested';

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
  /** false: the limit's slipstream lies outside the calibration anchors' range; null/absent: no range stated */
  calibration_in_range?: boolean | null;
  /** the scan ended still passing: `rpm` is a lower bound, `range_end` says why it stopped */
  lower_bound?: boolean;
  range_end?: S1RangeEnd;
  /** the backend's qualification codes (what the number rests on, omits, cannot check) */
  flags?: string[];
  drive_feasibility?: 'checked' | 'unknown' | 'failed';
  controller_limit_A?: number | null;
  /** an external ESC's continuous BUS current (checked against the battery current at the pack minimum) */
  controller_bus_limit_A?: number | null;
  battery_current_A?: number | null;
  cooling?: { slipstream_factor: number | null; slipstream_position: string | null;
              /** caller | model (bench-calibrated / transferred) | default (assumed 0.4) */
              slipstream_source?: string | null };
}

/** The board's cooling class (the controller board, not the motor): `slipstream` is the board in
 *  the propeller wash, evaluated at the operating point's wash speed. */
export type S1BoardCooling = 'favourable' | 'weak' | 'hot_motor' | 'slipstream';

/** The board build.  The two-device builds are the model's ESTIMATE drives (`_x2` the same board,
 *  `_x2_board2` the board scaled ×2) and are offered only where the S1 model carries them. */
export type S1BoardBuild = 'as_built' | 'x2' | 'x2_board2';

export const S1_BOARD_COOLING: S1BoardCooling[] = ['favourable', 'weak', 'hot_motor', 'slipstream'];
export const S1_BOARD_BUILDS: S1BoardBuild[] = ['as_built', 'x2', 'x2_board2'];

export function isBoardCooling(v: string): v is S1BoardCooling {
  return (S1_BOARD_COOLING as string[]).includes(v);
}

export function isBoardBuild(v: string): v is S1BoardBuild {
  return (S1_BOARD_BUILDS as string[]).includes(v);
}

/** The model drive id of one board build (the estimate builds are the variant id + a suffix). */
export function s1BuildDriveId(baseId: string, build: S1BoardBuild): string {
  return build === 'x2' ? `${baseId}_x2` : build === 'x2_board2' ? `${baseId}_x2_board2` : baseId;
}

/** The builds this S1 model offers for the Drive block's variant: `as_built` always (the variant
 *  itself), the two-device ones only when the model carries their drive ids.  Nothing learned yet
 *  (`available` null): as built only — the first request is always the unchanged one. */
export function s1BoardBuilds(available: readonly string[] | null | undefined, baseId: string): S1BoardBuild[] {
  const out: S1BoardBuild[] = ['as_built'];
  if (!available) return out;
  for (const b of S1_BOARD_BUILDS) {
    if (b !== 'as_built' && available.includes(s1BuildDriveId(baseId, b))) out.push(b);
  }
  return out;
}

/** The build a response's drive id stands for. */
export function s1BoardBuildOfId(did: string | null | undefined): S1BoardBuild {
  const s = String(did ?? '');
  return s.endsWith('_x2_board2') ? 'x2_board2' : s.endsWith('_x2') ? 'x2' : 'as_built';
}

/** The board cooling classes the S1 response says this model / drive runs (`drive.
 *  board_cooling_classes`) — the select shows only these.  Nothing learned yet (null /
 *  undefined, e.g. before the first answer): all four.  An explicitly EMPTY list means the
 *  model supports no class of its own: only `favourable`, which never gets refused. */
export function s1BoardCoolingOptions(available: readonly string[] | null | undefined): S1BoardCooling[] {
  if (available == null) return [...S1_BOARD_COOLING];
  if (!available.length) return ['favourable'];
  return S1_BOARD_COOLING.filter((c) => available.includes(c));
}

/** The remembered board cooling clamped to what the model supports: the remembered choice when
 *  it is available there, else `favourable` (the backend default), else the first supported one. */
export function s1BoardCoolingChoice(wanted: string | null | undefined,
                                     available: readonly string[] | null | undefined): S1BoardCooling {
  const opts = s1BoardCoolingOptions(available);
  if (wanted && isBoardCooling(wanted) && opts.includes(wanted)) return wanted;
  return opts.includes('favourable') ? 'favourable' : opts[0];
}

/** The locale key of a board cooling class (always one of the four the backend accepts). */
export function s1BoardCoolingKey(bc: string | null | undefined): string {
  switch (bc) {
    case 'weak': return 'configurePropeller.s1CoolWeak';
    case 'hot_motor': return 'configurePropeller.s1CoolHotMotor';
    case 'slipstream': return 'configurePropeller.s1CoolSlipstream';
    default: return 'configurePropeller.s1CoolFavourable';
  }
}

/** The locale key of a board build. */
export function s1BoardBuildKey(b: S1BoardBuild): string {
  switch (b) {
    case 'x2': return 'configurePropeller.s1BuildX2';
    case 'x2_board2': return 'configurePropeller.s1BuildX2Board2';
    default: return 'configurePropeller.s1BuildX1';
  }
}

/** The ESC the configuration ran on the bench (the model's default for the external-ESC drive). */
export interface S1ExternalEsc {
  name?: string | null;
  i_bus_cont_A?: number | null;
  i_phase_cont_A?: number | null;
  basis?: string | null;
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
  /** the drive the limit was computed for (Configure's selected one) */
  drive?: { id: string; kind: 'sine' | 'pwm' | 'external'; board_cooling: string;
            /** the board cooling classes this model / drive supports (`slipstream` only where the
             *  model carries a board block) — the Board cooling select offers only these */
            board_cooling_classes?: string[];
            I_cont_A: number | null; I_bus_cont_A?: number | null; name?: string | null };
  /** every drive id this S1 model offers (`sine`, the computed PWM variants, `external_esc`) */
  drives_available?: string[];
  /** the model's bench ESC (null: the model carries none, the user must give the current) */
  external_esc_default?: S1ExternalEsc | null;
  /** the motor model: a thermal calibration is present when the lumped network was fitted to FEM */
  model?: { thermal_calibration?: Record<string, unknown> | null };
}

const G0 = 9.80665;

/** Which controller the S1 line is computed for: the Drive block's (sine / computed PWM variant) or
 *  an EXTERNAL ESC (no board model; its continuous bus current is typed, else the model's bench ESC). */
export type S1Controller = 'drive' | 'external';

export const EXTERNAL_ESC = 'external_esc';

/** The `drive`, the ESC bus current and the board cooling of the S1 request.  A blank / non-positive
 *  typed current sends none, so the backend takes the model's bench ESC (or reports the limit as
 *  unknown).  Our drive carries the board build in the drive id (the model's `_x2` / `_x2_board2`
 *  estimate variants); an external ESC has no board, so no board cooling is sent for it. */
export function s1DriveRequest(ctrl: S1Controller, driveBlockId: string, escBusTxt: string | null | undefined,
                               boardCooling: S1BoardCooling = 'favourable', build: S1BoardBuild = 'as_built'
): { drive: string; escBusA: number | null; boardCooling: S1BoardCooling | null } {
  if (ctrl !== 'external') return { drive: s1BuildDriveId(driveBlockId, build), escBusA: null, boardCooling };
  const v = parseFloat(String(escBusTxt ?? '').replace(',', '.'));
  return { drive: EXTERNAL_ESC, escBusA: Number.isFinite(v) && v > 0 ? v : null, boardCooling: null };
}

/** The cache key of one S1 request: every parameter that changes the answer (the board build is
 *  already inside `drive` as the `_x2` / `_x2_board2` suffix). */
export function s1CacheKey(id: string, die: string, config: string, ambientC: number, drive: string,
                           pack: { min: number; max: number } | null, escBusA: number | null,
                           boardCooling: string | null): string {
  return `${id}|${die}|${config}|${ambientC.toFixed(1)}|${drive}|${pack ? `${pack.min.toFixed(2)}-${pack.max.toFixed(2)}` : ''}|${escBusA ?? ''}|${boardCooling ?? ''}`;
}

/** The locale key of what stops the speed. */
export function s1BindingKey(b: S1Binding | null | undefined): string {
  switch (b) {
    case 'winding': return 'configurePropeller.s1BindWinding';
    case 'magnet': return 'configurePropeller.s1BindMagnet';
    case 'current': return 'configurePropeller.s1BindCurrent';
    case 'voltage': case 'speed': return 'configurePropeller.s1BindVoltage';
    case 'speed_range': return 'configurePropeller.s1BindGrid';
    case 'runaway': return 'configurePropeller.s1BindRunaway';
    case 'controller': return 'configurePropeller.s1BindController';
    case 'battery_current': return 'configurePropeller.s1BindBattery';
    case 'demag': return 'configurePropeller.s1BindDemag';
    case 'loss_data': return 'configurePropeller.s1BindLossData';
    case 'nonconverged': return 'configurePropeller.s1BindNonconverged';
    default: return 'configurePropeller.s1BindUnknown';
  }
}

/** The locale key of why a still-passing scan stopped. */
export function s1RangeEndKey(e: S1RangeEnd | null | undefined): string {
  switch (e) {
    case 'propeller_data': return 'configurePropeller.s1EndProp';
    case 'requested': return 'configurePropeller.s1EndRequested';
    default: return 'configurePropeller.s1EndGrid';
  }
}

/** The backend's qualification code -> its tooltip sentence (unknown codes are skipped, not guessed). */
const FLAG_KEYS: Record<string, string> = {
  thermal_uncalibrated: 'configurePropeller.s1FlagThermalUncalibrated',
  thermal_extrapolated: 'configurePropeller.s1FlagCalibratedOutside',
  slipstream_uncalibrated: 'configurePropeller.s1FlagSlipstream',
  slipstream_bench: 'configurePropeller.s1FlagSlipstreamBench',
  slipstream_transferred: 'configurePropeller.s1FlagSlipstreamTransferred',
  esc_external: 'configurePropeller.s1FlagEscExternal',
  esc_loss_omitted: 'configurePropeller.s1FlagEscLoss',
  prop_extrapolated: 'configurePropeller.s1FlagExtrapolated',
  prop_power_estimated: 'configurePropeller.s1FlagEstimated',
  below_loss_grid_speed: 'configurePropeller.s1FlagBelowGrid',
  below_loss_grid_current: 'configurePropeller.s1FlagBelowGridCurrent',
  fw_floor: 'configurePropeller.s1FlagFwFloor',
  pwm_omitted: 'configurePropeller.s1FlagPwmOmitted',
  pwm_unavailable: 'configurePropeller.s1FlagPwmUnavailable',
  mech_omitted: 'configurePropeller.s1FlagMechOmitted',
  voltage_deeper_fw: 'configurePropeller.s1FlagVoltageFw',
  voltage_unverified: 'configurePropeller.s1FlagVoltageUnverified',
  demag_reference_only: 'configurePropeller.s1FlagDemagRef',
  demag_unknown: 'configurePropeller.s1FlagDemagUnknown',
  drive_failed: 'configurePropeller.s1FlagDriveFailed',
  drive_unknown: 'configurePropeller.s1FlagDriveUnknown',
  battery_current_unknown: 'configurePropeller.s1FlagBatteryUnknown',
};

export type S1Line =
  | { kind: 'limit'; rpm: number; thrust_kgf: number; shaft_W: number; electrical_W: number | null;
      bindingKey: string; flagKeys: string[]; limit: S1Limit;
      /** the scan ended still passing: print ≥ rpm and the reason, never "up to" */
      lowerBound: boolean; rangeEndKey: string | null }
  | { kind: 'none'; bindingKey: string };

/** What the one Configure line says, or null when there is nothing to say (no answer yet, or an
 *  answer for another propeller / configuration than the one on screen — never a stale number). */
export function s1Line(r: S1Result | null | undefined, propId: string | null, config: string | null): S1Line | null {
  if (!r || !propId || r.propeller_id !== propId || (config != null && r.config !== config)) return null;
  if (!r.limit) return { kind: 'none', bindingKey: s1BindingKey(r.first_failure) };
  const L = r.limit;
  const flagKeys: string[] = [];
  if (Array.isArray(L.flags)) {
    // the backend names every qualification itself (schema-2 models)
    if (r.model?.thermal_calibration && !L.flags.some((f) => f.startsWith('thermal_'))) {
      flagKeys.push('configurePropeller.s1FlagCalibrated');
    }
    for (const f of L.flags) if (FLAG_KEYS[f]) flagKeys.push(FLAG_KEYS[f]);
  } else {
    if (L.extrapolated) flagKeys.push('configurePropeller.s1FlagExtrapolated');
    if (L.power_estimated) flagKeys.push('configurePropeller.s1FlagEstimated');
    if (L.below_loss_grid) flagKeys.push('configurePropeller.s1FlagBelowGrid');
    if (L.fw_floor) flagKeys.push('configurePropeller.s1FlagFwFloor');
    if (r.model?.thermal_calibration) {
      flagKeys.push(L.calibration_in_range === false
        ? 'configurePropeller.s1FlagCalibratedOutside' : 'configurePropeller.s1FlagCalibrated');
    }
  }
  if (!r.monotone) flagKeys.push('configurePropeller.s1FlagBands');
  const lowerBound = L.lower_bound === true || L.binding === 'speed_range';
  return { kind: 'limit', rpm: L.rpm, thrust_kgf: L.thrust_N / G0, shaft_W: L.shaft_power_W,
           electrical_W: L.electrical_power_W, bindingKey: s1BindingKey(L.binding), flagKeys, limit: L,
           lowerBound, rangeEndKey: lowerBound ? s1RangeEndKey(L.range_end) : null };
}
