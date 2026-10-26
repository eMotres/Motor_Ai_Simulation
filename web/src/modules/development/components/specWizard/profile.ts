/** The wizard adapts to the project (owner 2026-10-27): the application, the mode and the load type decide which default
 *  operating points are offered, what the derived value is called (shaft power of a motor, power output of a generator),
 *  how the supply step is worded and which airflow presets the cooling offers.  Pure functions, tested in node; the schema
 *  and the field ids are never changed - only wording, defaults and what is offered change. */
import { CONTINUOUS, blankKeyPoint, type KeyPoint } from '../../keyPoints.ts';
import { loadGroup } from '../../specChoices.ts';
import type { SpecDraft } from '../../wizardState.ts';

export type WizardProfile = 'aviation' | 'industrial' | 'generator';

const text = (draft: SpecDraft, id: string): string => {
  const raw = draft.fields?.[id] as { status?: string; value?: unknown } | undefined;
  if (!raw || typeof raw !== 'object' || raw.status === 'not_known_yet') return '';
  return typeof raw.value === 'string' ? raw.value.toLowerCase() : '';
};
const GENERATOR_WORDS = /generator|turbine|wind|hydro|alternator/;
const AVIATION_WORDS = /aircraft|evtol|drone|uav|uas|aviation|propeller|multicopter|helicopter|vtol/;

/** generator = a generator mode (1_2), a turbine / engine-driven load, or a generator application (1_1);
 *  aviation = a propeller or aircraft / drone application; otherwise industrial (robot, vehicle, marine, industrial drive,
 *  and a project that has not said yet). */
export function wizardProfile(draft: SpecDraft): WizardProfile {
  const application = text(draft, '1_1'), mode = text(draft, '1_2'), load = text(draft, 'load_type');
  const group = loadGroup(load);
  if (mode.startsWith('generator') || load === 'engine_driven_generator' || group === 'turbine' || GENERATOR_WORDS.test(application)) return 'generator';
  if (AVIATION_WORDS.test(application) || group === 'air_propeller') return 'aviation';
  return 'industrial';
}

/** The default rows of a profile: a key (the i18n name `wizard.op.default.<key>`) and whether the point is continuous. */
export const PROFILE_DEFAULTS: Record<WizardProfile, Array<{ key: string; continuous?: boolean }>> = {
  aviation: [{ key: 'takeoff' }, { key: 'cruise' }, { key: 'landing' }, { key: 'peak' }],
  industrial: [{ key: 's1', continuous: true }, { key: 'peak2' }],
  generator: [{ key: 'cutIn' }, { key: 'rated', continuous: true }, { key: 'peak' }],
};
/** English names, so a project whose defaults were stored in the other language still counts as untouched. */
const EN_NAMES: Record<string, string> = { takeoff: 'Takeoff', cruise: 'Cruise', landing: 'Landing', peak: 'Peak', s1: 'Continuous (S1)', peak2: 'Peak (S2)', cutIn: 'Cut-in', rated: 'Rated (continuous)' };
const LEGACY_EN = ['Continuous S1'];

export function profileDefaultRows(profile: WizardProfile, nameOf: (key: string) => string): KeyPoint[] {
  return PROFILE_DEFAULTS[profile].map((d) => ({ ...blankKeyPoint(nameOf(d.key)), row_id: `default-${profile}-${d.key}`, duration_s: d.continuous ? CONTINUOUS : null }));
}

const norm = (s: string) => s.trim().toLowerCase();
/** The rows are exactly one profile's default list (same names, in order, no numbers, the default durations): nothing the
 *  user typed, so the list may be replaced by the defaults of the current profile.  Any rename, added or removed row, number
 *  or changed duration makes the list the user's own and it is never touched. */
export function isUntouchedDefaults(rows: KeyPoint[], nameOf: (key: string) => string): boolean {
  if (!rows.length) return false;
  if (rows.some((r) => r.torque_Nm !== null && r.torque_Nm !== undefined || r.speed_rpm !== null && r.speed_rpm !== undefined || r.voltage_V !== null && r.voltage_V !== undefined)) return false;
  const profiles = Object.keys(PROFILE_DEFAULTS) as WizardProfile[];
  return profiles.some((p) => {
    const defs = PROFILE_DEFAULTS[p];
    if (defs.length !== rows.length) return false;
    return defs.every((d, i) => {
      const names = [nameOf(d.key), EN_NAMES[d.key], ...(d.key === 's1' ? LEGACY_EN : [])].map(norm);
      const durationOk = d.continuous ? rows[i].duration_s === CONTINUOUS : (rows[i].duration_s ?? null) === null;
      return names.includes(norm(rows[i].name ?? '')) && durationOk;
    });
  });
}

/** Airflow presets the wizard hides for a load unless the stored mode is already that one: a turbine (wind or water) and a
 *  water propeller do not blow propeller wash over the housing, and ram air is not a cooling source of a turbine drive
 *  (owner 2026-10-27).  The shared load / cooling matrix (specChoices) is unchanged. */
export function hiddenAirflowModes(loadType: string | undefined, storedMode?: unknown): string[] {
  const group = loadGroup(loadType);
  if (group !== 'turbine' && group !== 'marine_propeller') return [];
  const stored = typeof storedMode === 'string' ? storedMode.trim().toLowerCase() : '';
  return ['Propeller wash', 'Ram air'].filter((m) => m.toLowerCase() !== stored);
}
/** The airflow mode a load brings when none is stored, after the hidden presets: a turbine drive starts at still air. */
export function wizardNaturalAirflow(loadType: string | undefined, natural: string | null): string | null {
  return natural && hiddenAirflowModes(loadType).includes(natural) ? 'Still air' : natural;
}
