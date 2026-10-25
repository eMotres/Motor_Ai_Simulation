import type { LoadDraft, LoadType } from './missionProfileState';
import type { SchemaSuggestion } from './types';

/** The specification's switches and menus (owner 2026-10-25): every standard choice is a menu, cooling and the
 *  load are switches, and every value stays one number. Pure helpers so the page and the tests share them. */

export type Suggestion = SchemaSuggestion;
export interface SuggestionOption { value: string | number; label: string; labels: string[] }

const localized = (v: unknown, lang: string): string => typeof v === 'string' ? v : v && typeof v === 'object' ? String((v as Record<string, unknown>)[lang === 'zh-CN' ? 'zh-CN' : 'en'] ?? (v as Record<string, unknown>).en ?? '') : '';
const blank = (v: unknown) => v === undefined || v === null || (typeof v === 'string' && !v.trim());

/** The schema's UI-only `suggestions` as menu entries: the label in the page language, the EN value stored. */
export function suggestionOptions(list: Suggestion[] | undefined, lang: string): SuggestionOption[] {
  return (list ?? []).map((s) => {
    if (typeof s === 'number' || typeof s === 'string') return { value: s, label: String(s), labels: [String(s)] };
    const labels = s.label && typeof s.label === 'object' ? Object.values(s.label).filter((x): x is string => typeof x === 'string') : typeof s.label === 'string' ? [s.label] : [];
    return { value: s.value, label: localized(s.label, lang) || String(s.value), labels: [String(s.value), ...labels] };
  });
}

/** What the field shows for a stored value: a suggestion's localized label, any other text as stored. */
export function suggestionDisplay(list: Suggestion[] | undefined, stored: unknown, lang: string): string {
  if (blank(stored)) return '';
  const match = suggestionOptions(list, lang).find((o) => String(o.value) === String(stored));
  return match ? match.label : typeof stored === 'object' ? '' : String(stored);
}

/** What typing or picking `text` stores: a suggestion (by its label in either language, or its value) stores its
 *  value; any other text is kept as typed. A numeric field stores a number, '' when cleared, and rejects
 *  (`undefined`) text that is not a number. */
export function suggestionValue(list: Suggestion[] | undefined, text: string, numeric = false): string | number | undefined {
  const trimmed = text.trim();
  if (!trimmed) return '';
  const match = suggestionOptions(list, 'en').find((o) => o.labels.includes(trimmed));
  if (match) return numeric ? Number(match.value) : match.value;
  if (!numeric) return text;
  const n = Number(trimmed);
  return Number.isFinite(n) ? n : undefined;
}

export type CoolingMedium = 'air' | 'liquid' | 'submerged';
export const COOLING_MEDIA: CoolingMedium[] = ['air', 'liquid', 'submerged'];
/** Each medium shows only its own conditions; the other media's values stay stored. */
export const COOLING_PROPERTIES: Record<CoolingMedium, string[]> = {
  air: ['airflow'],
  liquid: ['coolant', 'inlet_temperature_C', 'flow_rate', 'pressure_drop_Pa'],
  submerged: ['water_temperature_C', 'water_speed_mps', 'depth_m'],
};

const present = (v: unknown): boolean => v && typeof v === 'object' && !Array.isArray(v) ? Object.values(v as Record<string, unknown>).some(present) : !blank(v);

/** The cooling medium of a 3_1 answer: the stored `cooling_medium`, else the one its stored values imply
 *  (shown, never written until the user clicks), else null. */
export function coolingMedium(value: unknown): { medium: CoolingMedium | null; inferred: boolean } {
  const v = value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
  if (COOLING_MEDIA.includes(v.cooling_medium as CoolingMedium)) return { medium: v.cooling_medium as CoolingMedium, inferred: false };
  const type = typeof v.cooling_type === 'string' ? v.cooling_type.toLowerCase() : '';
  const inferred = (medium: CoolingMedium) => ({ medium, inferred: true });
  // sea water around a pod is underwater cooling even when written as a "coolant"; a jacket / glycol / PPG loop is
  // liquid; a coolant circuit is never hidden by an airflow that also blows over the housing (Astra review 2026-10-25)
  const text = `${type} ${typeof v.coolant === 'string' ? v.coolant.toLowerCase() : ''}`
    .replace(/\b(no|without|not an?)\s+(internal\s+)?(coolant\s+|liquid\s+|cooling\s+)?(loop|circuit|jacket)s?\b/g, ' ');
  const circuit = /glycol|jacket|ppg|loop|乙二醇|冷却液|回路/.test(text);
  if (/submerg|immers|underwater|sea ?water|水下|浸没|海水/.test(text) && !circuit) return inferred('submerged');
  // a coolant, a flow or a pressure drop is a circuit; an inlet temperature alone may be the air's
  if (['coolant', 'flow_rate', 'pressure_drop_Pa'].some((id) => present(v[id])) || circuit) return inferred('liquid');
  if (present(v.airflow) || /\bair|风冷|空气/.test(type)) return inferred('air');
  if (present(v.inlet_temperature_C)) return inferred('liquid');
  if (COOLING_PROPERTIES.submerged.some((id) => present(v[id])) || /water|submerg|immers|水下|浸没|水冷/.test(type)) return /glycol|coolant|jacket|乙二醇|冷却液/.test(type) ? inferred('liquid') : inferred('submerged');
  if (/liquid|coolant|glycol|oil|液冷|冷却液|油冷/.test(type)) return inferred('liquid');
  return { medium: null, inferred: false };
}

/** The temperature every thermal calculation starts from (owner 2026-10-26): the maximum ambient temperature (3_2) for
 *  air cooling, the maximum coolant inlet temperature for liquid cooling, the maximum water temperature underwater.
 *  Not chosen yet reads as air (the default assumption).  `temperature_C` is null while that value is not stated.
 *  Any reader of the thermal basis goes through this - never straight to `temperature_max_C`. */
export type ThermalBasisSource = 'temperature_max_C' | 'inlet_temperature_C' | 'water_temperature_C';
export function thermalBasis(cooling: unknown, ambient: unknown): { medium: CoolingMedium; source: ThermalBasisSource; temperature_C: number | null } {
  const medium = coolingMedium(cooling).medium ?? 'air';
  const source: ThermalBasisSource = medium === 'liquid' ? 'inlet_temperature_C' : medium === 'submerged' ? 'water_temperature_C' : 'temperature_max_C';
  const from = (v: unknown): Record<string, unknown> => v && typeof v === 'object' && !Array.isArray(v) ? v as Record<string, unknown> : {};
  const raw = from(medium === 'air' ? ambient : cooling)[source];
  const n = typeof raw === 'number' ? raw : typeof raw === 'string' && raw.trim() ? Number(raw) : NaN;
  return { medium, source, temperature_C: Number.isFinite(n) ? n : null };
}

/** The load switch (owner 2026-10-25, one block with the cooling): what the motor drives.  The finer stored
 *  types belong to a button and are kept as stored: a water-jet is a water propeller; a pump, an engine
 *  driving a generator and a robot joint are a shaft. */
export const LOAD_SWITCH: LoadType[] = ['air_propeller', 'marine_propeller', 'wheel', 'turbine', 'shaft'];
const LOAD_GROUP: Partial<Record<string, LoadType>> = { water_jet: 'marine_propeller', pump: 'shaft', engine_driven_generator: 'shaft', robot_joint: 'shaft' };
/** The switch button a stored load type sits under (null: nothing chosen yet, or a type no button covers). */
export function loadGroup(stored: string | undefined): LoadType | null {
  if (!stored || stored === 'unknown') return null;
  return LOAD_SWITCH.includes(stored as LoadType) ? stored as LoadType : LOAD_GROUP[stored] ?? null;
}
export function loadSwitchChoices(stored: string | undefined): LoadType[] {
  return stored && stored !== 'unknown' && !loadGroup(stored) ? [...LOAD_SWITCH, stored as LoadType] : LOAD_SWITCH;
}

/** Which motor cooling fits which load.  Every medium fits every load except one: an air propeller never runs under
 *  water.  A wheel can (submerged crawlers), a turbine can (hydro), a water propeller usually does (pods, thrusters).
 *  The first listed is the load's natural medium.  Liquid keeps the outside conditions asked too: the air over the
 *  housing (propeller wash, ride, fan) or, for a water propeller, the water around the pod. */
export const LOAD_COOLING: Record<string, CoolingMedium[]> = {
  air_propeller: ['air', 'liquid'],
  marine_propeller: ['submerged', 'liquid', 'air'],
  wheel: ['air', 'liquid', 'submerged'],
  turbine: ['air', 'submerged', 'liquid'],
  shaft: ['air', 'liquid', 'submerged'],
};
export function coolingChoices(loadType: string | undefined): CoolingMedium[] {
  const group = loadGroup(loadType);
  return group ? LOAD_COOLING[group] : COOLING_MEDIA;
}
/** The air the motor sees, by load: propeller wash only behind an air propeller; ram air wherever the machine can move
 *  through the air (an aircraft, a vehicle, a wind turbine, a shaft drive on a vehicle); an inboard boat motor sees
 *  still air or a fan. */
const AIRFLOW_BY_LOAD: Record<string, string[]> = {
  air_propeller: ['Propeller wash', 'Ram air', 'Still air', 'Fan'],
  wheel: ['Ram air', 'Still air', 'Fan'],
  turbine: ['Ram air', 'Still air', 'Fan'],
  marine_propeller: ['Still air', 'Fan'],
  shaft: ['Still air', 'Fan', 'Ram air'],
};
export function airflowSuggestions(list: Suggestion[] | undefined, loadType: string | undefined): Suggestion[] | undefined {
  const group = loadGroup(loadType), allowed = group ? AIRFLOW_BY_LOAD[group] : null;
  const valueOf = (s: Suggestion) => typeof s === 'object' ? s.value : s;
  return list && allowed ? allowed.map((v) => list.find((s) => valueOf(s) === v)).filter((s): s is Suggestion => s !== undefined) : list;
}
/** The conditions shown for a medium under a load: a liquid-cooled motor still has an outside - the air over its
 *  housing (propeller wash, ride, wind, a fan), or for a water propeller the water around its pod - so those stay
 *  asked next to the coolant. */
export function coolingPropertyIds(medium: CoolingMedium, loadType: string | undefined): string[] {
  if (medium !== 'liquid') return COOLING_PROPERTIES[medium];
  return loadGroup(loadType) === 'marine_propeller' ? [...COOLING_PROPERTIES.liquid, ...COOLING_PROPERTIES.submerged] : [...COOLING_PROPERTIES.liquid, 'airflow'];
}
/** The airflow mode a medium switch brings under a load when none is stored (air propeller: propeller wash). */
export function naturalAirflow(medium: CoolingMedium, loadType: string | undefined): string | null {
  const group = loadGroup(loadType), natural = group ? DEFAULT_COOLING[group] : undefined;
  return natural?.airflow && coolingPropertyIds(medium, group ?? undefined).includes('airflow') ? natural.airflow : null;
}
const DEFAULT_COOLING: Record<string, { medium: CoolingMedium; airflow?: string }> = {
  air_propeller: { medium: 'air', airflow: 'Propeller wash' },
  marine_propeller: { medium: 'submerged' },
  wheel: { medium: 'air', airflow: 'Ram air' },
};
/** The cooling answer after a load is chosen: a cooling that does not fit the load (or none) takes the load's
 *  natural one (air propeller -> air, propeller wash; water propeller -> underwater; wheel -> air, ram air;
 *  otherwise the first that fits once a misfit must go); an airflow mode the load cannot make (propeller wash
 *  without an air propeller) takes the load's own.  Null when nothing changes. */
export function coolingForLoad(cooling: unknown, loadType: string): Record<string, unknown> | null {
  const v = cooling && typeof cooling === 'object' && !Array.isArray(cooling) ? cooling as Record<string, unknown> : {};
  const group = loadGroup(loadType);
  if (!group) return null;
  const { medium } = coolingMedium(v), allowed = LOAD_COOLING[group], natural = DEFAULT_COOLING[group];
  const target: CoolingMedium | null = medium && allowed.includes(medium) ? medium : natural?.medium ?? (medium ? allowed[0] : null);
  if (!target) return null;
  const airflow = v.airflow && typeof v.airflow === 'object' && !Array.isArray(v.airflow) ? v.airflow as Record<string, unknown> : {};
  const shown = coolingPropertyIds(target, group).includes('airflow');
  const mode = typeof airflow.mode === 'string' ? airflow.mode : '';
  const modes = AIRFLOW_BY_LOAD[group];
  const nextMode = !shown ? null : blank(mode) ? (natural?.airflow && (target === 'air' || group === 'air_propeller') ? natural.airflow : null)
    : modes.some((m) => m.toLowerCase() === mode.trim().toLowerCase()) || !Object.values(AIRFLOW_BY_LOAD).flat().some((m) => m.toLowerCase() === mode.trim().toLowerCase())
      ? null : natural?.airflow ?? modes[0];
  if (target === v.cooling_medium && !nextMode) return null;
  return { ...v, cooling_medium: target, ...(nextMode ? { airflow: { ...airflow, mode: nextMode } } : {}) };
}

/** A new load type through the load adapter, with the same reset LoadSection does: the curve source and the
 *  characteristic are asked again (their stored values are kept by `writeLoadDraft`). */
export function withLoadType(load: LoadDraft, type: LoadType): LoadDraft {
  if (load.load_type === type && load.answer_status?.load_type === 'filled') return load;
  return { ...load, load_type: type, source: 'unknown', series: undefined, answer_status: { ...(load.answer_status ?? {}), load_type: 'filled', load_source: 'missing', load_characteristic: 'missing' } };
}

/** The 4_6 properties shown for a load type: the axial load first (always), the propeller or shaft loads, and any
 *  other property only while it holds a value. */
export function loadPropertyIds(loadType: string | undefined, propertyIds: string[], value: unknown): string[] {
  const v = value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
  const group = loadGroup(loadType), propeller = group === 'air_propeller' || group === 'marine_propeller';
  const own = propeller ? ['reverse_thrust_N', 'gyroscopic_moment_Nm', 'tilt_rate'] : loadType && loadType !== 'unknown' ? ['radial_load_N', 'bending_moment_Nm'] : [];
  const first = ['axial_thrust_N', ...own].filter((id) => propertyIds.includes(id));
  return [...first, ...propertyIds.filter((id) => !first.includes(id) && present(v[id]))];
}
