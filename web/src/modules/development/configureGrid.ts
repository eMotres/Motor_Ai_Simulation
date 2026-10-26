/** The result grid of a stored point, laid out EXACTLY like the ordinary Configure's (owner 2026-10-26: both modes look identical).
 *
 *  Same rows in the same order, the same label and tooltip keys (`configure.*`, controller namespace), the same units and
 *  roundings as `ConfiguratorPanel`'s tiles; fed from the stored point instead of the tuner.  Two rules from the owner:
 *    - a cell whose value cannot be computed from the stored data is NOT drawn (no "-" cell);
 *    - a cell whose value is exactly 0 is not drawn either;
 *  and a row left with no cell disappears.  Values derived from stored ones (power / mass, rms from peak, DC bus from the phase
 *  peak, loss density) follow the formulas the Configure uses.  The temperatures row of the Configure belongs to the thermal
 *  block of the points view (the coupled loop, with its own source label).  Pure functions: `node --test` imports this file. */
import { finite } from './pointsOnly.ts';
import { massKind } from './pointsLimited.ts';
import { coggingDigits, coggingFromFlat, coggingTipParts } from '../../lib/coggingTile.ts';

type Rec = Record<string, unknown>;

export interface GridCell {
  id: string;
  /** label key in the `controller` namespace (the ordinary Configure's) */
  labelKey: string;
  /** tooltip key in the same namespace, or null */
  tipKey: string | null;
  unit: string;
  d: number;
  value: number;
  /** more tooltip sentences (locale key + interpolation values), joined after `tipKey`'s; only the cogging cell has any */
  tipParts?: Array<{ key: string; args?: Record<string, string | number> }>;
}

interface Spec {
  id: string; labelKey: (mass: 'active' | 'full') => string; tipKey: (mass: 'active' | 'full') => string | null; unit: string;
  d: number | ((value: number) => number); value: (c: Rec) => number | null;
  tipParts?: (c: Rec) => GridCell['tipParts'];
}

const num = (c: Rec, key: string): number | null => finite(c[key]);
const SQRT2 = Math.SQRT2;
const SQRT3 = Math.sqrt(3);
const per = (a: number | null, b: number | null, scale = 1): number | null => (a !== null && b !== null && b > 0 ? (a / b) * scale : null);
const times = (a: number | null, k: number): number | null => (a === null ? null : a * k);

/** The tooltip sentences of the cogging cell (shared with the other screens: lib/coggingTile). */
function coggingParts(c: Rec): GridCell['tipParts'] {
  return coggingTipParts(coggingFromFlat(c));
}

const same = (key: string) => () => key;
const none = () => null;

/** Rows as in the ordinary Configure (rows 1, 2 and 3 of its grid; row 3 follows the temperatures row). */
const ROWS: Spec[][] = [
  [
    { id: 'torque', labelKey: same('configure.torque'), tipKey: same('configure.torqueTip'), unit: 'N·m', d: 1, value: (c) => num(c, 'torque_nm') },
    { id: 'power', labelKey: same('configure.power'), tipKey: same('configure.powerTip'), unit: 'kW', d: 2, value: (c) => times(num(c, 'power_w'), 1e-3) },
    { id: 'mass', labelKey: (m) => (m === 'active' ? 'configure.massPartial' : 'configure.mass'), tipKey: (m) => (m === 'active' ? 'configure.massPartialTip' : 'configure.massFullTip'), unit: 'kg', d: 3, value: (c) => num(c, 'mass_kg') },
    { id: 'efficiency', labelKey: same('configure.efficiency'), tipKey: same('configure.efficiencyTip'), unit: '%', d: 1, value: (c) => num(c, 'efficiency_pct') },
    { id: 'ripple', labelKey: same('configure.tRipple'), tipKey: same('configure.tRippleTip'), unit: '%', d: 1, value: (c) => num(c, 'ripple_pct') },
    // COGGING TORQUE (owner 2026-10-26): peak-to-peak with no current, a property of the configuration (the same stored number on
    // every duty); half-amplitude, share of the continuous torque, magnet state and method go to the tooltip.  Not drawn without a value.
    { id: 'cogging', labelKey: same('configure.cogging'), tipKey: same('configure.coggingTip'), unit: 'N·m', d: coggingDigits,
      value: (c) => num(c, 'cogging_pkpk_nm'), tipParts: coggingParts },
    { id: 'torqueMass', labelKey: (m) => (m === 'active' ? 'configure.tPerMassActive' : 'configure.tPerMass'), tipKey: same('configure.tPerMassTip'), unit: 'N·m/kg', d: 2,
      value: (c) => num(c, 'torque_to_mass_nm_kg') ?? per(num(c, 'torque_nm'), num(c, 'mass_kg')) },
    { id: 'powerMass', labelKey: (m) => (m === 'active' ? 'configure.pPerMassActive' : 'configure.pPerMass'), tipKey: same('configure.pPerMassTip'), unit: 'kW/kg', d: 2,
      value: (c) => per(num(c, 'power_w'), num(c, 'mass_kg'), 1e-3) },
  ],
  [
    { id: 'losses', labelKey: same('configure.totalLoss'), tipKey: same('configure.totalLossTip'), unit: 'W', d: 0, value: (c) => num(c, 'p_loss_total_w') },
    { id: 'pFe', labelKey: same('configure.ironLoss'), tipKey: same('configure.ironLossTip'), unit: 'W', d: 0, value: (c) => num(c, 'p_fe_w') },
    { id: 'pCu', labelKey: same('configure.copperLoss'), tipKey: none, unit: 'W', d: 0, value: (c) => num(c, 'p_cu_w') },
    { id: 'pMag', labelKey: same('configure.magnetLoss'), tipKey: same('configure.magnetLossTip'), unit: 'W', d: 0, value: (c) => num(c, 'p_mag_w') },
    { id: 'lossDensity', labelKey: same('configure.lossDensity'), tipKey: same('configure.lossDensityTip'), unit: 'W/kg', d: 0,
      value: (c) => per(num(c, 'p_loss_total_w'), num(c, 'mass_kg')) },
  ],
  [
    { id: 'dcBus', labelKey: same('configure.dcBusMin'), tipKey: same('configure.dcBusMinTip'), unit: 'V', d: 0,
      value: (c) => num(c, 'v_dc_required_v') ?? times(num(c, 'v_phase_peak_v'), SQRT3) },
    { id: 'vLine', labelKey: same('configure.vLinePeak'), tipKey: same('configure.vLinePeakTip'), unit: 'V', d: 1, value: (c) => num(c, 'v_line_peak_v') },
    { id: 'vPhase', labelKey: same('configure.vPhasePeak'), tipKey: same('configure.vPhasePeakTip'), unit: 'V', d: 1, value: (c) => num(c, 'v_phase_peak_v') },
    { id: 'vLineRms', labelKey: same('configure.vLineRms'), tipKey: same('configure.vLineRmsTip'), unit: 'V', d: 1, value: (c) => { const v = num(c, 'v_line_peak_v'); return v === null ? null : v / SQRT2; } },
    { id: 'vPhaseRms', labelKey: same('configure.vPhaseRms'), tipKey: same('configure.vPhaseRmsTip'), unit: 'V', d: 1, value: (c) => { const v = num(c, 'v_phase_peak_v'); return v === null ? null : v / SQRT2; } },
    { id: 'currDensity', labelKey: same('configure.currDensity'), tipKey: same('configure.currDensityTip'), unit: 'A/mm²', d: 1, value: (c) => num(c, 'current_density_a_mm2') },
  ],
];

/** The Configure's result rows for one stored point: cells that cannot be computed or are exactly 0 are left out, empty rows
 *  disappear.  `point.comparison` carries the stored numbers (and the mass stamp). */
export function configureGrid(point: { comparison?: Record<string, unknown> } | null | undefined): GridCell[][] {
  const c = (point?.comparison ?? {}) as Rec;
  const mass = massKind(point);
  return ROWS
    .map((row) => row.flatMap((s): GridCell[] => {
      const v = s.value(c);
      if (v === null || !Number.isFinite(v) || v === 0) return [];
      const parts = s.tipParts?.(c);
      return [{ id: s.id, labelKey: s.labelKey(mass), tipKey: s.tipKey(mass), unit: s.unit, d: typeof s.d === 'function' ? s.d(v) : s.d, value: v,
        ...(parts && parts.length ? { tipParts: parts } : {}) }];
    }))
    .filter((row) => row.length > 0);
}
