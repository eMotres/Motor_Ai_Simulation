// Motors catalog = ONE ROW PER MOTOR (die) - owner 2026-10-13.  Its configurations are small
// buttons inside the row; outside, only each configuration's DEFAULT mode is shown, and the
// numbers (power, torque, mass, rpm) are that mode's.  The server states the default
// (`default_duty`, resolved by routes/family.config_default_duty); the same rule is repeated
// here only for trees from an older server that does not send it.
//
// Pure, no imports: web/src/lib/__tests__/catalogRows.test.mjs imports this file.

export interface RowDuty {
  name: string; mode?: string; power_kw?: number | null; torque_nm?: number | null;
  rpm?: number | null; current_arms?: number | null;
  /** SHORT-TIME OR CONTINUOUS (owner 2026-10-19), from the server */
  duty_class?: string | null;
  short_time?: { time_to_limit_s?: number | null } | null;
  result?: { mass_kg?: number | null; mass_active_kg?: number | null; mass_partial?: boolean | null } | null;
}
export interface RowCfg {
  name: string; default_duty?: string | null; duties: RowDuty[];
  card_date?: string | null; role?: string;
}
export interface RowDie { name: string; configs: RowCfg[]; }

/** Does the configuration store a computed point (any duty with a saved result)?  Such a motor can be opened in Configure
 *  points-limited even though it has no catalogue card / passport. */
export function hasStoredPoint(c: RowCfg): boolean {
  return (c.duties ?? []).some((d) => d.result !== null && d.result !== undefined && typeof d.result === 'object');
}

/** Which configurations a client's Motors tab lists (owner 2026-10-26): every one it may open - a configuration with a
 *  catalogue card, one with stored computed points (points-only, no passport yet), and the motors of the project the tab
 *  highlights.  `highlighted` says whether the configuration belongs to that project.  The server's own gate (grants,
 *  project membership) already decided what the tree contains; this only stops the tab hiding what is in it. */
export function isListableForClient(c: RowCfg, highlighted: boolean): boolean {
  return !!c.card_date || hasStoredPoint(c) || highlighted;
}

/** A configuration listed without a catalogue card: it opens points-limited. */
export const isPointsOnly = (c: RowCfg): boolean => !c.card_date;

/** Same rule as the server: the flag, else "rated", else "rated ...", else the first duty. */
export function defaultDutyOf(c: RowCfg): RowDuty | null {
  const ds = c.duties ?? [];
  if (!ds.length) return null;
  const by = (n?: string | null) => (n ? ds.find((d) => d.name === n) : undefined);
  return by(c.default_duty) ?? by('rated')
    ?? ds.find((d) => String(d.name).toLowerCase().startsWith('rated')) ?? ds[0];
}

export interface ConfigButton {
  config: string; duty: string | null;
  power_kw: number | null; torque_nm: number | null; mass_kg: number | null; rpm: number | null;
  /** THE motor mass is the FULL assembled mass (owner 2026-10-14); true = no CAD model yet, so the mass is the active
   *  mass only ("partial mass (no CAD model yet)") - the row says so, never silently mixing the two. */
  mass_partial: boolean;
  mass_active_kg: number | null;
  card_date: string | null; modes: number;
  /** the default mode is SHORT-TIME and the configuration has no continuous
   *  one to show instead: the card says so rather than calling it a rating */
  short_time: boolean;
  short_time_s: number | null;
}

const num = (v: unknown): number | null =>
  (typeof v === 'number' && Number.isFinite(v) ? v : null);

/** The die's configuration buttons, in the die's order, each with its default mode's numbers. */
export function configButtons(die: RowDie): ConfigButton[] {
  return (die.configs ?? []).map((c) => {
    const d = defaultDutyOf(c);
    return {
      config: c.name, duty: d ? d.name : null,
      power_kw: num(d?.power_kw), torque_nm: num(d?.torque_nm),
      mass_kg: num(d?.result?.mass_kg), rpm: num(d?.rpm),
      mass_partial: d?.result?.mass_partial !== false, mass_active_kg: num(d?.result?.mass_active_kg),
      card_date: c.card_date ?? null, modes: (c.duties ?? []).length,
      short_time: d?.duty_class === 'short_time',
      short_time_s: num(d?.short_time?.time_to_limit_s),
    };
  });
}

/** Which button is selected: a pick that still exists, else the loaded configuration of this
 *  die, else the first. */
export function selectedConfig(buttons: readonly ConfigButton[], active: string | null,
                               picked: string | null): string | null {
  const has = (n: string | null) => !!n && buttons.some((b) => b.config === n);
  if (has(picked)) return picked;
  if (has(active)) return active;
  return buttons[0]?.config ?? null;
}

/** What a click on a configuration button does.  The button's tooltip promises "loads at its
 *  default mode", so the click LOADS that mode (incident 2026-10-13: it only picked the
 *  button, the owner clicked "L155 motor" and nothing loaded - the load sat behind the small
 *  play glyph).  `refuse` is the reason a click that cannot load must SAY, never swallow. */
export interface ConfigClickPlan {
  pick: string;
  load: string | null;
  refuse: 'busy' | 'noMode' | null;
  /** true = this die + configuration + default mode IS the loaded machine: nothing to load, but the click
   *  must still answer ("already loaded") - never silence (incident 2026-10-15). */
  alreadyLoaded: boolean;
}

/** The machine that is open in THIS browser.  For an ordinary signed-in account (enforced backend, not admin)
 *  that is the account's own remembered motor (the breadcrumb / lastMotor store) - `/api/family/context` is the
 *  SHARED machine of the owner and says nothing about what this user has loaded (incident 2026-10-15: the
 *  catalog compared its chips against that shared context, so the highlighted L12 chip counted as "loaded"
 *  while CIANO28 85 / L13 was open, and the click did nothing).  Everyone else works on the shared machine. */
export interface LoadedMotor { die: string; config: string; duty: string | null }
interface CtxLike { active?: boolean; die?: string; config?: string; duty?: string | null }
export function loadedMotor(ownWorkspace: boolean,
                            remembered: { die: string; config: string; duty: string | null } | null,
                            server: CtxLike | null): LoadedMotor | null {
  if (ownWorkspace) {
    return remembered ? { die: remembered.die, config: remembered.config, duty: remembered.duty ?? null } : null;
  }
  if (!server || !server.die || !server.config) return null;
  return { die: server.die, config: server.config, duty: server.duty ?? null };
}

/** `loaded` is the configuration name of THIS die that is open (string, mode unknown) or the loaded
 *  {config, duty} (the mode is then compared with the chip's default mode too). */
export function configClickPlan(buttons: readonly ConfigButton[], config: string,
                                loaded: string | { config: string | null; duty?: string | null } | null,
                                busy: boolean): ConfigClickPlan {
  const b = buttons.find((x) => x.config === config);
  if (busy) return { pick: config, load: null, refuse: 'busy', alreadyLoaded: false };
  if (!b || !b.duty) return { pick: config, load: null, refuse: 'noMode', alreadyLoaded: false };
  const lc = typeof loaded === 'string' || loaded == null ? loaded : loaded.config;
  const ld = typeof loaded === 'object' && loaded != null ? loaded.duty ?? null : null;
  // the machine that is already loaded (same configuration, and the same mode when it is known): answer, don't reload
  if (lc === config && (ld == null || ld === b.duty)) {
    return { pick: config, load: null, refuse: null, alreadyLoaded: true };
  }
  return { pick: config, load: b.duty, refuse: null, alreadyLoaded: false };
}

/** Where a load opens the view, at once and before the load finishes (owner 2026-10-13): the
 *  Geometry tab for an account that has it, Configure for a standard user (only Motors +
 *  Configure).  `canWrite`: may write the shared config (the owner path). */
export type LoadTargetTab = 'geometry' | 'compare';
export function loadTargetTab(canWrite: boolean, standardUser: boolean): LoadTargetTab {
  return !standardUser && canWrite ? 'geometry' : 'compare';
}

const modeRank = (name: string): number => {
  const n = name.trim().toLowerCase();
  return n.startsWith('rated') ? 0 : n.startsWith('peak') ? 1 : 2;
};

/** The modes (duties) of one configuration for the header dropdown: rated, peak, then the
 *  rest in saved order.  Empty when the die or configuration is not in the tree. */
export function modeNames(dies: readonly RowDie[], die: string | null | undefined,
                          config: string | null | undefined): string[] {
  const c = dies.find((d) => d.name === die)?.configs.find((x) => x.name === config);
  const names = (c?.duties ?? []).map((d) => d.name);
  return names.map((n, i) => ({ n, i }))
    .sort((a, b) => modeRank(a.n) - modeRank(b.n) || a.i - b.i).map((x) => x.n);
}
