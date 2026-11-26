// Saved gearboxes: the pure logic of the save / open panel (web tests import this file).
import type { BoxFlag, BoxList, BoxRecord, BoxSummary, PlainBin, PlainGear, PlainStage, RequestRecord, StageKind } from './types';
import type { EditorState } from './plain';

export const BOX_FLAGS: readonly BoxFlag[] = ['request_hash_mismatch', 'request_invalid', 'checksum_mismatch', 'core_stale', 'catalogue_changed'];

/** A save that lost against a newer stored version (409 stale_version). */
export const isStaleVersion = (e: unknown): boolean => !!e && typeof e === 'object' && (e as { status?: unknown }).status === 409 && (e as { code?: unknown }).code === 'stale_version';

/** The list as shown: active ones, archived ones only behind the toggle; unreadable files are counted, never listed as records. */
export function visibleBoxes(list: BoxList | null, showArchived: boolean): { items: BoxSummary[]; archived: number; unreadable: number } {
  const all = list?.items ?? [];
  return { items: all.filter((b) => showArchived || b.state === 'active'), archived: all.filter((b) => b.state === 'archived').length, unreadable: list?.unreadable?.length ?? 0 };
}

/** Flags to show as chips: the record's own flags (known ones first, unknown kept), plus the stale-response chip. */
export function boxChips(b: Pick<BoxSummary, 'flags' | 'response_stale'>): string[] {
  const flags = [...(b.flags ?? [])];
  const known = BOX_FLAGS.filter((f) => flags.includes(f));
  const other = flags.filter((f) => !(BOX_FLAGS as readonly string[]).includes(f));
  return [...(b.response_stale ? ['response_stale'] : []), ...known, ...other];
}

/** Open a record: its saved response is shown only when the server does not call it stale and it belongs to a point / spectrum. */
export function responseToShow(rec: Pick<BoxRecord, 'response' | 'response_stale'>): BoxRecord['response'] {
  const r = rec.response;
  if (!r || rec.response_stale) return null;
  return r.operation === 'point' || r.operation === 'spectrum' ? r : null;
}

export class EditorFromRequestError extends Error {
  path: string;
  constructor(path: string, message: string) { super(message); this.path = path; }
}

const num = (v: unknown, fallback: number): number => (typeof v === 'number' && Number.isFinite(v) ? v : fallback);
const KINDS: Record<string, StageKind> = { planetary: 'planetary', compound_planetary: 'compound_planetary', spur_pair: 'pair' };

function plainGear(path: string, g: unknown): PlainGear {
  const r = g as Record<string, unknown> | null;
  if (!r || typeof r.z !== 'number' || typeof r.m_n_mm !== 'number' || typeof r.b_mm !== 'number') throw new EditorFromRequestError(path, 'gear is incomplete');
  const out: PlainGear = { z: r.z, m_n_mm: r.m_n_mm, b_mm: r.b_mm };
  if (typeof r.x === 'number' && r.x !== 0) out.x = r.x;
  if (typeof r.beta_deg === 'number' && r.beta_deg !== 0) out.beta_deg = r.beta_deg;
  if (typeof r.tooth_thickness_allowance_mm === 'number') out.tooth_thickness_allowance_mm = r.tooth_thickness_allowance_mm;
  if (typeof r.material_name === 'string') out.material_name = r.material_name;
  return out;
}

const SLOTS = ['planet_bearing', 'input_bearing', 'output_bearing', 'bearing_1a', 'bearing_1b', 'bearing_2a', 'bearing_2b'];

/** The editor state of a saved canonical request (what the plain editor can show; anything it cannot show is refused loudly). */
export function editorFromRequest(req: RequestRecord): EditorState {
  const r = req as Record<string, unknown>;
  const stagesIn = r.stages;
  if (!Array.isArray(stagesIn) || stagesIn.length === 0) throw new EditorFromRequestError('/stages', 'no stages');
  const stages: PlainStage[] = stagesIn.map((raw, i) => {
    const s = raw as Record<string, unknown>;
    const kind = KINDS[String(s.kind)];
    const path = `/stages/${i}`;
    if (!kind) throw new EditorFromRequestError(`${path}/kind`, `stage kind ${String(s.kind)} is not editable here`);
    const roles = kind === 'pair' ? ['gear1', 'gear2'] : kind === 'compound_planetary' ? ['sun', 'planet', 'planet2', 'ring'] : ['sun', 'planet', 'ring'];
    const gears: PlainStage['gears'] = {};
    for (const role of roles) gears[role as keyof PlainStage['gears']] = plainGear(`${path}/${role}`, s[role]);
    const bearings: Record<string, string> = {};
    for (const slot of SLOTS) {
      const b = s[slot] as Record<string, unknown> | null | undefined;
      if (b && typeof b === 'object') bearings[slot] = String(b.card_name ?? b.name ?? '');
    }
    const stage: PlainStage = { uid: String(s.stage_id), kind, name: String(s.name), gears, bearings };
    if (kind !== 'pair') { stage.n_planets = num(s.n_planets, 3); stage.fixed_member = String(s.fixed_member ?? 'ring'); }
    for (const k of ['f_ma_um', 'sun_planet_f_ma_um', 'planet_ring_f_ma_um'] as const) {   // the optional misalignment inputs ride along (absent stays absent)
      const v = s[k];
      if (typeof v === 'number' && Number.isFinite(v) && v >= 0) stage[k] = v;
    }
    return stage;
  });
  const sp = r.spectrum as Record<string, unknown> | null;
  const bins: PlainBin[] = Array.isArray(sp?.bins)
    ? (sp!.bins as Array<Record<string, unknown>>).map((b) => ({ torque_out_Nm: num(b.torque_out_Nm, 0), speed_out_rpm: num(b.speed_out_rpm, 0), duration_h: num(b.duration_h, 1), ambient_temp_C: num(b.ambient_temp_C, 25), label: String(b.label ?? '') }))
    : [{ torque_out_Nm: 9.8, speed_out_rpm: 247, duration_h: 6000, ambient_temp_C: 25, label: '' }, { torque_out_Nm: 4.9, speed_out_rpm: 300, duration_h: 4000, ambient_temp_C: 25, label: '' }];
  return {
    name: String(r.name ?? 'gearbox'), preset_name: String(r.preset_name ?? 'standard'),
    lubricant: typeof r.lubricant === 'string' ? r.lubricant : 'ROBOT_GREASE_NLGI1_PAO',
    input_speed_rpm: num(r.input_speed_rpm, 1000), torque_Nm: num(r.torque_Nm, 1), torque_at: r.torque_at === 'output' ? 'output' : 'input',
    power_direction: r.power_direction === 'reverse' ? 'reverse' : 'forward', temp_C: num(r.temp_C, 70), stages,
    spectrum: { bins, peak_torque_out_Nm: num(sp?.peak_torque_out_Nm, 0), peak_events: num(sp?.peak_events, 1), peak_speed_out_rpm: num(sp?.peak_speed_out_rpm, 0) },
  };
}

export interface SavedRef { id: string; version: number; project: string; name: string }
export const validateSavedRef = (raw: unknown): { ref: SavedRef | null; project: string; name: string; showArchived: boolean } | null => {
  const r = raw as Record<string, unknown> | null;
  if (!r || typeof r !== 'object') return null;
  const project = typeof r.project === 'string' ? r.project : '';
  const ref = r.ref && typeof r.ref === 'object' && typeof (r.ref as SavedRef).id === 'string' && typeof (r.ref as SavedRef).version === 'number' ? r.ref as SavedRef : null;
  return { ref, project, name: typeof r.name === 'string' ? r.name : '', showArchived: r.showArchived === true };
};
