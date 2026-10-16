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

/** Whether this passport has computed motor loss inputs, including older point curves/maps. */
export function lossMapAvailability(p: {
  Pfe0_W?: number | null; Pmag0_W?: number | null; R0_ohm?: number | null; endWindFrac?: number | null;
  speed?: { rpm?: number[]; Pfe_W?: number[]; Pmag_W?: number[] } | null;
  loss_grid?: { I_A?: number[]; rpm?: number[]; Pfe_W?: number[][]; Pmag_W?: number[][] } | null;
}): { iron: boolean; copper: boolean; magnet: boolean; complete: boolean } {
  const finiteScalar = (v: number | null | undefined) => typeof v === 'number' && Number.isFinite(v);
  const finiteAxis = (axis: number[] | undefined) => Array.isArray(axis) && axis.length >= 2 && axis.every(Number.isFinite);
  const finiteVector = (v: number[] | undefined, axis: number[] | undefined) => Array.isArray(v)
    && finiteAxis(axis) && v.length === axis!.length && v.every(Number.isFinite);
  const finiteGrid = (v: number[][] | undefined, rows: number[] | undefined, cols: number[] | undefined) =>
    Array.isArray(v) && finiteAxis(rows) && finiteAxis(cols) && v.length === rows!.length
    && v.every((row) => Array.isArray(row) && row.length === cols!.length && row.every(Number.isFinite));
  const grid = p.loss_grid;
  const curve = p.speed;
  const hasGrid = !!(grid && Array.isArray(grid.I_A) && grid.I_A.length >= 2
    && Array.isArray(grid.rpm) && grid.rpm.length >= 2);
  const hasCurve = !!(curve && Array.isArray(curve.rpm) && curve.rpm.length >= 2);
  const iron = hasGrid ? finiteGrid(grid?.Pfe_W, grid?.I_A, grid?.rpm) : hasCurve ? finiteVector(curve?.Pfe_W, curve?.rpm) : finiteScalar(p.Pfe0_W);
  const copper = finiteScalar(p.R0_ohm) && finiteScalar(p.endWindFrac);
  const magnet = hasGrid ? finiteGrid(grid?.Pmag_W, grid?.I_A, grid?.rpm) : hasCurve ? finiteVector(curve?.Pmag_W, curve?.rpm) : finiteScalar(p.Pmag0_W);
  return { iron, copper, magnet, complete: iron && copper && magnet };
}

export interface PointDisplayGate {
  accepted: boolean;
  showMotorLosses: boolean;
  showController: boolean;
  showBatteryDerived: boolean;
}

/** One acceptance decision controls every point-derived Configure presentation. */
export function pointDisplayGate(i: {
  batteryValid: boolean; speedAccepted: boolean; driveMode: DriveMode;
  driveAccepted: boolean; motorLossesComputed: boolean;
}): PointDisplayGate {
  const accepted = i.batteryValid && i.speedAccepted && (i.driveMode === 'sine' || i.driveAccepted);
  return {
    accepted,
    showMotorLosses: accepted && i.motorLossesComputed,
    showController: accepted && i.driveMode === 'pwm' && i.driveAccepted,
    showBatteryDerived: i.batteryValid,
  };
}

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
export function totalLossShown(baseW: number | null, mode: DriveMode, drv: VariantReading | null): number | null {
  if (baseW == null || !Number.isFinite(baseW)) return null;
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
export function systemEfficiency(mode: DriveMode, drv: VariantReading | null, P_shaft_W: number, P_loss_W: number | null):
  { value: number | null; blank: Blank } {
  const tot = totalLossShown(P_loss_W, mode, drv);
  if (tot == null) return { value: null, blank: drv ? 'missing' : 'refused' };
  return { value: P_shaft_W > 0 ? (100 * P_shaft_W) / (P_shaft_W + tot) : 0, blank: null };
}

/** The motor's own efficiency: shaft power over the motor's input power, i.e. the motor losses plus the PWM
 *  extra loss (the controller excluded).  Sine: the model's.  null = "—" while PWM is refused / the extra is unknown. */
export function motorEfficiency(mode: DriveMode, drv: VariantReading | null, P_shaft_W: number, P_loss_W: number | null): number | null {
  if (P_loss_W == null || !Number.isFinite(P_loss_W)) return null;
  const e = extraLossTile(mode, drv).value;
  if (e == null) return null;
  return P_shaft_W > 0 ? (100 * P_shaft_W) / (P_shaft_W + P_loss_W + e) : 0;
}

// ── the temperatures row (EVERY cooling mode: propeller air, still air, typed air, liquid) ──
// ONE row in the results block — the only place Configure shows temperatures (owner 2026-10-12:
// the separate "Estimated temperatures" block is gone).  The same four tiles from the first
// render on: while the cooling data is loading, or the load is refused, they read "—" (a block
// never appears later and pushes the grid down).
export const TEMP_ROW_IDS = ['tWinding', 'tMagnet', 'tHousing', 'airSpeed'] as const;

export interface TempTileSpec {
  id: string; labelKey: string; tipKey: string; unit: string; d: number;
  value: number | null;
  /** printed instead of the number: "> 200" for a temperature over its limit */
  display?: string;
  level?: 'ok' | 'warn' | 'bad';
  /** the limit the tile is judged against (for its tooltip) */
  limit?: number;
  /** the cooling air speed [m/s], the housing film coefficient h [W/m2K] and the cooling surface [cm²]
   *  behind a housing tile: they are printed in ITS tooltip (one clause each), not as tiles
   *  (owner 2026-10-05 for air and h, 2026-10-12 for the surface — it left the removed block) */
  air?: number | null;
  h?: number | null;
  surface?: number | null;
}

export interface TempRowInput {
  T_winding_C: number; T_magnet_C: number; T_housing_C: number;
  /** null = no cooling air over the housing (a liquid jacket) */
  air_speed_ms: number | null;
  h_W_m2K: number;
  surface_cm2: number;
}

const _n = (v: number) => String(Number(v.toFixed(0)));

/** ONE temperature tile, judged the way every temperature tile in this app is judged: over the
 *  limit it reads "> limit" in red (never the absurd number), within 20 K of it amber.
 *  Shared by this row and the Electromagnetic tab's tile panel (lib/emResultTiles.ts), so the
 *  two panels can never disagree on what a temperature means. */
export function tempTileSpec(id: string, labelKey: string, tipKey: string, T: number | null, limit: number): TempTileSpec {
  if (T == null || !Number.isFinite(T)) return { id, labelKey, tipKey, unit: '°C', d: 0, value: null, limit };
  if (T > limit) return { id, labelKey, tipKey, unit: '°C', d: 0, value: null, display: `> ${_n(limit)}`, level: 'bad', limit };
  return { id, labelKey, tipKey, unit: '°C', d: 0, value: T, level: T > limit - 20 ? 'warn' : 'ok', limit };
}

// ── the compact results bar (owner 2026-10-14: on a narrow screen the sliders sit on top and the numbers they move are far below) ──
/** Below this viewport width Configure pins a one-line results bar to the bottom edge. */
export const COMPACT_BAR_MAX_PX = 1280;
/** Is the viewport narrow enough for the compact bar?  (`width` = window.innerWidth.) */
export const compactBarApplies = (width: number): boolean => Number.isFinite(width) && width > 0 && width < COMPACT_BAR_MAX_PX;

export interface CompactTile {
  id: string; labelKey: string; unit: string; d: number;
  value: number | null; display?: string; level?: 'ok' | 'warn' | 'bad';
}

/** The key results the bar shows, from the SAME numbers the full tiles print: torque, power, efficiency, winding and magnet
 *  temperature (judged exactly like the temperature row: "> limit" in red).  A value that is not available reads "—". */
export function compactResultTiles(i: { T_Nm: number | null; P_kW: number | null; eff_pct: number | null; temps: readonly TempTileSpec[] }): CompactTile[] {
  const fin = (v: number | null) => (v != null && Number.isFinite(v) ? v : null);
  const temp = (id: string) => i.temps.find((t) => t.id === id);
  const winding = temp('tWinding'), magnet = temp('tMagnet');
  const t = (s: TempTileSpec | undefined, id: string, labelKey: string): CompactTile =>
    ({ id, labelKey, unit: '°C', d: 0, value: s?.value ?? null, display: s?.display, level: s?.level });
  return [
    { id: 'torque', labelKey: 'configure.torque', unit: 'N·m', d: 1, value: fin(i.T_Nm) },
    { id: 'power', labelKey: 'configure.power', unit: 'kW', d: 2, value: fin(i.P_kW) },
    { id: 'efficiency', labelKey: 'configure.efficiency', unit: '%', d: 1, value: fin(i.eff_pct) },
    t(winding, 'tWinding', 'configurePropeller.tWinding'),
    t(magnet, 'tMagnet', 'configurePropeller.tMagnet'),
  ];
}

/** `null` input = nothing to show yet / refused: every tile is "—". */
export function tempRowTiles(t: TempRowInput | null, lim: { winding_C: number; magnet_C: number }): TempTileSpec[] {
  return [
    tempTileSpec('tWinding', 'configurePropeller.tWinding', 'configurePropeller.tWindingTip', t ? t.T_winding_C : null, lim.winding_C),
    tempTileSpec('tMagnet', 'configurePropeller.tMagnet', 'configurePropeller.tMagnetTip', t ? t.T_magnet_C : null, lim.magnet_C),
    // the housing has no limit of its own, but it can never be hotter than the winding it carries
    { ...tempTileSpec('tHousing', 'configurePropeller.tHousing', 'configurePropeller.tHousingTip', t ? t.T_housing_C : null, lim.winding_C),
      air: t ? t.air_speed_ms : null, h: t ? t.h_W_m2K : null, surface: t ? t.surface_cm2 : null },
    // COOLING AIR: the air speed over the housing (0 m/s in still air, "—" with a liquid jacket)
    { id: 'airSpeed', labelKey: 'configurePropeller.airSpeed', tipKey: 'configurePropeller.airSpeedTip', unit: 'm/s', d: 1,
      value: t ? t.air_speed_ms : null },
  ];
}

/** Loss density follows the total (same factor). */
export function lossDensityShown(baseDensity: number, baseW: number | null, mode: DriveMode, drv: VariantReading | null): number | null {
  if (baseW == null || !Number.isFinite(baseW)) return null;
  const tot = totalLossShown(baseW, mode, drv);
  return tot == null ? null : (baseW > 0 ? baseDensity * (tot / baseW) : baseDensity);
}
