// The results tile block must be IDENTICAL in Sine and PWM (owner 2026-10-05: «поле значений
// одинаковым для sin и pwm, а то всё дёргается при переключении»).
//
// Every drive-dependent tile exists in BOTH modes, in the same place; only its VALUE changes:
//   * Sine: the extra PWM loss is a real 0 W; the inverter / junction / drive-efficiency tiles
//     are "—" (there is no inverter model in a sine drive).
//   * PWM refused (outside the envelope, device limit, bus range ...): the tiles are "—".
//   * PWM: the numbers read from the computed variant.
// A refusal never inserts a block — it replaces values inside the tiles.
import type { VariantReading } from './configuratorDrive';

export type DriveMode = 'sine' | 'pwm';

/** Why a tile is "—" (picks the tooltip). */
export type Blank = 'sine' | 'refused' | 'missing' | null;

export interface DriveTileSpec {
  id: string;
  labelKey: string;
  tipKey: string;
  unit: string;
  d: number;
  /** value as the tile shows it; null = "—" */
  value: number | null;
  blank: Blank;
  goodHi?: boolean;
  /** the junction tile is coloured by the device limit */
  level?: 'ok' | 'warn';
}

/** The rest of the controller group (after PWM loss and controller loss): the same tiles, in the same
 *  order, in every mode.  `motorEff` is the motor's own shaft efficiency, for reference; the system
 *  (motor + controller) efficiency is the main EFFICIENCY tile (see `systemEfficiency`). */
export const DRIVE_ROW_IDS = ['tj', 'motorEff', 'pContMax'] as const;

/** The loss row's last two tiles (after loss density): the motor's PWM extra loss, then the controller's. */
export const EXTRA_LOSS_ID = 'motorLoss';
export const CONTROLLER_LOSS_ID = 'invLoss';
export const LOSS_TAIL_IDS = [EXTRA_LOSS_ID, CONTROLLER_LOSS_ID] as const;

const pick = (mode: DriveMode, drv: VariantReading | null, f: (r: VariantReading) => number | null):
  { value: number | null; blank: Blank } => {
  if (mode === 'sine') return { value: null, blank: 'sine' };
  if (!drv) return { value: null, blank: 'refused' };
  const v = f(drv);
  return v == null ? { value: null, blank: 'missing' } : { value: v, blank: null };
};

/** `drv` is the reading to show (null whenever PWM is refused or the drive is Sine).
 *  `motorEff` = the motor's own efficiency [%] from `motorEfficiency()` (null = unknown / refused). */
export function driveRowTiles(mode: DriveMode, drv: VariantReading | null, tjLimit: number | null,
                              motorEff: number | null = null): DriveTileSpec[] {
  const t = (id: string, labelKey: string, tipKey: string, unit: string, d: number,
    f: (r: VariantReading) => number | null, goodHi?: boolean): DriveTileSpec =>
    ({ id, labelKey, tipKey, unit, d, goodHi, ...pick(mode, drv, f) });
  const tiles = [
    t('tj', 'configureDrive.tj', 'configureDrive.tjTip', '°C', 0, (r) => r.tj_C),
    { id: 'motorEff', labelKey: 'configureDrive.motorEff', tipKey: 'configureDrive.motorEffTip', unit: '%', d: 1, goodHi: true,
      ...(motorEff != null && Number.isFinite(motorEff) ? { value: motorEff, blank: null as Blank }
        : { value: null, blank: (mode === 'sine' ? 'sine' : drv ? 'missing' : 'refused') as Blank }) },
    t('pContMax', 'configureDrive.pContMax', 'configureDrive.pContMaxTip', 'kW', 2,
      (r) => (r.p_cont_max_W == null ? null : r.p_cont_max_W / 1000), true),
  ];
  const tj = tiles.find((x) => x.id === 'tj')!;
  if (tj.value != null && tjLimit != null) tj.level = tj.value > tjLimit - 25 ? 'warn' : 'ok';
  return tiles;
}

/** The extra motor loss the carrier adds over the sine point: 0 in Sine (a real zero),
 *  "—" when PWM is refused or the variant carries no value. */
export function extraLossTile(mode: DriveMode, drv: VariantReading | null): DriveTileSpec {
  const base = { id: EXTRA_LOSS_ID, labelKey: 'configureDrive.motorLoss', tipKey: 'configureDrive.motorLossTip',
    unit: 'W', d: 0, goodHi: false };
  if (mode === 'sine') return { ...base, value: 0, blank: null };
  if (!drv) return { ...base, value: null, blank: 'refused' };
  return drv.motor_pwm_loss_W == null
    ? { ...base, value: null, blank: 'missing' }
    : { ...base, value: drv.motor_pwm_loss_W, blank: null };
}

/** The controller's own loss: conduction + switching + dead time of the bridge + the copper of the coil
 *  links on its board.  0 W in Sine (a real zero: there is no inverter model), the computed value in PWM,
 *  "—" when PWM is refused or the point lacks a part (never a sum that leaves one out). */
export const controllerLossW = (drv: VariantReading): number | null =>
  drv.inv_total_W == null || drv.board_copper_W == null ? null : drv.inv_total_W + drv.board_copper_W;

export function controllerLossTile(mode: DriveMode, drv: VariantReading | null): DriveTileSpec {
  const base = { id: CONTROLLER_LOSS_ID, labelKey: 'configureDrive.invLoss', tipKey: 'configureDrive.invLossTip',
    unit: 'W', d: 1, goodHi: false };
  if (mode === 'sine') return { ...base, value: 0, blank: null };
  if (!drv) return { ...base, value: null, blank: 'refused' };
  const w = controllerLossW(drv);
  return w == null ? { ...base, value: null, blank: 'missing' } : { ...base, value: w, blank: null };
}

/** The two loss-row tiles that depend on the drive, in their fixed order. */
export const lossTailTiles = (mode: DriveMode, drv: VariantReading | null): DriveTileSpec[] =>
  [extraLossTile(mode, drv), controllerLossTile(mode, drv)];

/** TOTAL LOSS = the model's motor losses + the PWM extra loss + the controller loss (the last two are
 *  0 in Sine).  null while either is unknown (PWM refused), so the tile never shows a total that
 *  leaves a part out. */
export function totalLossShown(baseW: number, mode: DriveMode, drv: VariantReading | null): number | null {
  const e = extraLossTile(mode, drv).value, c = controllerLossTile(mode, drv).value;
  return e == null || c == null ? null : baseW + e + c;
}

/** The EFFICIENCY tile: the SYSTEM efficiency, shaft power over battery power (owner 2026-10-05: motor +
 *  controller), computed from the very numbers the tiles show, so the tiles add up EXACTLY:
 *
 *      EFFICIENCY = P_shaft / (P_shaft + TOTAL LOSS)        (POWER tile, TOTAL LOSS tile)
 *
 *  with TOTAL LOSS = motor losses + PWM extra loss + controller loss (incl. its board copper).  In Sine the
 *  last two are 0, so it equals the model's own efficiency.  null = "—" while PWM is refused or a part of the
 *  total is unknown. */
export function systemEfficiency(mode: DriveMode, drv: VariantReading | null, P_shaft_W: number, P_loss_W: number):
  { value: number | null; blank: Blank } {
  const tot = totalLossShown(P_loss_W, mode, drv);
  if (tot == null) return { value: null, blank: drv ? 'missing' : 'refused' };
  return { value: P_shaft_W > 0 ? (100 * P_shaft_W) / (P_shaft_W + tot) : 0, blank: null };
}

/** The motor's own efficiency: shaft power over the motor's input power, i.e. the motor losses plus the PWM
 *  extra loss (the controller excluded).  Sine: the model's.  null = "—" while PWM is refused / the extra is unknown. */
export function motorEfficiency(mode: DriveMode, drv: VariantReading | null, P_shaft_W: number, P_loss_W: number): number | null {
  const e = extraLossTile(mode, drv).value;
  if (e == null) return null;
  return P_shaft_W > 0 ? (100 * P_shaft_W) / (P_shaft_W + P_loss_W + e) : 0;
}

// ── the temperatures row (propeller-cooled machines) ─────────────────────────────────────────
// ONE row in the results block, the same five tiles from the first render on: while the
// propeller data is loading, or the load is refused, they read "—" (a block never appears
// later and pushes the grid down).
export const TEMP_ROW_IDS = ['tWinding', 'tMagnet', 'tHousing', 'airSpeed'] as const;

export interface TempTileSpec {
  id: string; labelKey: string; tipKey: string; unit: string; d: number;
  value: number | null;
  /** printed instead of the number: "> 180" for a temperature over its limit */
  display?: string;
  level?: 'ok' | 'warn' | 'bad';
  /** the limit the tile is judged against (for its tooltip) */
  limit?: number;
  /** the cooling air speed [m/s] and the housing film coefficient h [W/m2K] behind a housing tile: they are
   *  printed in ITS tooltip (one line each), not as tiles (owner 2026-10-05) */
  air?: number | null;
  h?: number | null;
}

export interface TempRowInput {
  T_winding_C: number; T_magnet_C: number; T_housing_C: number;
  air_speed_ms: number; h_W_m2K: number;
}

const _n = (v: number) => String(Number(v.toFixed(0)));

/** `null` input = nothing to show yet / refused: every tile is "—". */
export function tempRowTiles(t: TempRowInput | null, lim: { winding_C: number; magnet_C: number }, still = false): TempTileSpec[] {
  // `still`: the robot-joint cooling (still air + radiation) — same tiles, its own housing / air tips
  const ns = still ? 'configureCooling' : 'configurePropeller';
  const temp = (id: string, labelKey: string, tipKey: string, T: number | null, limit: number): TempTileSpec => {
    if (T == null || !Number.isFinite(T)) return { id, labelKey, tipKey, unit: '°C', d: 0, value: null, limit };
    if (T > limit) return { id, labelKey, tipKey, unit: '°C', d: 0, value: null, display: `> ${_n(limit)}`, level: 'bad', limit };
    return { id, labelKey, tipKey, unit: '°C', d: 0, value: T, level: T > limit - 20 ? 'warn' : 'ok', limit };
  };
  return [
    temp('tWinding', 'configurePropeller.tWinding', 'configurePropeller.tWindingTip', t ? t.T_winding_C : null, lim.winding_C),
    temp('tMagnet', 'configurePropeller.tMagnet', 'configurePropeller.tMagnetTip', t ? t.T_magnet_C : null, lim.magnet_C),
    // the housing has no limit of its own, but it can never be hotter than the winding it carries
    { ...temp('tHousing', 'configurePropeller.tHousing', `${ns}.tHousingTip`, t ? t.T_housing_C : null, lim.winding_C),
      air: t ? t.air_speed_ms : null, h: t ? t.h_W_m2K : null },
    { id: 'airSpeed', labelKey: 'configurePropeller.airSpeed', tipKey: `${ns}.airSpeedTip`, unit: 'm/s', d: 1,
      value: t ? t.air_speed_ms : null },
  ];
}

/** Loss density follows the total (same factor). */
export function lossDensityShown(baseDensity: number, baseW: number, mode: DriveMode, drv: VariantReading | null): number | null {
  const tot = totalLossShown(baseW, mode, drv);
  return tot == null ? null : (baseW > 0 ? baseDensity * (tot / baseW) : baseDensity);
}
