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
  if (present(v.airflow) || /\bair|风冷|空气/.test(type)) return inferred('air');
  if (COOLING_PROPERTIES.liquid.some((id) => present(v[id]))) return inferred('liquid');
  if (COOLING_PROPERTIES.submerged.some((id) => present(v[id])) || /water|submerg|immers|水下|浸没|水冷/.test(type)) return /glycol|coolant|jacket|乙二醇|冷却液/.test(type) ? inferred('liquid') : inferred('submerged');
  if (/liquid|coolant|glycol|oil|液冷|冷却液|油冷/.test(type)) return inferred('liquid');
  return { medium: null, inferred: false };
}

/** The load switch: air propeller, water propeller, shaft, plus any other stored type (kept, shown selected). */
export const LOAD_SWITCH: LoadType[] = ['air_propeller', 'marine_propeller', 'shaft'];
export function loadSwitchChoices(stored: string | undefined): LoadType[] {
  return stored && stored !== 'unknown' && !LOAD_SWITCH.includes(stored as LoadType) ? [...LOAD_SWITCH, stored as LoadType] : LOAD_SWITCH;
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
  const propeller = loadType === 'air_propeller' || loadType === 'marine_propeller';
  const own = propeller ? ['reverse_thrust_N', 'gyroscopic_moment_Nm', 'tilt_rate'] : loadType && loadType !== 'unknown' ? ['radial_load_N', 'bending_moment_Nm'] : [];
  const first = ['axial_thrust_N', ...own].filter((id) => propertyIds.includes(id));
  return [...first, ...propertyIds.filter((id) => !first.includes(id) && present(v[id]))];
}
