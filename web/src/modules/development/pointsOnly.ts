/** Pure helpers of the points-only Configure card (project motors without a full passport).
 *  Only stored numbers are read; a missing value stays null (shown as "-"), never 0, never interpolated. */
import type { MotorPoint, MotorPointVariant } from './types';

type Rec = Record<string, unknown>;

export function finite(v: unknown): number | null {
  if (typeof v === 'boolean' || v === null || v === undefined || v === '') return null;
  const n = typeof v === 'number' ? v : Number(v);
  return Number.isFinite(n) ? n : null;
}
const rec = (v: unknown): Rec => (v && typeof v === 'object' && !Array.isArray(v) ? v as Rec : {});

/** One result tile: where its stored number lives, its unit and display rounding. */
export interface PointTileSpec {
  id: string;
  /** key under `points.tile.<id>` in the development namespace */
  unit: string;
  d: number;
  /** key of `comparison` (stored, 3-D corrected where the run carries its factor) */
  key: string;
  /** multiply the stored number to reach `unit` (W -> kW) */
  scale?: number;
  /** better direction for the vs-step colouring; undefined = neutral */
  goodHi?: boolean;
  /** shown even when the value is missing; other tiles appear only when the point stores them */
  core?: boolean;
  group: 'output' | 'loss' | 'electric' | 'thermal' | 'mass';
}

export const POINT_TILES: PointTileSpec[] = [
  { id: 'torque', unit: 'N·m', d: 2, key: 'torque_nm', goodHi: true, core: true, group: 'output' },
  { id: 'power', unit: 'kW', d: 2, key: 'power_w', scale: 1e-3, goodHi: true, core: true, group: 'output' },
  { id: 'efficiency', unit: '%', d: 1, key: 'efficiency_pct', goodHi: true, core: true, group: 'output' },
  { id: 'ripple', unit: '%', d: 1, key: 'ripple_pct', goodHi: false, core: true, group: 'output' },
  { id: 'losses', unit: 'W', d: 0, key: 'p_loss_total_w', goodHi: false, core: true, group: 'loss' },
  { id: 'pCu', unit: 'W', d: 0, key: 'p_cu_w', goodHi: false, group: 'loss' },
  { id: 'pFe', unit: 'W', d: 0, key: 'p_fe_w', goodHi: false, group: 'loss' },
  { id: 'pMag', unit: 'W', d: 0, key: 'p_mag_w', goodHi: false, group: 'loss' },
  { id: 'pSolid', unit: 'W', d: 0, key: 'p_solid_w', goodHi: false, group: 'loss' },
  { id: 'pMech', unit: 'W', d: 0, key: 'p_mech_total_w', goodHi: false, group: 'loss' },
  { id: 'vLine', unit: 'V', d: 1, key: 'v_line_peak_v', core: true, group: 'electric' },
  { id: 'vPhase', unit: 'V', d: 1, key: 'v_phase_peak_v', group: 'electric' },
  { id: 'vDc', unit: 'V', d: 1, key: 'v_dc_required_v', group: 'electric' },
  { id: 'j', unit: 'A/mm²', d: 2, key: 'current_density_a_mm2', goodHi: false, core: true, group: 'electric' },
  { id: 'coilTemp', unit: '°C', d: 0, key: 'coil_temp_c', goodHi: false, core: true, group: 'thermal' },
  { id: 'magnetTemp', unit: '°C', d: 0, key: 'magnet_temp_c', goodHi: false, group: 'thermal' },
  { id: 'statorTemp', unit: '°C', d: 0, key: 'stator_temp_c', goodHi: false, group: 'thermal' },
  { id: 'rotorTemp', unit: '°C', d: 0, key: 'rotor_temp_c', goodHi: false, group: 'thermal' },
  { id: 'bearingTemp', unit: '°C', d: 0, key: 'bearing_temp_c', goodHi: false, group: 'thermal' },
  { id: 'mass', unit: 'kg', d: 2, key: 'mass_kg', goodHi: false, core: true, group: 'mass' },
  { id: 'torqueMass', unit: 'N·m/kg', d: 2, key: 'torque_to_mass_nm_kg', goodHi: true, group: 'mass' },
];

export const TILE_GROUPS = ['output', 'loss', 'electric', 'thermal', 'mass'] as const;

/** The stored value of a tile for a point, already in the tile's unit; null when the point does not store it. */
export function tileValue(point: MotorPoint | null | undefined, spec: PointTileSpec): number | null {
  const stored = finite(rec(point?.comparison)[spec.key]);
  return stored === null ? null : stored * (spec.scale ?? 1);
}

/** Tiles to draw for a point: every core tile (blank when missing) plus the others that carry a stored value.
 *  `peers` (the same duty in the other variants) also keeps a tile visible so a step table lines up. */
export function visibleTiles(point: MotorPoint | null | undefined, peers: Array<MotorPoint | null | undefined> = []): PointTileSpec[] {
  return POINT_TILES.filter((spec) => spec.core || tileValue(point, spec) !== null
    || peers.some((peer) => tileValue(peer, spec) !== null));
}

export interface PointOperating { rpm: number | null; currentA: number | null; gammaDeg: number | null }

/** The operating point of a stored point: speed, phase current (A rms) and current angle, as stored. */
export function pointOperating(point: MotorPoint | null | undefined): PointOperating {
  const op = rec(point?.operating_point);
  const em = rec(point?.electromagnetic);
  const first = (...values: unknown[]) => { for (const v of values) { const n = finite(v); if (n !== null) return n; } return null; };
  return {
    rpm: first(op.rpm, em.rpm),
    currentA: first(op.current_arms, em.current_arms, em.i_phase_rms, em.i_phase_rms_a),
    gammaDeg: first(op.gamma_deg, em.gamma_deg),
  };
}

/** Whether the DC-bus voltage the user typed covers the bus this point was computed to need.
 *  Pure arithmetic on the stored `v_dc_required_v`; null when that number is not stored or the input is not a voltage. */
export function busHeadroom(point: MotorPoint | null | undefined, busV: number | null):
  { requiredV: number; headroomPct: number; ok: boolean } | null {
  const required = finite(rec(point?.comparison).v_dc_required_v);
  if (required === null || required <= 0 || busV === null || !(busV > 0)) return null;
  const headroomPct = (busV - required) / required * 100;
  return { requiredV: required, headroomPct, ok: busV >= required };
}

export type StepBaseMode = 'none' | 'previous' | 'first';

/** The same-duty point of another step to colour the tiles against (differences of stored numbers only). */
export function baseVariantPoint(variants: MotorPointVariant[], index: number, duty: string, mode: StepBaseMode): MotorPoint | null {
  if (mode === 'none' || index < 0 || index >= variants.length) return null;
  const baseIndex = mode === 'first' ? 0 : index - 1;
  if (baseIndex < 0 || baseIndex === index) return null;
  return variants[baseIndex].points.find((p) => p.duty === duty) ?? null;
}

/** Which point a variant switch lands on: the same duty if it is stored there, else the first stored point. */
export function dutyAfterVariantSwitch(variant: MotorPointVariant | null | undefined, wanted: string | null): string | null {
  if (!variant || variant.points.length === 0) return null;
  return variant.points.find((p) => p.duty === wanted)?.duty ?? variant.points[0].duty;
}

/** Locate the variant a project motor refers to (ordinary alias or canonical die, case-insensitive). */
export function findVariantIndex(variants: MotorPointVariant[], die: string | null | undefined, config: string | null | undefined): number {
  if (!die || !config) return -1;
  const same = (a: string, b: string) => a.toLowerCase() === b.toLowerCase();
  return variants.findIndex((v) => same(v.config, config) && (same(v.die, die) || same(v.canonical_die, die)));
}
