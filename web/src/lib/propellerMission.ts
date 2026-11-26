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
  catalogue: boolean; own: boolean; pdMin: string; pdMax: string; pdStep: string; chords: string; blades: string;
  refine: boolean; topN: string; pop: string; generations: string; seed: string; objective: Objective;
}

export const DEFAULT_FORM: MissionForm = {
  motorKey: '', drive: 'sine', boardCap: '', rotors: '4',
  kvMode: 'free', turns0: '', strip0: '', stripWidth: '', headroom: '15', turnsBelow: '3', turnsAbove: '5', coilTol: '0',
  chemistry: 'li-ion-21700', cells: '6', capacity: '16', usable: '85', battMass: '', vNom: '', vMin: '', vMax: '', whKg: '', rCell: '',
  dryMass: '3.5', payload: '1', dragArea: '0.05', maxD: '13', minD: '10', airTemp: '25', altitude: '0', avionics: '0',
  speedKmh: '70', bestSpeed: false, hoverShare: '0', twCont: '1.5', twPeak: '2', tipMach: '0.7', enduranceMode: 'hover',
  catalogue: true, own: true, pdMin: '0.25', pdMax: '0.6', pdStep: '0.05', chords: '1, 1.15', blades: '2',
  refine: true, topN: '1', pop: '12', generations: '6', seed: '1', objective: 'range_km',
};

export type BuildResult = { ok: true; body: Record<string, unknown> } | { ok: false; error: string; field: string };

function num(form: MissionForm, key: keyof MissionForm): { v: number } | { error: string; field: string } {
  const n = parseNumber(String(form[key]));
  return n === null ? { error: 'number', field: key } : { v: n };
}

function list(text: string): number[] | null {
  const parts = text.split(/[;,\s]+/).filter(Boolean).map((p) => parseNumber(p));
  return parts.length && parts.every((p): p is number => p !== null) ? (parts as number[]) : null;
}

/** The request body of POST /api/propellers/mission/run from the form: SI inside (km/h -> m/s, % -> fraction), an empty optional field
 *  left out (the server takes the preset / the model's value).  The first unreadable field is named. */
export function buildBody(form: MissionForm, motors: MotorEntry[]): BuildResult {
  const motor = motors.find((m) => motorKey(m) === form.motorKey);
  if (!motor) return { ok: false, error: 'motor', field: 'motorKey' };
  if (motor.kind === 'none') return { ok: false, error: 'motor', field: 'motorKey' };
  const body: Record<string, Record<string, unknown> | string> = { motor: {}, kv: {}, battery: {}, airframe: {}, flight: {}, candidates: {}, refine: {} };
  const put = (g: string, k: string, key: keyof MissionForm, scale = 1, optional = false): string | null => {
    if (optional && String(form[key]).trim() === '') return null;
    const r = num(form, key);
    if ('error' in r) return key;
    (body[g] as Record<string, unknown>)[k] = r.v * scale;
    return null;
  };
  const checks: Array<string | null> = [
    put('motor', 'rotors', 'rotors'), put('motor', 'board_cap_A', 'boardCap', 1, true),
    put('kv', 'turns0', 'turns0', 1, true), put('kv', 'strip_thickness0_mm', 'strip0', 1, true), put('kv', 'strip_width_mm', 'stripWidth', 1, true),
    put('kv', 'headroom', 'headroom', 0.01), put('kv', 'turns_below', 'turnsBelow'), put('kv', 'turns_above', 'turnsAbove'), put('kv', 'coil_height_tol', 'coilTol', 0.01),
    put('battery', 'cells_s', 'cells'), put('battery', 'capacity_ah', 'capacity'), put('battery', 'usable', 'usable', 0.01), put('battery', 'mass_kg', 'battMass', 1, true),
    put('battery', 'v_nom', 'vNom', 1, true), put('battery', 'v_min', 'vMin', 1, true), put('battery', 'v_max', 'vMax', 1, true),
    put('battery', 'wh_per_kg', 'whKg', 1, true), put('battery', 'r_cell_ohm_ah', 'rCell', 1, true),
    put('airframe', 'dry_mass_kg', 'dryMass'), put('airframe', 'payload_kg', 'payload'), put('airframe', 'drag_area_m2', 'dragArea'),
    put('airframe', 'max_diameter_in', 'maxD'), put('airframe', 'min_diameter_in', 'minD'), put('airframe', 'air_temp_c', 'airTemp'),
    put('airframe', 'altitude_m', 'altitude'), put('airframe', 'avionics_W', 'avionics'),
    put('flight', 'cruise_speed_ms', 'speedKmh', 1 / KMH), put('flight', 'hover_share_pct', 'hoverShare'), put('flight', 'tw_cont', 'twCont'),
    put('flight', 'tw_peak', 'twPeak'), put('flight', 'tip_mach', 'tipMach'),
    put('candidates', 'pd_min', 'pdMin'), put('candidates', 'pd_max', 'pdMax'), put('candidates', 'pd_step', 'pdStep'),
    put('refine', 'top_n', 'topN'), put('refine', 'pop', 'pop'), put('refine', 'generations', 'generations'), put('refine', 'seed', 'seed'),
  ];
  const bad = checks.find((c) => c !== null);
  if (bad) return { ok: false, error: 'number', field: bad };
  const chords = list(form.chords);
  if (!chords) return { ok: false, error: 'number', field: 'chords' };
  const blades = list(form.blades);
  if (!blades) return { ok: false, error: 'number', field: 'blades' };
  const m = body.motor as Record<string, unknown>;
  if (motor.id) m.card_id = motor.id; else { m.die = motor.die; m.config = motor.config; }
  m.drive = form.drive || 'sine';
  Object.assign(body.kv as object, { mode: form.kvMode });
  Object.assign(body.battery as object, { chemistry: form.chemistry });
  Object.assign(body.flight as object, { best_speed: form.bestSpeed, endurance_mode: form.enduranceMode });
  Object.assign(body.candidates as object, { catalogue: form.catalogue, own: form.own, chords, blades });
  Object.assign(body.refine as object, { enabled: form.refine });
  body.objective = form.objective;
  return { ok: true, body };
}

export interface Point {
  V_ms: number; thrust_N: number; rpm: number; torque_Nm: number; current_A: number | null; pack_power_W: number | null; motor_eta: number | null;
  T_winding_C: number | null; tip_mach: number; tilt_deg: number | null;
}
export interface Failure { rule: string; detail: string }
export interface CandidateResult {
  id: string; name: string; source: 'own' | 'catalogue'; prop_id?: string | null; D_in: number; pitch_ratio: number | null; pitch_in?: number | null;
  chord_scale: number | null; blades: number | null; flags?: string[]; turns?: number; strip?: { thickness_mm: number; width_mm: number; stock_kg: number | null; check: boolean; low_stock: boolean };
  as_built?: boolean; kv_rpm_per_V?: number | null; objectives: Partial<Record<Objective, number | null>>; objective_value: number | null;
  tw_cont?: number; tw_peak?: number; points?: { hover: Point | null; cruise: Point | null }; failures: Failure[]; passed: boolean;
}
export interface Alternative { turns: number; kv_rpm_per_V: number | null; objectives: CandidateResult['objectives']; tw_cont?: number; tw_peak?: number; passed?: boolean }
export interface Refined {
  from_id: string; from_name: string; seed_vector: Record<string, number | string>; seed_value: number | null; best_vector: Record<string, number | string> | null;
  best_value: number | null; gain_pct: number | null; result: CandidateResult | null; n_evaluations: number; n_feasible: number;
  settings: { pop: number; generations: number; seed: number; free: string[] };
}
export interface MissionResult {
  results: CandidateResult[]; alternatives: Record<string, Alternative[]>; refined: Refined[]; note: string; stopped: boolean;
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
    strip: s ? `${s.thickness_mm} x ${s.width_mm}` : '', kv: r.kv_rpm_per_V ?? null, objective: r.objective_value,
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
