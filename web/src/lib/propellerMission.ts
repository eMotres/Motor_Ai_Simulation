// Propellers > Mission: the pure helpers behind the page (defaults, unit conversion, request body, result rows, chart data, motor picker).
// No React, no window: web/src/lib/__tests__/propellerMission.test.mjs imports this file.

/** Parse a text field to a number (comma or point); null for empty / not a number.  Same rule as ``propellerDesign.parseNumber``, kept
 *  here so this file has no sibling import and runs under ``node --test``. */
export function parseNumber(text: string): number | null {
  const t = text.trim().replace(',', '.');
  if (t === '') return null;
  const n = Number(t);
  return Number.isFinite(n) ? n : null;
}

export const KMH = 3.6;
export const kmhToMs = (kmh: number): number => kmh / KMH;
export const msToKmh = (ms: number): number => ms * KMH;

export type ModelKind = 's1' | 'estimate' | 'none';
export type Objective = 'range_km' | 'endurance_min' | 'top_speed_ms' | 'hover_g_per_w';
export const OBJECTIVES: readonly Objective[] = ['range_km', 'endurance_min', 'top_speed_ms', 'hover_g_per_w'];

export interface MotorEntry {
  id: string | null; name: string; diameter_mm: number | null; die: string | null; config: string | null; kind: ModelKind; reason: string | null;
  kv_rpm_per_V?: number | null; R_phase_ohm?: number | null; pole_pairs?: number | null; mass_kg?: number | null; I_peak_A?: number | null;
  drives?: string[]; board_cap_A?: number | null; winding?: { turns?: number; strip_thickness_mm?: number; strip_width_mm?: number } | null;
}

/** The value of a picker option: the card id, or `s1:<die>|<config>` for an S1 model no card carries the name of. */
export function motorKey(m: MotorEntry): string {
  return m.id ? m.id : `s1:${m.die}|${m.config}`;
}

export interface MotorGroup { die: string; motors: MotorEntry[] }

/** Picker rows grouped by die (diameter for a motor with no die) and filtered by a search text over name, die and configuration.  A greyed
 *  motor (kind none) is kept, listed after the usable ones of its group. */
export function motorGroups(motors: MotorEntry[], query: string): MotorGroup[] {
  const q = query.trim().toLowerCase();
  const hit = (m: MotorEntry) => !q || [m.name, m.die ?? '', m.config ?? ''].some((s) => s.toLowerCase().includes(q));
  const by = new Map<string, MotorEntry[]>();
  for (const m of motors) {
    if (!hit(m)) continue;
    const g = m.die ?? (m.diameter_mm ? `${m.diameter_mm} mm` : '-');
    by.set(g, [...(by.get(g) ?? []), m]);
  }
  const rank = (m: MotorEntry) => (m.kind === 'none' ? 1 : 0);
  return [...by.entries()].sort((a, b) => a[0].localeCompare(b[0]))
    .map(([die, ms]) => ({ die, motors: [...ms].sort((a, b) => rank(a) - rank(b) || a.name.localeCompare(b.name)) }));
}

export interface MissionForm {
  motorKey: string; drive: string; boardCap: string; rotors: string;
  kvMode: 'free' | 'as_built'; turns0: string; strip0: string; stripWidth: string; headroom: string; turnsBelow: string; turnsAbove: string; coilTol: string;
  chemistry: string; cells: string; capacity: string; usable: string; battMass: string; vNom: string; vMin: string; vMax: string; whKg: string; rCell: string;
  dryMass: string; payload: string; dragArea: string; maxD: string; minD: string; airTemp: string; altitude: string; avionics: string;
  speedKmh: string; bestSpeed: boolean; hoverShare: string; twCont: string; twPeak: string; tipMach: string; enduranceMode: 'hover' | 'best_speed';
  catalogue: boolean; own: boolean; extendBelow: string; pdRange: string; pdStep: string; chords: string; blades: string; allowUnverified: boolean; battCurrent: string;
  refine: boolean; topN: string; pop: string; generations: string; seed: string; objective: Objective;
}

export const DEFAULT_FORM: MissionForm = {
  motorKey: '', drive: 'sine', boardCap: '', rotors: '4',
  kvMode: 'free', turns0: '', strip0: '', stripWidth: '', headroom: '15', turnsBelow: '3', turnsAbove: '5', coilTol: '0',
  chemistry: 'li-ion-21700', cells: '6', capacity: '16', usable: '85', battMass: '', vNom: '', vMin: '', vMax: '', whKg: '', rCell: '',
  dryMass: '3.5', payload: '1', dragArea: '0.05', maxD: '13', minD: '10', airTemp: '25', altitude: '0', avionics: '0',
  speedKmh: '70', bestSpeed: false, hoverShare: '0', twCont: '1.5', twPeak: '2', tipMach: '0.7', enduranceMode: 'hover',
  catalogue: true, own: true, extendBelow: '0', pdRange: '0.25-0.6', pdStep: '0.05', chords: '1, 1.15', blades: '2', allowUnverified: false, battCurrent: '',
  refine: true, topN: '1', pop: '12', generations: '6', seed: '1', objective: 'range_km',
};

export type BuildResult = { ok: true; body: Record<string, unknown> } | { ok: false; error: string; field: string; errors: Record<string, string> };

function list(text: string): number[] | null {
  const parts = text.split(/[;,\s]+/).filter(Boolean).map((p) => parseNumber(p));
  return parts.length && parts.every((p): p is number => p !== null) ? (parts as number[]) : null;
}

/** Allowed ranges of the numeric fields (the server's own limits, so a typo is shown on its field before the request). */
export const FIELD_RULES: Partial<Record<keyof MissionForm, { min: number; max: number; int?: boolean }>> = {
  rotors: { min: 1, max: 12, int: true }, boardCap: { min: 1, max: 2000 }, turns0: { min: 1, max: 60, int: true }, strip0: { min: 0.05, max: 5 },
  stripWidth: { min: 0.5, max: 30 }, headroom: { min: 0, max: 50 }, turnsBelow: { min: 0, max: 10, int: true }, turnsAbove: { min: 0, max: 10, int: true },
  coilTol: { min: 0, max: 50 }, cells: { min: 1, max: 30, int: true }, capacity: { min: 0.1, max: 500 }, usable: { min: 30, max: 100 }, battMass: { min: 0.01, max: 500 },
  vNom: { min: 2, max: 5 }, vMin: { min: 2, max: 5 }, vMax: { min: 2, max: 5 }, whKg: { min: 20, max: 600 }, rCell: { min: 0, max: 1 }, battCurrent: { min: 1, max: 5000 },
  dryMass: { min: 0.05, max: 500 }, payload: { min: 0, max: 500 }, dragArea: { min: 0, max: 5 }, maxD: { min: 4, max: 40 }, minD: { min: 0, max: 40 },
  airTemp: { min: -40, max: 60 }, altitude: { min: -500, max: 6000 }, avionics: { min: 0, max: 2000 }, speedKmh: { min: 0, max: 288 },
  hoverShare: { min: 0, max: 95 }, twCont: { min: 1, max: 10 }, twPeak: { min: 1, max: 10 }, tipMach: { min: 0.2, max: 1 }, pdStep: { min: 0.01, max: 0.5 }, extendBelow: { min: 0, max: 80 },
  topN: { min: 1, max: 3, int: true }, pop: { min: 4, max: 20, int: true }, generations: { min: 1, max: 10, int: true }, seed: { min: 0, max: 4294967295, int: true },
};

/** `a-b` (or `a – b`, `a b`) -> [a, b]; null when it is not two numbers with a <= b. */
export function parseRange(text: string): [number, number] | null {
  const m = text.trim().replace(/,/g, '.').match(/^(-?[\d.]+)\s*(?:-|–|—|\s)\s*(-?[\d.]+)$/);
  if (!m) return null;
  const lo = Number(m[1]), hi = Number(m[2]);
  return Number.isFinite(lo) && Number.isFinite(hi) && lo <= hi ? [lo, hi] : null;
}

/** The request body of POST /api/propellers/mission/run from the form: SI inside (km/h -> m/s, % -> fraction), an empty optional field
 *  left out (the server takes the preset / the model's value).  EVERY unreadable or out-of-range field is returned in `errors` (key =
 *  form field, value = 'number' | 'range' | 'int'), the first one in `field`. */
export function buildBody(form: MissionForm, motors: MotorEntry[]): BuildResult {
  const motor = motors.find((m) => motorKey(m) === form.motorKey);
  const errors: Record<string, string> = {};
  if (!motor || motor.kind === 'none') errors.motorKey = 'motor';
  const body: Record<string, Record<string, unknown> | string> = { motor: {}, kv: {}, battery: {}, airframe: {}, flight: {}, candidates: {}, refine: {} };
  const put = (g: string, k: string, key: keyof MissionForm, scale = 1, optional = false): void => {
    const text = String(form[key]);
    if (optional && text.trim() === '') return;
    const n = parseNumber(text);
    const rule = FIELD_RULES[key];
    if (n === null) { errors[key] = 'number'; return; }
    if (rule && (n < rule.min || n > rule.max)) { errors[key] = 'range'; return; }
    if (rule?.int && !Number.isInteger(n)) { errors[key] = 'int'; return; }
    (body[g] as Record<string, unknown>)[k] = n * scale;
  };
  put('motor', 'rotors', 'rotors'); put('motor', 'board_cap_A', 'boardCap', 1, true);
  put('kv', 'turns0', 'turns0', 1, true); put('kv', 'strip_thickness0_mm', 'strip0', 1, true); put('kv', 'strip_width_mm', 'stripWidth', 1, true);
  put('kv', 'headroom', 'headroom', 0.01); put('kv', 'turns_below', 'turnsBelow'); put('kv', 'turns_above', 'turnsAbove'); put('kv', 'coil_height_tol', 'coilTol', 0.01);
  put('battery', 'cells_s', 'cells'); put('battery', 'capacity_ah', 'capacity'); put('battery', 'usable', 'usable', 0.01); put('battery', 'mass_kg', 'battMass', 1, true);
  put('battery', 'v_nom', 'vNom', 1, true); put('battery', 'v_min', 'vMin', 1, true); put('battery', 'v_max', 'vMax', 1, true);
  put('battery', 'wh_per_kg', 'whKg', 1, true); put('battery', 'r_cell_ohm_ah', 'rCell', 1, true); put('battery', 'cont_current_A', 'battCurrent', 1, true);
  put('airframe', 'dry_mass_kg', 'dryMass'); put('airframe', 'payload_kg', 'payload'); put('airframe', 'drag_area_m2', 'dragArea');
  put('airframe', 'max_diameter_in', 'maxD'); put('airframe', 'min_diameter_in', 'minD'); put('airframe', 'air_temp_c', 'airTemp');
  put('airframe', 'altitude_m', 'altitude'); put('airframe', 'avionics_W', 'avionics');
  put('flight', 'cruise_speed_ms', 'speedKmh', 1 / KMH); put('flight', 'hover_share_pct', 'hoverShare'); put('flight', 'tw_cont', 'twCont');
  put('flight', 'tw_peak', 'twPeak'); put('flight', 'tip_mach', 'tipMach');
  put('candidates', 'pd_step', 'pdStep'); put('candidates', 'extend_below', 'extendBelow', 0.01);
  put('refine', 'top_n', 'topN'); put('refine', 'pop', 'pop'); put('refine', 'generations', 'generations'); put('refine', 'seed', 'seed');
  // relations between fields, checked before the request; an error on a field that is only in a tooltip goes to its visible field
  const dMin = parseNumber(form.minD), dMax = parseNumber(form.maxD);
  if (dMin !== null && dMax !== null && dMin > dMax && !errors.maxD) errors.maxD = 'order';
  for (const [hidden, shown] of Object.entries(HIDDEN_FIELD)) {
    if (errors[hidden]) { if (!errors[shown]) errors[shown] = errors[hidden]; delete errors[hidden]; }
  }
  const range = parseRange(form.pdRange);
  if (!range || range[0] < 0.1 || range[1] > 1.5) errors.pdRange = 'range';
  const chords = list(form.chords);
  if (!chords || chords.some((c) => c < 0.5 || c > 1.6)) errors.chords = chords ? 'range' : 'number';
  const blades = list(form.blades);
  if (!blades || blades.some((b) => b !== 2 && b !== 3)) errors.blades = blades ? 'range' : 'number';
  const keys = Object.keys(errors);
  if (keys.length > 0 || !motor || !range || !chords || !blades) return { ok: false, error: errors[keys[0]] ?? 'number', field: keys[0] ?? 'motorKey', errors };
  const m = body.motor as Record<string, unknown>;
  if (motor.id) m.card_id = motor.id; else { m.die = motor.die; m.config = motor.config; }
  m.drive = form.drive || 'sine';
  Object.assign(body.kv as object, { mode: form.kvMode, allow_unverified: form.allowUnverified });
  Object.assign(body.battery as object, { chemistry: form.chemistry });
  Object.assign(body.flight as object, { best_speed: form.bestSpeed, endurance_mode: form.enduranceMode });
  Object.assign(body.candidates as object, { catalogue: form.catalogue, own: form.own, chords, blades, pd_min: range[0], pd_max: range[1] });
  Object.assign(body.refine as object, { enabled: form.refine });
  body.objective = form.objective;
  return { ok: true, body };
}

/** Fields shown only in a tooltip (owner rule: one value on screen, the other bound in the tooltip) and the visible field that carries
 *  their errors. */
export const HIDDEN_FIELD: Record<string, keyof MissionForm> = { minD: 'maxD' };

/** P/D: the screen shows ONE value, the upper bound of the grid (pdRange 'lo-hi' is kept as stored, the lower bound is in the tooltip). */
export function pdShown(pdRange: string): string {
  const r = parseRange(pdRange);
  return r ? String(r[1]) : pdRange;
}
export function pdLow(pdRange: string): string {
  const r = parseRange(pdRange);
  return r ? String(r[0]) : '';
}
export function withPdHigh(pdRange: string, text: string): string {
  const r = parseRange(pdRange);
  return `${r ? r[0] : 0.25}-${text.trim()}`;
}

/** The form as it was remembered: only the known keys, each with the type of its default (a stale or edited store never injects anything). */
export function sanitizeForm(raw: unknown): MissionForm {
  const out: Record<string, unknown> = { ...DEFAULT_FORM };
  if (raw && typeof raw === 'object') {
    for (const [k, def] of Object.entries(DEFAULT_FORM)) {
      const v = (raw as Record<string, unknown>)[k];
      if (typeof v === typeof def && (typeof v !== 'string' || v.length <= 200)) out[k] = v;
    }
  }
  if (!['free', 'as_built'].includes(String(out.kvMode))) out.kvMode = DEFAULT_FORM.kvMode;
  if (!['hover', 'best_speed'].includes(String(out.enduranceMode))) out.enduranceMode = DEFAULT_FORM.enduranceMode;
  if (!(OBJECTIVES as readonly string[]).includes(String(out.objective))) out.objective = DEFAULT_FORM.objective;
  return out as unknown as MissionForm;
}

/** A server validation message names a field as `group.key`: the form field it belongs to (null when unknown). */
const SERVER_FIELDS: Record<string, keyof MissionForm> = {
  'motor.rotors': 'rotors', 'motor.board_cap_A': 'boardCap', 'kv.turns0': 'turns0', 'kv.strip_thickness0_mm': 'strip0', 'kv.strip_width_mm': 'stripWidth',
  'kv.headroom': 'headroom', 'kv.turns_below': 'turnsBelow', 'kv.turns_above': 'turnsAbove', 'kv.coil_height_tol': 'coilTol', 'battery.cells_s': 'cells',
  'battery.capacity_ah': 'capacity', 'battery.usable': 'usable', 'battery.mass_kg': 'battMass', 'battery.v_nom': 'vNom', 'battery.v_min': 'vMin',
  'battery.v_max': 'vMax', 'battery.wh_per_kg': 'whKg', 'battery.r_cell_ohm_ah': 'rCell', 'battery.cont_current_A': 'battCurrent', 'airframe.dry_mass_kg': 'dryMass',
  'airframe.payload_kg': 'payload', 'airframe.drag_area_m2': 'dragArea', 'airframe.max_diameter_in': 'maxD', 'airframe.min_diameter_in': 'maxD',
  'airframe.air_temp_c': 'airTemp', 'airframe.altitude_m': 'altitude', 'airframe.avionics_W': 'avionics', 'flight.cruise_speed_ms': 'speedKmh',
  'flight.hover_share_pct': 'hoverShare', 'flight.tw_cont': 'twCont', 'flight.tw_peak': 'twPeak', 'flight.tip_mach': 'tipMach', 'candidates.pd_min': 'pdRange',
  'candidates.pd_max': 'pdRange', 'candidates.pd_step': 'pdStep', 'candidates.extend_below': 'extendBelow', 'candidates.chords': 'chords', 'candidates.blades': 'blades', 'refine.top_n': 'topN',
  'refine.pop': 'pop', 'refine.generations': 'generations', 'refine.seed': 'seed', 'refine': 'pop',
};
export function serverFieldKey(message: string): keyof MissionForm | null {
  const m = message.match(/\b([a-z]+(?:\.[A-Za-z0-9_]+)?)\b/g) ?? [];
  for (const w of m) if (SERVER_FIELDS[w]) return SERVER_FIELDS[w];
  return null;
}

export const RUN_ID_RX = /^msn-[0-9a-f]{10,12}$/;
/** A run id read from storage: the id or null (nothing else is ever sent to the server from a stored value). */
export function validRunId(v: unknown): string | null {
  return typeof v === 'string' && RUN_ID_RX.test(v) ? v : null;
}
/** The remembered row, if the opened run still has it; else null (the page falls back to the best row without a word). */
export function pickRow(rows: Array<{ id: string }>, id: unknown): string | null {
  return typeof id === 'string' && rows.some((r) => r.id === id) ? id : null;
}

export type RunStatus = 'queued' | 'running' | 'done' | 'stopped' | 'failed' | 'interrupted';
export const RUN_STATUSES: readonly RunStatus[] = ['queued', 'running', 'done', 'stopped', 'failed', 'interrupted'];
/** The translation key of a run status (an unknown value gets the `interrupted` text, never the raw English word). */
export function statusKey(status: string): string {
  return `propMissionStatus_${(RUN_STATUSES as readonly string[]).includes(status) ? status : 'interrupted'}`;
}
/** The translation key of an API error by HTTP status; the server's own text follows it as detail. */
export function errorKey(status: number): string {
  return [401, 403, 404, 409, 422, 499, 503].includes(status) ? `propMissionErr_${status}` : 'propMissionErr_other';
}

export interface Point {
  V_ms: number; thrust_N: number; rpm: number; torque_Nm: number; current_A: number | null; pack_power_W: number | null; motor_eta: number | null;
  T_winding_C: number | null; tip_mach: number; tilt_deg: number | null; verification?: string | null; pack_V?: number | null;
}
export interface Failure { rule: string; detail: string; params?: Record<string, string | number | null> }

/** The translation of a failure's WHOLE message (review #922 r3 D): the key and its parameters; the server's English ``detail`` is
 *  never shown.  Point failures (hover / cruise ...) carry the reason of the point. */
export function failureMessage(f: Failure): { key: string; params: Record<string, string | number> } | null {
  const p: Record<string, string | number> = {};
  for (const [k, v] of Object.entries(f.params ?? {})) if (v !== null && v !== undefined) p[k] = v;
  if (typeof p.reason === 'string') {
    const reason = p.reason.startsWith('motor_') ? 'motor' : p.reason;
    return { key: `propMissionPointReason_${reason}`, params: p };
  }
  if (['headroom', 'tw_cont', 'tw_peak', 'drive_bus_window'].includes(f.rule) && Object.keys(p).length) return { key: `propMissionRuleMsg_${f.rule}`, params: p };
  return null;
}
export interface CandidateResult {
  id: string; name: string; source: 'own' | 'catalogue'; prop_id?: string | null; D_in: number; pitch_ratio: number | null; pitch_in?: number | null;
  chord_scale: number | null; blades: number | null; flags?: string[]; tested_rpm?: [number, number]; turns?: number | null; winding_known?: boolean; strip?: { thickness_mm: number | null; width_mm: number | null; stock_kg: number | null; check: boolean; low_stock: boolean; unknown?: boolean } | null;
  as_built?: boolean; kv_rpm_per_V?: number | null; checks?: Record<string, string>; curve?: { source: string | null; points: number; rpm_min: number | null; rpm_max: number | null };
  vector?: Record<string, number | string> | null; objectives: Partial<Record<Objective, number | null>>; objective_value: number | null;
  tw_cont?: number; tw_peak?: number; points?: { hover: Point | null; cruise: Point | null; hover_end?: Point | null; cruise_end?: Point | null }; failures: Failure[]; passed: boolean;
}
export interface Alternative { turns: number; kv_rpm_per_V: number | null; objectives: CandidateResult['objectives']; tw_cont?: number; tw_peak?: number; passed?: boolean }
export interface Refined {
  from_id: string; from_name: string; seed_vector: Record<string, number | string>; seed_value: number | null; best_vector: Record<string, number | string> | null;
  best_value: number | null; gain_pct: number | null; result: CandidateResult | null; n_evaluations: number; n_feasible: number;
  settings: { pop: number; generations: number; seed: number; free: string[] }; optimizer_run_id?: string | null;
}
export interface MissionResult {
  results: CandidateResult[]; alternatives: Record<string, Alternative[]>; refined: Refined[]; note: string;
  own_family?: { diameters_in: [number, number] | null; in_window: number; requested: boolean }; stopped: boolean; stopped_stage?: string | null; complete?: boolean;
  provenance?: Record<string, unknown>;
  counts: { total: number; done: number; passed: number };
  mission: { mass_kg: number; weight_N: number; rho_kg_m3: number; pack: { E_usable_Wh: number; mass_kg: number } };
  winding: { turns0: number; options: Array<{ turns: number }> };
}

export type SortKey = 'objective' | 'name' | 'D_in' | 'pitch_ratio' | 'turns' | 'kv' | 'range_km' | 'endurance_min' | 'top_speed_ms' | 'hover_g_per_w' | 'tw_cont' | 'tw_peak';

export function sortValue(r: CandidateResult, key: SortKey): number | string | null {
  switch (key) {
    case 'name': return r.name;
    case 'D_in': return r.D_in;
    case 'pitch_ratio': return r.pitch_ratio;
    case 'turns': return r.turns ?? null;
    case 'kv': return r.kv_rpm_per_V ?? null;
    case 'tw_cont': return r.tw_cont ?? null;
    case 'tw_peak': return r.tw_peak ?? null;
    case 'objective': return r.objective_value;
    default: return r.objectives[key] ?? null;
  }
}

/** The table order: a passing row always above a failed one, then by ``key`` (asc / desc); a row without a value after the rows with one. */
export function sortRows(rows: CandidateResult[], key: SortKey, dir: 'asc' | 'desc'): CandidateResult[] {
  const sgn = dir === 'asc' ? 1 : -1;
  return [...rows].sort((a, b) => {
    if (a.passed !== b.passed) return a.passed ? -1 : 1;
    const x = sortValue(a, key), y = sortValue(b, key);
    if (x === null && y === null) return 0;
    if (x === null) return 1;
    if (y === null) return -1;
    if (typeof x === 'string' || typeof y === 'string') return sgn * String(x).localeCompare(String(y));
    return sgn * (x - y);
  });
}

export interface TableRow {
  id: string; name: string; source: string; D_in: number; pitch: number | null; blades: number | null; turns: number | null; strip: string; kv: number | null;
  objective: number | null; range_km: number | null; endurance_min: number | null; top_speed_kmh: number | null; hover_g_per_w: number | null;
  tw_cont: number | null; tw_peak: number | null; passed: boolean; reasons: string[]; stripFlags: string[];
}

/** One display row: speeds in km/h, the strip as thickness x width, the failure rules as keys for the translation. */
export function tableRow(r: CandidateResult): TableRow {
  const s = r.strip;
  const top = r.objectives.top_speed_ms;
  return {
    id: r.id, name: r.name, source: r.source, D_in: r.D_in, pitch: r.pitch_ratio, blades: r.blades, turns: r.turns ?? null,
    strip: s && s.thickness_mm != null && s.width_mm != null ? `${s.thickness_mm} x ${s.width_mm}` : '', kv: r.kv_rpm_per_V ?? null, objective: r.objective_value,
    range_km: r.objectives.range_km ?? null, endurance_min: r.objectives.endurance_min ?? null,
    top_speed_kmh: top === null || top === undefined ? null : msToKmh(top), hover_g_per_w: r.objectives.hover_g_per_w ?? null,
    tw_cont: r.tw_cont ?? null, tw_peak: r.tw_peak ?? null, passed: r.passed, reasons: r.failures.map((f) => f.rule),
    stripFlags: s ? [...(s.low_stock ? ['low_stock'] : []), ...(s.check ? ['check'] : [])] : [],
  };
}

export interface ChartSeries { key: string; pitch: number; points: Array<{ D: number; value: number }> }
export interface ChartData { lines: ChartSeries[]; dots: Array<{ D: number; value: number; name: string }> }

/** Objective against diameter: one line per P/D of the own family (the best passing value at each diameter over chord scale and
 *  blade count), catalogue propellers as dots.  Failed rows are not drawn. */
export function chartData(rows: CandidateResult[], objective: Objective): ChartData {
  const lines = new Map<number, Map<number, number>>();
  const dots: ChartData['dots'] = [];
  for (const r of rows) {
    const v = r.objectives[objective];
    if (!r.passed || v === null || v === undefined) continue;
    if (r.source === 'catalogue') { dots.push({ D: r.D_in, value: v, name: r.name }); continue; }
    if (r.pitch_ratio === null) continue;
    const pr = Math.round(r.pitch_ratio * 100) / 100;
    const m = lines.get(pr) ?? new Map<number, number>();
    m.set(r.D_in, Math.max(m.get(r.D_in) ?? -Infinity, v));
    lines.set(pr, m);
  }
  return {
    lines: [...lines.entries()].sort((a, b) => a[0] - b[0])
      .map(([pitch, m]) => ({ key: `pd${pitch}`, pitch, points: [...m.entries()].sort((a, b) => a[0] - b[0]).map(([D, value]) => ({ D, value })) })),
    dots,
  };
}

/** Refined value against the stage-1 seed: the gain in percent, null when either is missing. */
export function gainPct(seed: number | null, best: number | null): number | null {
  return seed === null || best === null || seed === 0 ? null : (100 * (best - seed)) / Math.abs(seed);
}

/** Name of the draft that "Open in Designer" saves: ASCII, at most 60 characters, starts with a letter or digit. */
export function draftName(prefix: string, runId: string, rank: number): string {
  return `${prefix}_${runId.replace(/^msn-/, '')}_${rank}`.replace(/[^A-Za-z0-9_.-]/g, '_').slice(0, 60);
}

export const DRAFT_PREFIX = 'mission';

/** The strip of a result as text; null when the winding of the model is unknown (the server sends null sizes, never placeholders). */
export function stripText(s: { thickness_mm: number | null; width_mm: number | null } | null | undefined): string | null {
  if (!s || s.thickness_mm == null || s.width_mm == null) return null;
  return `${s.thickness_mm} × ${s.width_mm} mm`;
}
