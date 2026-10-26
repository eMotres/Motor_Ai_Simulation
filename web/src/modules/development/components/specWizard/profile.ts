/** The wizard adapts to the project (owner 2026-10-27): the mode, the load type and the application decide which default
 *  operating points are offered, what the derived value is called (shaft power of a motor, shaft input power of a generator),
 *  whether the generator output window is asked and which airflow presets the cooling offers.  Pure functions, tested in node;
 *  the schema and the field ids never change - only wording, defaults and what is offered change.  Stored answers are never
 *  relabelled, converted or renamed because of the profile.
 *
 *  Precedence (review F04): the explicit structured choices first - the mode (1_2), then the load type - and the free
 *  application text (1_1) only as the fallback, recognised by whole words / bounded terms in English and Chinese.  A marine,
 *  shaft or ground-vehicle context is never aviation. */
import { CONTINUOUS, blankKeyPoint, type KeyPoint } from '../../keyPoints.ts';
import { loadGroup } from '../../specChoices.ts';
import type { SpecDraft } from '../../wizardState.ts';

export type WizardProfile = 'aviation' | 'industrial' | 'generator';

const text = (draft: SpecDraft, id: string): string => {
  const raw = draft.fields?.[id] as { status?: string; value?: unknown } | undefined;
  if (!raw || typeof raw !== 'object' || raw.status === 'not_known_yet') return '';
  return typeof raw.value === 'string' ? raw.value.trim().toLowerCase() : '';
};

// bounded terms: whole English words (so "window actuator", "winding machine", "hydrostatic drive" match nothing) and
// specific Chinese terms (CJK has no word boundary, so only whole terms that name the machine are listed)
const GENERATOR_TEXT = /(?<![a-z])(generators?|alternators?|turbogenerators?|wind turbines?|hydro turbines?|water turbines?|windmills?)(?![a-z])|发电机|发电|风力机|风力|风电|水轮机|水力/;
const AVIATION_TEXT = /(?<![a-z])(aircraft|aeroplanes?|airplanes?|evtol|vtol|drones?|uavs?|uas|multicopters?|quadcopters?|helicopters?|aviation|airframe|unmanned|aerial)(?![a-z])|无人机|飞行器|飞机|直升机|多旋翼|航空|垂直起降/;
const NOT_AIR_TEXT = /(?<![a-z])(marine|boat|ship|vessel|submarine|underwater|subsea|thruster|waterjet|water-jet|pump|wheel|robot|gearbox|shaft)(?![a-z])|船|舰|水下|推进器|泵|机器人|减速机|齿轮箱/;

/** Mode and load first, then the application text.  Contradictions: a stated mode beats the load, the load beats the text. */
export function wizardProfile(draft: SpecDraft): WizardProfile {
  const mode = text(draft, '1_2'), load = text(draft, 'load_type'), application = text(draft, '1_1');
  const group = loadGroup(load);
  const motorDecided = /^motor/.test(mode);
  if (/^generator/.test(mode)) return 'generator';
  if (!motorDecided && (load === 'engine_driven_generator' || group === 'turbine')) return 'generator';
  if (load === 'air_propeller' || group === 'air_propeller') return 'aviation';
  if (load && load !== 'unknown') return 'industrial';          // another structured load (marine, wheel, shaft, robot joint ...) decides
  if (!motorDecided && GENERATOR_TEXT.test(application)) return 'generator';
  if (AVIATION_TEXT.test(application) && !NOT_AIR_TEXT.test(application)) return 'aviation';
  return 'industrial';
}

/** The default rows of a profile: a key (the i18n name `wizard.op.default.<key>`) and whether the point is continuous. */
export const PROFILE_DEFAULTS: Record<WizardProfile, Array<{ key: string; continuous?: boolean }>> = {
  aviation: [{ key: 'takeoff' }, { key: 'cruise' }, { key: 'landing' }, { key: 'peak' }],
  industrial: [{ key: 's1', continuous: true }, { key: 'peak2' }],
  generator: [{ key: 'cutIn' }, { key: 'rated', continuous: true }, { key: 'peak' }],
};
/** Canonical names in both languages (kept in step with the locale files by a test): a default is recognised by its generated
 *  id AND these names, whatever language the page is in now. */
export const DEFAULT_NAMES: Record<string, string[]> = {
  takeoff: ['Takeoff', '起飞'], cruise: ['Cruise', '巡航'], landing: ['Landing', '着陆'], peak: ['Peak', '峰值'],
  s1: ['Continuous (S1)', '连续 (S1)', 'Continuous S1', '连续 S1'], peak2: ['Peak (S2)', '峰值 (S2)'],
  cutIn: ['Cut-in', '切入'], rated: ['Rated (continuous)', '额定（连续）'],
};

/** Generated default rows carry this id prefix; any other id belongs to the user (or an older build) and is never replaced. */
export const DEFAULT_ID_PREFIX = 'wizard-default-';
export const defaultRowId = (profile: WizardProfile, key: string) => `${DEFAULT_ID_PREFIX}${profile}-${key}`;
/** The four rows the version before the wizard generated (propulsion points) - recognised explicitly as generated defaults. */
const LEGACY_DEFAULT_IDS = ['default-0', 'default-1', 'default-2', 'default-3'];

export function profileDefaultRows(profile: WizardProfile, nameOf: (key: string) => string): KeyPoint[] {
  return PROFILE_DEFAULTS[profile].map((d) => ({ ...blankKeyPoint(nameOf(d.key)), row_id: defaultRowId(profile, d.key), duration_s: d.continuous ? CONTINUOUS : null }));
}

const norm = (s: string) => s.trim().toLowerCase();
/** The rows are exactly one generated default list: every id is a generated default id (or a known legacy default id) at the
 *  right position, the names are the canonical default names, no numbers, the default durations.  A row id the user's own
 *  edits created is never recognised - however its content looks - so a user-built list is never renamed or replaced. */
export function isUntouchedDefaults(rows: KeyPoint[], nameOf: (key: string) => string = () => ''): boolean {
  if (!rows.length) return false;
  if (rows.some((r) => (r.torque_Nm ?? null) !== null || (r.speed_rpm ?? null) !== null || (r.voltage_V ?? null) !== null)) return false;
  const profiles = Object.keys(PROFILE_DEFAULTS) as WizardProfile[];
  return profiles.some((p) => {
    const defs = PROFILE_DEFAULTS[p];
    if (defs.length !== rows.length) return false;
    return defs.every((d, i) => {
      const row = rows[i];
      const idOk = row.row_id === defaultRowId(p, d.key) || (p === 'aviation' && row.row_id === LEGACY_DEFAULT_IDS[i]);
      const names = [nameOf(d.key), ...(DEFAULT_NAMES[d.key] ?? [])].filter(Boolean).map(norm);
      const durationOk = d.continuous ? row.duration_s === CONTINUOUS : (row.duration_s ?? null) === null;
      return idOk && names.includes(norm(row.name ?? '')) && durationOk;
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
