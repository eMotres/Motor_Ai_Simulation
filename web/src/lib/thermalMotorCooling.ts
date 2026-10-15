/**
 * The Thermal cooling fields belong to the MOTOR (2026-10-15).
 *
 * Pure helpers - no store, no fetch - so the rules are tested with plain node:
 *
 *   - `parseMotorCooling`  - what `PUT /api/me/last_motor` answers about the
 *     fields of the motor just loaded (`thermal_settings.follow_loaded_motor`);
 *   - `splitMotorKey`      - the `"<die>|<config>"` identity the server stores
 *     the per-motor records under;
 *   - `coolingSourceLine`  - the one line near the cooling inputs;
 *   - `coolingMismatch`    - the loud "solved with ... fields now ..." warning.
 *
 * Why: the 200 mm liquid-cooled L155 was once solved with the open frame and
 * the 20 m/s bore air of the 40 mm propeller motor, because the fields of the
 * previous motor stayed in the panel.
 */

/** Where the fields a motor shows came from. */
export type CoolingSource = 'saved' | 'config' | 'application' | 'last_used';

export const COOLING_SOURCES: readonly CoolingSource[] = ['saved', 'config', 'application', 'last_used'];

/** The cooling fields the server moves with the motor (the panel's string state). */
export const COOLING_FIELD_KEYS = [
  'coolMode', 'ambientT', 'airSpeed', 'fluid', 'tIn', 'hConv', 'flowLpm',
  'boreMode', 'boreAirSpeed', 'boreFluid', 'boreTIn', 'boreFlowLpm',
  'shaftExtMm', 'shaftExtSides', 'frame', 'openAirSpeed',
  'emissivity', 'heatPath', 'endFaces', 'endFaceSides',
] as const;

export interface MotorIdentity { die: string; config: string }

export interface MotorCooling {
  /** every cooling field of the motor, as the panel keeps them (strings) */
  fields: Record<string, string>;
  source: CoolingSource;
  application: string | null;
}

export function splitMotorKey(key: unknown): MotorIdentity | null {
  const s = typeof key === 'string' ? key : '';
  const i = s.lastIndexOf('|');
  if (i <= 0 || i >= s.length - 1) return null;
  return { die: s.slice(0, i), config: s.slice(i + 1) };
}

export function isCoolingSource(v: unknown): v is CoolingSource {
  return typeof v === 'string' && (COOLING_SOURCES as readonly string[]).includes(v);
}

/** The motor's fields to adopt from the `thermal` answer of `PUT /api/me/last_motor`,
 *  or `null` when the answer carries none (the same motor was loaded again, or the
 *  server could not follow).  The server has ALREADY stored them: the caller adopts
 *  them without saving, so a default never turns into "your saved settings". */
export function parseMotorCooling(answer: unknown): MotorCooling | null {
  const a = answer as { fields?: unknown; source?: unknown; application?: unknown } | null | undefined;
  if (!a || typeof a !== 'object' || !a.fields || typeof a.fields !== 'object') return null;
  const fields: Record<string, string> = {};
  for (const [k, v] of Object.entries(a.fields as Record<string, unknown>)) {
    if (typeof v === 'string') fields[k] = v;
  }
  if (!Object.keys(fields).length) return null;
  return {
    fields,
    source: isCoolingSource(a.source) ? a.source : 'last_used',
    application: typeof a.application === 'string' && a.application ? a.application : null,
  };
}

/** The adoptable subset of `fields`: only the cooling keys the panel holds, and
 *  only values of the same type the store keeps (strings).  The propeller binding
 *  is handled by the caller. */
export function adoptableCoolingFields(fields: Record<string, string>): Record<string, string> {
  const out: Record<string, string> = {};
  for (const k of COOLING_FIELD_KEYS) {
    if (typeof fields[k] === 'string') out[k] = fields[k];
  }
  return out;
}

/** The `thermal` namespace key of each source's line (flat keys, as the file keeps them). */
export const COOLING_SOURCE_KEYS: Record<CoolingSource, string> = {
  saved: 'coolingSourceSaved',
  config: 'coolingSourceConfig',
  application: 'coolingSourceApplication',
  last_used: 'coolingSourceLastUsed',
};

/** i18n key + arguments of "Cooling of <die> <config>: <source>". */
export function coolingSourceLine(
  motor: MotorIdentity | null, source: CoolingSource | null, application: string | null,
): { key: string; args: { motor: string; app: string } } | null {
  if (!motor || !source) return null;
  return {
    key: COOLING_SOURCE_KEYS[source],
    args: { motor: `${motor.die} ${motor.config}`, app: application || '-' },
  };
}

export interface SolvedCooling {
  cooling?: { outer?: { mode?: string | null } | null; frame?: string | null } | null;
}

export interface CoolingNow {
  coolMode: string;
  airSpeedSource: string;
  frame: string;
}

/** The outer cooling mode / frame as the panel's fields would send them: a
 *  propeller source is `air`, anything but `open` is `housed`. */
export function fieldsMode(f: CoolingNow): { mode: string; frame: string } {
  return {
    mode: f.airSpeedSource === 'propeller' ? 'air' : String(f.coolMode || 'air'),
    frame: f.frame === 'open' ? 'open' : 'housed',
  };
}

/** `null` when the result was solved with the cooling mode and frame the fields
 *  hold now (or the result says nothing); else what differs, as "mode/frame"
 *  words for the warning. */
export function coolingMismatch(
  result: SolvedCooling | null | undefined, f: CoolingNow,
): { solved: string; now: string } | null {
  const solvedMode = result?.cooling?.outer?.mode;
  if (!solvedMode) return null;
  const solvedFrame = result?.cooling?.frame === 'open' ? 'open' : 'housed';
  const now = fieldsMode(f);
  if (solvedMode === now.mode && solvedFrame === now.frame) return null;
  return { solved: `${solvedMode}/${solvedFrame}`, now: `${now.mode}/${now.frame}` };
}

/** Every field the server moves with the motor, propeller binding included. */
export const MOTOR_OWNED_KEYS: readonly string[] = [
  ...COOLING_FIELD_KEYS, 'airSpeedSource', 'propellerId', 'propellerContextKey',
];

export function sameMotor(a: MotorIdentity | null | undefined, b: MotorIdentity | null | undefined): boolean {
  return !!a && !!b && a.die === b.die && a.config === b.config;
}

/** Why a solve must not use the panel's cooling fields, or `null`.
 *
 *  The fields are tagged with the motor they belong to (`fieldsMotor`).  With a
 *  motor loaded the tag must be KNOWN and be that motor: a solve of the loaded
 *  motor under the fields of ANOTHER one - a slow hydration, a follow that never
 *  arrived - or under fields nobody has yet verified (localStorage's snapshot
 *  while the server's answer is on its way, or after it failed) would solve one
 *  machine under another's boundary conditions, and the result-vs-fields warning
 *  cannot see it (both would read the same mode and frame).  Callers that let the
 *  backend resolve the motor's own record send nothing instead of the fields; the
 *  direct Thermal solve refuses.  With NO motor loaded there is nothing to be
 *  wrong about. */
export function ownershipIssue(
  fieldsMotor: MotorIdentity | null | undefined, loaded: MotorIdentity | null | undefined,
): string | null {
  if (!loaded) return null;
  if (!fieldsMotor) {
    return `the cooling of ${loaded.die} ${loaded.config} is not loaded yet - `
      + 'wait a moment, or reload the motor';
  }
  if (sameMotor(fieldsMotor, loaded)) return null;
  return `the cooling fields shown belong to ${fieldsMotor.die} ${fieldsMotor.config}, `
    + `not to the loaded ${loaded.die} ${loaded.config} - reload the motor to get its cooling`;
}

/** Should the answer of `PUT /api/me/last_motor` replace the panel's cooling
 *  fields?  Yes when it carries them AND the motor changed (or the server
 *  repaired the record), or the browser's own fields are not the loaded motor's -
 *  the recovery path of a lost answer: reloading the motor resynchronises even
 *  though the server had nothing left to change.  A reload of the same motor
 *  whose fields the browser already owns keeps the user's unsaved typing. */
export function shouldAdoptMotorCooling(
  answer: unknown, fieldsMotor: MotorIdentity | null | undefined,
  loaded: MotorIdentity | null | undefined,
): boolean {
  if (!parseMotorCooling(answer)) return false;
  const a = answer as { motor_changed?: unknown; changed?: unknown };
  return a.motor_changed === true || a.changed === true || !sameMotor(fieldsMotor, loaded);
}

/** A hydration response is stale when a motor change adopted fields while it was
 *  in flight (`epoch` moved) or the loaded motor is no longer the one it was
 *  asked for.  A stale response may still carry account-level fields, never
 *  cooling. */
export function hydrationIsStale(
  startEpoch: number, epochNow: number,
  startMotor: MotorIdentity | null, motorNow: MotorIdentity | null,
): boolean {
  if (startEpoch !== epochNow) return true;
  return (startMotor === null) !== (motorNow === null)
    || (startMotor !== null && !sameMotor(startMotor, motorNow));
}

/** `adopted` without any motor-owned field (a stale hydration's account-level rest). */
export function withoutMotorOwned<T extends Record<string, unknown>>(adopted: T): Partial<T> {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(adopted)) {
    if (!MOTOR_OWNED_KEYS.includes(k)) out[k] = v;
  }
  return out as Partial<T>;
}

// ── a motor's cooling is adopted COMPLETE, from the server, or not at all ────────

/** The fields that make a cooling description complete: every cooling key and the
 *  source.  A response lacking one leaves it to whatever the browser held before
 *  (another motor's localStorage snapshot), so it can not establish ownership. */
export const REQUIRED_COOLING_KEYS: readonly string[] = [...COOLING_FIELD_KEYS, 'airSpeedSource'];

export function isCompleteCooling(fields: Record<string, unknown> | null | undefined): boolean {
  if (!fields || typeof fields !== 'object') return false;
  return REQUIRED_COOLING_KEYS.every((k) => typeof fields[k] === 'string');
}

export interface FollowAdoption extends MotorCooling { motor: MotorIdentity }

/** The cooling to adopt from a `PUT /api/me/last_motor` answer, or `null`.
 *  It must (1) carry the motor's COMPLETE fields, (2) be an answer for the motor
 *  that is loaded NOW (a late answer for a previous one is dropped) and (3) be
 *  wanted (`shouldAdoptMotorCooling`).  Ownership is assigned from these fields
 *  only - never to browser-local values the answer did not replace. */
export function followAdoption(
  answer: unknown, fieldsMotor: MotorIdentity | null | undefined,
  loaded: MotorIdentity | null | undefined,
): FollowAdoption | null {
  const mc = parseMotorCooling(answer);
  if (!mc || !isCompleteCooling(mc.fields)) return null;
  const motor = splitMotorKey((answer as { motor?: unknown }).motor);
  if (!motor || !loaded || !sameMotor(motor, loaded)) return null;
  if (!shouldAdoptMotorCooling(answer, fieldsMotor, loaded)) return null;
  return { ...mc, motor };
}

export interface HydrationInput {
  settings?: Record<string, unknown> | null;
  motor?: string | null;
}

export interface HydrationPlan {
  /** adopt the server's cooling fields (complete, and bound to a motor) */
  adoptCooling: boolean;
  /** the motor those fields belong to - `null` = ownership stays UNVERIFIED */
  owner: MotorIdentity | null;
  /** ask the server to resolve the loaded motor's fields (a same-motor follow) */
  needsResync: boolean;
}

/** What a settings read establishes.  A failed read, an empty or sparse response,
 *  or one that names no motor establishes NOTHING: the browser's cooling fields
 *  stay unverified (a loaded motor's solve is held back) until a motor-specific
 *  resolution - the follow answer's complete `fields` - replaces them. */
export function hydrationPlan(
  srv: HydrationInput | null | undefined, loaded: MotorIdentity | null | undefined,
): HydrationPlan {
  const owner = splitMotorKey(srv?.motor);
  if (srv && owner && isCompleteCooling(srv.settings as Record<string, unknown>)) {
    return { adoptCooling: true, owner, needsResync: !!loaded && !sameMotor(owner, loaded) };
  }
  return { adoptCooling: false, owner: null, needsResync: !!loaded };
}
