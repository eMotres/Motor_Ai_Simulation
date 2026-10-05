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

/** The DRIVE row: the same six tiles, the same order, in every mode. */
export const DRIVE_ROW_IDS = ['invLoss', 'tj', 'driveEff', 'shaftEffPwm', 'pContMax'] as const;

/** The loss row's last tile (after loss density). */
export const EXTRA_LOSS_ID = 'motorLoss';

const pick = (mode: DriveMode, drv: VariantReading | null, f: (r: VariantReading) => number | null):
  { value: number | null; blank: Blank } => {
  if (mode === 'sine') return { value: null, blank: 'sine' };
  if (!drv) return { value: null, blank: 'refused' };
  const v = f(drv);
  return v == null ? { value: null, blank: 'missing' } : { value: v, blank: null };
};

/** `drv` is the reading to show (null whenever PWM is refused or the drive is Sine). */
export function driveRowTiles(mode: DriveMode, drv: VariantReading | null, tjLimit: number | null): DriveTileSpec[] {
  const t = (id: string, labelKey: string, tipKey: string, unit: string, d: number,
    f: (r: VariantReading) => number | null, goodHi?: boolean): DriveTileSpec =>
    ({ id, labelKey, tipKey, unit, d, goodHi, ...pick(mode, drv, f) });
  const tiles = [
    t('invLoss', 'configureDrive.invLoss', 'configureDrive.invLossTip', 'W', 1, (r) => r.inv_total_W, false),
    t('tj', 'configureDrive.tj', 'configureDrive.tjTip', '°C', 0, (r) => r.tj_C),
    t('driveEff', 'configureDrive.driveEff', 'configureDrive.driveEffTip', '%', 1, (r) => r.eta_drive_pct, true),
    t('shaftEffPwm', 'configureDrive.shaftEffPwm', 'configureDrive.shaftEffPwmTip', '%', 1, (r) => r.eta_shaft_pct, true),
    t('pContMax', 'configureDrive.pContMax', 'configureDrive.pContMaxTip', 'kW', 2,
      (r) => (r.p_cont_max_W == null ? null : r.p_cont_max_W / 1000), true),
  ];
  const tj = tiles[1];
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

/** TOTAL LOSS = the model's loss + the PWM extra loss (0 in Sine).  null while the extra loss
 *  is unknown (PWM refused), so the tile never shows a total that leaves a part out. */
export function totalLossShown(baseW: number, mode: DriveMode, drv: VariantReading | null): number | null {
  const e = extraLossTile(mode, drv).value;
  return e == null ? null : baseW + e;
}

/** Loss density follows the total (same factor). */
export function lossDensityShown(baseDensity: number, baseW: number, mode: DriveMode, drv: VariantReading | null): number | null {
  const tot = totalLossShown(baseW, mode, drv);
  return tot == null ? null : (baseW > 0 ? baseDensity * (tot / baseW) : baseDensity);
}
