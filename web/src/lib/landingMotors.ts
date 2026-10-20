// The landing page's public motor preview: the answer of GET /api/public/motors, and the
// "open this motor after I sign up" intent.  PURE (no React, no import.meta, no imports), so
// `node --test` loads it directly (src/components/landing/__tests__/landingMotors.test.mjs).
//
// WHAT THE SERVER MAY SEND is the allow-list in src/motor_ai_sim/public_motors.py; the parser
// below re-applies it, so a field added on the server tomorrow never reaches the page.

export interface PublicMotorRated {
  power_kw: number | null;
  torque_nm: number | null;
  speed_rpm: number | null;
  efficiency_pct: number | null;
  mass_kg: number | null;
  /** True when the full motor mass is not known yet and `mass_kg` is the active (EM parts) mass. */
  mass_is_partial: boolean;
  continuous: boolean;
}

export interface PublicMotor {
  die: string;
  configuration: string;
  /** 'propeller' | 'robot' | another application word, or null. */
  application: string | null;
  rated: PublicMotorRated;
}

const num = (v: unknown): number | null =>
  (typeof v === 'number' && Number.isFinite(v)) ? v : null;
const word = (v: unknown, max: number): string =>
  (typeof v === 'string' ? v : '').replace(/[^A-Za-z0-9 _.-]+/g, '').trim().slice(0, max);
/** A catalogue name (routes/family._check_name): Latin letters, digits, spaces, "-_.,()#+", up to 64. */
const NAME = /^[A-Za-z0-9][A-Za-z0-9 _.,()#+-]{0,63}$/;
const nameOf = (v: unknown): string => (typeof v === 'string' && NAME.test(v) ? v : '');

/** The motors of an API answer; anything malformed is dropped, unknown fields are ignored. */
export function parsePublicMotors(body: unknown): PublicMotor[] {
  const list = (body && typeof body === 'object') ? (body as { motors?: unknown }).motors : null;
  if (!Array.isArray(list)) return [];
  const out: PublicMotor[] = [];
  for (const raw of list.slice(0, 24)) {
    if (!raw || typeof raw !== 'object') continue;
    const r = raw as Record<string, unknown>;
    const die = nameOf(r.die);
    const configuration = nameOf(r.configuration);
    if (!die || !configuration) continue;
    const rated = (r.rated && typeof r.rated === 'object') ? r.rated as Record<string, unknown> : {};
    out.push({
      die, configuration,
      application: word(r.application, 24) || null,
      rated: {
        power_kw: num(rated.power_kw), torque_nm: num(rated.torque_nm),
        speed_rpm: num(rated.speed_rpm), efficiency_pct: num(rated.efficiency_pct),
        mass_kg: num(rated.mass_kg), mass_is_partial: rated.mass_is_partial === true,
        continuous: rated.continuous === true,
      },
    });
  }
  return out;
}

/** Significant digits, no trailing noise: 0.7400 -> "0.74", 5000 -> "5000", 12.345 -> "12.3". */
export function fmtNum(v: number | null, digits = 3): string {
  if (v === null || !Number.isFinite(v)) return '–';
  if (v === 0) return '0';
  const a = Math.abs(v);
  const dec = Math.max(0, digits - 1 - Math.floor(Math.log10(a)));
  return String(Number(v.toFixed(Math.min(dec, 4))));
}

/** The headline rows of a card, in display order: key (a `landing:motors.*` label key), value, unit. */
export function headlineRows(m: PublicMotor): Array<{ key: string; value: string; unit: string }> {
  const r = m.rated;
  return [
    { key: 'power', value: fmtNum(r.power_kw), unit: 'kW' },
    { key: 'torque', value: fmtNum(r.torque_nm), unit: 'N·m' },
    { key: 'speed', value: r.speed_rpm === null ? '–' : String(Math.round(r.speed_rpm)), unit: 'rpm' },
    { key: 'mass', value: fmtNum(r.mass_kg), unit: 'kg' },
    { key: 'efficiency', value: fmtNum(r.efficiency_pct, 3), unit: '%' },
  ];
}

// ── the intent: "open this motor in Configure after I sign up" ─────────────────────────────────

export const LANDING_MOTOR_KEY = 'landing.openMotor.v1';
/** A visitor may confirm the e-mail the next day; two days is enough and never forever (a stale
 *  choice must not surprise somebody who signs in a month later). */
export const LANDING_MOTOR_TTL_MS = 2 * 24 * 3600 * 1000;

export interface LandingMotorIntent { die: string; config: string }

type Store = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;
const defaultStore = (): Store | null => {
  try { return typeof localStorage === 'undefined' ? null : localStorage; } catch { return null; }
};

export const validIntent = (i: unknown): i is LandingMotorIntent =>
  !!i && typeof i === 'object'
  && typeof (i as LandingMotorIntent).die === 'string' && NAME.test((i as LandingMotorIntent).die)
  && typeof (i as LandingMotorIntent).config === 'string' && NAME.test((i as LandingMotorIntent).config)
  && !(i as LandingMotorIntent).die.includes('..') && !(i as LandingMotorIntent).config.includes('..');

/** Remember the motor whose button the visitor pressed.  A convenience: storage may be blocked. */
export function rememberLandingMotor(intent: LandingMotorIntent, now = Date.now(), store: Store | null = defaultStore()): boolean {
  if (!store || !validIntent(intent)) return false;
  try {
    store.setItem(LANDING_MOTOR_KEY, JSON.stringify({ die: intent.die, config: intent.config, at: now }));
    return true;
  } catch { return false; }
}

/** Forget it (a generic sign-up button was pressed instead). */
export function forgetLandingMotor(store: Store | null = defaultStore()): void {
  try { store?.removeItem(LANDING_MOTOR_KEY); } catch { /* storage blocked */ }
}

/** The remembered motor, if still fresh and well-formed; it is REMOVED either way (it applies once). */
export function takeLandingMotor(now = Date.now(), store: Store | null = defaultStore()): LandingMotorIntent | null {
  if (!store) return null;
  let raw: string | null = null;
  try { raw = store.getItem(LANDING_MOTOR_KEY); store.removeItem(LANDING_MOTOR_KEY); } catch { return null; }
  if (!raw) return null;
  try {
    const v = JSON.parse(raw) as { die?: unknown; config?: unknown; at?: unknown };
    const at = typeof v.at === 'number' ? v.at : NaN;
    if (!Number.isFinite(at) || now - at > LANDING_MOTOR_TTL_MS || at - now > 60_000) return null;
    const intent = { die: v.die, config: v.config };
    return validIntent(intent) ? intent : null;
  } catch { return null; }
}

/** The selection to hand to the app's own "last motor" restore (lib/lastMotor.ts): no reference id
 *  (that needs a signed-in catalogue), so Configure binds the exact die + configuration card. */
export const selectionOfIntent = (i: LandingMotorIntent) =>
  ({ ref_id: null as string | null, die: i.die, config: i.config, duty: null as string | null });

// ── the one-shot "this sign-in came from a motor card: land on Configure" flag ────────────────
// Per tab session: App's first-screen choice for a standard account (Configure when something is
// selected there, else Motors) reads it once, so the card's motor is what the visitor sees first.
export const LANDING_OPEN_FLAG = 'landing.openInConfigure.v1';

const sessionStore = (): Store | null => {
  try { return typeof sessionStorage === 'undefined' ? null : sessionStorage; } catch { return null; }
};

export function markOpenInConfigure(store: Store | null = sessionStore()): void {
  try { store?.setItem(LANDING_OPEN_FLAG, '1'); } catch { /* storage blocked */ }
}

/** True once after a card-initiated sign-in; the flag is removed by reading it. */
export function consumeOpenInConfigure(store: Store | null = sessionStore()): boolean {
  if (!store) return false;
  try {
    const on = store.getItem(LANDING_OPEN_FLAG) === '1';
    store.removeItem(LANDING_OPEN_FLAG);
    return on;
  } catch { return false; }
}
