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
