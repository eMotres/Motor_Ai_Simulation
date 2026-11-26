// The editor's state and how it becomes the plain body of POST /api/gears/template (pure; web/src/modules/gears/__tests__).
import type { FieldError, GearRole, Operation, PlainBin, PlainBody, PlainGear, PlainStage, StageKind } from './types';

export interface SpectrumState { bins: PlainBin[]; peak_torque_out_Nm: number; peak_events: number; peak_speed_out_rpm: number }
export interface EditorState {
  name: string; preset_name: string; lubricant: string;
  input_speed_rpm: number; torque_Nm: number; torque_at: 'input' | 'output'; power_direction: 'forward' | 'reverse'; temp_C: number;
  stages: PlainStage[]; spectrum: SpectrumState;
}

export const ROLES: Record<StageKind, GearRole[]> = {
  planetary: ['sun', 'planet', 'ring'],
  compound_planetary: ['sun', 'planet', 'planet2', 'ring'],
  pair: ['gear1', 'gear2'],
};
export const BEARING_SLOTS: Record<StageKind, string[]> = {
  planetary: ['input_bearing', 'planet_bearing', 'output_bearing'],
  compound_planetary: ['input_bearing', 'planet_bearing', 'output_bearing'],
  pair: ['bearing_1a', 'bearing_1b', 'bearing_2a', 'bearing_2b'],
};
export const FIXED_MEMBERS = ['ring', 'sun', 'carrier'] as const;
export const GEAR_FIELDS = ['z', 'm_n_mm', 'b_mm', 'x', 'beta_deg', 'tooth_thickness_allowance_mm'] as const;
export type GearField = typeof GEAR_FIELDS[number];

let counter = 0;
export const newUid = (): string => `stage_${Date.now().toString(36)}${(counter++).toString(36)}${Math.random().toString(36).slice(2, 6)}`;

const gear = (z: number, b: number, m = 0.5): PlainGear => ({ z, m_n_mm: m, b_mm: b, tooth_thickness_allowance_mm: 0.02 });

export function defaultStage(kind: StageKind, name: string): PlainStage {
  if (kind === 'pair') return { uid: newUid(), kind, name, gears: { gear1: gear(20, 12, 1), gear2: gear(40, 12, 1) }, bearings: {} };
  if (kind === 'compound_planetary') {
    return { uid: newUid(), kind, name, n_planets: 3, fixed_member: 'ring',
      gears: { sun: gear(30, 12, 1), planet: gear(40, 12, 1), planet2: gear(30, 12, 1), ring: gear(100, 12, 1) }, bearings: {} };
  }
  return { uid: newUid(), kind, name, n_planets: 3, fixed_member: 'ring', gears: { sun: gear(40, 10), planet: gear(32, 10), ring: gear(104, 10) }, bearings: {} };
}

/** Catalogue card of the preset's default bearings (mirrors the template route). */
export const DEFAULT_BEARING = '618/8-2Z';
const jointBearings = (): Record<string, string> => ({ input_bearing: DEFAULT_BEARING, planet_bearing: DEFAULT_BEARING, output_bearing: DEFAULT_BEARING });
/** The humanoid-joint preset: two planetary stages, 12.884:1 (9.8 N*m at 247 rpm out).  Mirrors the template route's default. */
export function humanoidJoint(): EditorState {
  const s1: PlainStage = { ...defaultStage('planetary', 's1'), bearings: jointBearings() };
  const s2: PlainStage = { ...defaultStage('planetary', 's2'), n_planets: 4, bearings: jointBearings(),
    gears: { sun: gear(38, 12), planet: gear(30, 12), ring: gear(98, 12) } };
  return {
    name: 'joint', preset_name: 'humanoid_robot_joint', lubricant: 'ROBOT_GREASE_NLGI1_PAO',
    input_speed_rpm: 3182, torque_Nm: 9.8, torque_at: 'output', power_direction: 'forward', temp_C: 70,
    stages: [s1, s2],
    spectrum: {
      bins: [
        { torque_out_Nm: 9.8, speed_out_rpm: 247, duration_h: 6000, ambient_temp_C: 25, label: '' },
        { torque_out_Nm: 4.9, speed_out_rpm: 300, duration_h: 4000, ambient_temp_C: 25, label: '' },
      ],
      peak_torque_out_Nm: 24, peak_events: 1000, peak_speed_out_rpm: 20,
    },
  };
}

const stageBody = (s: PlainStage): Record<string, unknown> => {
  const out: Record<string, unknown> = { kind: s.kind, name: s.name, stage_id: s.uid, gears: {}, bearings: {} };
  for (const role of ROLES[s.kind]) {
    const g = s.gears[role];
    if (!g) continue;
    const clean: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(g)) if (v !== undefined && v !== '' && !(typeof v === 'number' && Number.isNaN(v))) clean[k] = v;
    (out.gears as Record<string, unknown>)[role] = clean;
  }
  for (const slot of BEARING_SLOTS[s.kind]) if (s.bearings[slot]) (out.bearings as Record<string, string>)[slot] = s.bearings[slot];
  if (s.kind !== 'pair') { out.n_planets = s.n_planets ?? 3; out.fixed_member = s.fixed_member ?? 'ring'; }
  return out;
};

/** The plain body for /template.  `requestId` is stable per editor session so the request hash changes only with the inputs. */
export function toPlainBody(state: EditorState, operation: Operation, extra: Partial<PlainBody> = {}): PlainBody {
  const body: PlainBody = {
    operation, request_id: 'gears_page', name: state.name, lubricant: state.lubricant, preset_name: state.preset_name,
    input_speed_rpm: state.input_speed_rpm, torque_Nm: state.torque_Nm, torque_at: state.torque_at,
    power_direction: state.power_direction, temp_C: state.temp_C,
    stages: state.stages.map(stageBody), ...extra,
  };
  if (operation === 'spectrum') {
    body.spectrum = { bins: state.spectrum.bins.map((b) => ({ ...b })), peak_torque_out_Nm: state.spectrum.peak_torque_out_Nm,
      peak_events: state.spectrum.peak_events, peak_speed_out_rpm: state.spectrum.peak_speed_out_rpm };
  }
  return body;
}

/** Core and template paths -> one form: /stages/0/sun/z (core) and /stages/0/gears/sun/z (template) both land on the second. */
export function normalisePath(path: string): string {
  return path.replace(/^\/stages\/(\d+)\/(sun|planet2|planet|ring|gear1|gear2)(\/|$)/, '/stages/$1/gears/$2$3')
    .replace(/^\/spectrum\/bins\/(\d+)\/(.*)$/, '/spectrum/bins/$1/$2');
}
export function errorMap(errors: readonly FieldError[]): Record<string, string> {
  const out: Record<string, string> = {};
  for (const e of errors) { const p = normalisePath(e.path); if (!(p in out)) out[p] = e.message; }
  return out;
}
export const gearPath = (stage: number, role: GearRole, field: GearField): string => `/stages/${stage}/gears/${role}/${field}`;
/** Errors that belong to no visible field (shown once, loud, above the results). */
export function unplacedErrors(errors: readonly FieldError[], known: (p: string) => boolean): FieldError[] {
  return errors.filter((e) => !known(normalisePath(e.path)));
}

/** Validator for the remembered editor state: anything malformed gives null and the page opens on the preset. */
export function validateEditor(raw: unknown): EditorState | null {
  try {
    const r = raw as EditorState;
    if (!r || typeof r !== 'object' || !Array.isArray(r.stages) || r.stages.length === 0 || r.stages.length > 6) return null;
    if (typeof r.preset_name !== 'string' || typeof r.lubricant !== 'string' || typeof r.name !== 'string') return null;
    for (const k of ['input_speed_rpm', 'torque_Nm', 'temp_C'] as const) if (typeof r[k] !== 'number' || !Number.isFinite(r[k])) return null;
    if (r.torque_at !== 'input' && r.torque_at !== 'output') return null;
    if (r.power_direction !== 'forward' && r.power_direction !== 'reverse') return null;
    const seen = new Set<string>();
    for (const s of r.stages) {
      if (!s || !(s.kind in ROLES) || typeof s.uid !== 'string' || typeof s.name !== 'string' || seen.has(s.uid)) return null;
      seen.add(s.uid);
      for (const role of ROLES[s.kind]) {
        const g = s.gears?.[role];
        if (!g || typeof g.z !== 'number' || typeof g.m_n_mm !== 'number' || typeof g.b_mm !== 'number') return null;
      }
      if (typeof s.bearings !== 'object' || s.bearings === null) return null;
    }
    const sp = r.spectrum;
    if (!sp || !Array.isArray(sp.bins) || sp.bins.length === 0 || sp.bins.length > 20) return null;
    for (const b of sp.bins) for (const k of ['torque_out_Nm', 'speed_out_rpm', 'duration_h', 'ambient_temp_C'] as const) if (typeof b?.[k] !== 'number') return null;
    for (const k of ['peak_torque_out_Nm', 'peak_events', 'peak_speed_out_rpm'] as const) if (typeof sp[k] !== 'number') return null;
    return r;
  } catch { return null; }
}
