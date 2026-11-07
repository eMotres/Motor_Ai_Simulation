/**
 * The POINTS of a configuration, as the Electromagnetic Simulation tab's point
 * switcher shows them (owner 2026-11-04: «как в конфигуре подгружаешь один
 * мотор и появляется переключатель различных точек моделирования»; and «все
 * duty переходят внутрь моделирования»).
 *
 * The catalog lists MOTORS only and loads each configuration's default duty
 * (`rated`); every other duty of the configuration — a "point" — is chosen,
 * created, renamed, duplicated and deleted in Simulation.  This module is the
 * pure half: what the tree says about the points, in which order, with which
 * marks, and which excitation (Sine / PWM) stays on screen when the point
 * changes.  Nothing here fetches or writes.
 *
 * WHICH DUTY A MOTOR LOADS is not decided here: the server resolves it into
 * `default_duty` on every configuration of the tree (routes/family.py
 * `config_rated_duty`: the per-configuration flag, default `rated` → a duty
 * named exactly `rated` → the first duty whose name starts with `rated` → the
 * first duty; a test / validation point never; a short-time duty never stands
 * in for the rating while a continuous one exists) and `defaultDutyOf` repeats
 * the same rule for trees from an older server.  Names are compared as the
 * tree sends them, never re-typed, so a stray Cyrillic look-alike inside a
 * stored name (`rated 120С`, the 2026-09-19 trap) cannot split a duty in two
 * here; new names are folded to Latin by the server (`_delookalike_duty_name`).
 */
import { defaultDutyOf, shownDuties } from './catalogRows.ts';
import { perSetFromTotal, setsOf } from './perSetCurrent.ts';

/** One stored run of a duty, as the TREE describes it (no payload). */
export interface TreeRun {
  drive: string; primary?: boolean; stale?: boolean; has_payload?: boolean;
}

/** A duty row of `GET /api/family/tree` — the fields the switcher reads. */
export interface TreeDuty {
  name: string; mode?: string; kind?: string | null;
  current_arms?: number | null; rpm?: number | null; gamma_deg?: number | null;
  torque_nm?: number | null; power_kw?: number | null;
  star_delta?: string | null;
  result?: { efficiency_pct?: number | null; v_ll_peak_v?: number | null;
             build_sig?: string | null } | null;
  primary_drive?: string;
  runs?: TreeRun[];
  duty_cycle?: Record<string, unknown> | null;
  /** The server already compared the saved numbers with the build (the read-only project view sends this flag and
   *  no signatures); the tree sends `result.build_sig` and the web compares. */
  stale?: boolean;
}

/** A configuration row of the tree. */
export interface TreeCfg {
  name: string; build_sig?: string; default_duty?: string | null;
  phases?: number | string | null; duties: TreeDuty[];
}

/** What one chip needs. */
export interface Point {
  name: string;
  /** the duty the catalog loads for this motor (the "rated" of the rule above) */
  isDefault: boolean;
  /** the saved numbers were computed on another build — shown, never hidden */
  stale: boolean;
  /** stored excitations (drive ids), the primary first */
  drives: string[];
  primaryDrive: string;
  /** one line for the tooltip */
  tip: string;
}

const rank = (name: string): number => {
  const n = name.trim().toLowerCase();
  return n.startsWith('rated') ? 0 : n.startsWith('peak') ? 1 : 2;
};

const num = (v: unknown): number | null =>
  (typeof v === 'number' && Number.isFinite(v) ? v : null);

const f = (v: unknown, d = 1): string => {
  const n = num(v);
  return n == null ? '—' : n.toFixed(d).replace(/\.0+$/, '');
};

/** The configuration `cfg` of die `die` in a tree, or null. */
export function configOfTree(dies: readonly { name?: string; configs?: TreeCfg[] }[] | null | undefined,
                             die: string | null | undefined,
                             cfg: string | null | undefined): TreeCfg | null {
  if (!die || !cfg) return null;
  return dies?.find((d) => d.name === die)?.configs?.find((c) => c.name === cfg) ?? null;
}

/** The excitations a duty has stored, the primary one first. */
export function drivesOf(d: TreeDuty): string[] {
  const rows = (d.runs ?? []).filter((r) => r?.drive);
  const prim = d.primary_drive ?? rows.find((r) => r.primary)?.drive ?? 'current';
  const out = rows.map((r) => r.drive);
  out.sort((a, b) => (a === prim ? -1 : 0) - (b === prim ? -1 : 0));
  return out;
}

/** Was this duty's saved result — or any of its stored runs — solved on a build
 *  the configuration has since moved off? */
export function isStale(d: TreeDuty, cfg: TreeCfg): boolean {
  if (d.stale === true) return true;
  const sig = d.result?.build_sig;
  if (sig && cfg.build_sig && sig !== cfg.build_sig) return true;
  return (d.runs ?? []).some((r) => r?.stale === true);
}

/** "peak · 30 kW · 95.5 Nm · 3000 rpm · 240 A/set · γ 0° · η 94.1 % · Sine + PWM".
 *  `driveName` turns a drive id into its chip word ("pwm_voltage" → "PWM");
 *  `extra` adds one more phrase (the duty-cycle chip, behind its feature gate). */
export function pointTip(d: TreeDuty, cfg: TreeCfg, driveName: (id: string) => string,
                         extra?: (d: TreeDuty) => string | null): string {
  const sets = setsOf({ phases: cfg.phases });
  const cur = num(d.current_arms);
  const bits = [
    d.mode ?? '',
    num(d.power_kw) == null ? '' : `${num(d.power_kw)!.toFixed(1)} kW`,
    num(d.torque_nm) == null ? '' : `${f(d.torque_nm, 1)} N·m`,
    num(d.rpm) == null ? '' : `${f(d.rpm, 0)} rpm`,
    cur == null ? '' : `${f(perSetFromTotal(cur, sets))} A${sets === 2 ? '/set' : ''}`,
    num(d.gamma_deg) == null ? '' : `γ ${f(d.gamma_deg, 1)}°`,
    num(d.result?.efficiency_pct) == null ? '' : `η ${f(d.result?.efficiency_pct, 1)} %`,
  ].filter(Boolean);
  const drives = drivesOf(d);
  if (drives.length) bits.push(drives.map(driveName).join(' + '));
  const more = extra?.(d);
  if (more) bits.push(more);
  if (isStale(d, cfg)) bits.push('⚠ computed on an older build — load, Run, then Save');
  return bits.join(' · ');
}

/**
 * The points of a configuration in the order the chips show them: rated, peak,
 * then the rest in saved order (the order the header strip's mode list uses).
 * A customer does not see test / validation points; an editor sees all.
 */
export function pointsOf(cfg: TreeCfg | null | undefined, canWrite: boolean,
                         driveName: (id: string) => string = (x) => x,
                         extra?: (d: TreeDuty) => string | null): Point[] {
  if (!cfg) return [];
  const list = canWrite ? (cfg.duties ?? []) : shownDuties(cfg.duties);
  const def = defaultDutyOf(cfg as never)?.name ?? null;
  return list
    .map((d, i) => ({ d, i }))
    .sort((a, b) => rank(a.d.name) - rank(b.d.name) || a.i - b.i)
    .map(({ d }) => {
      const drives = drivesOf(d);
      return {
        name: d.name, isDefault: d.name === def, stale: isStale(d, cfg), drives,
        primaryDrive: d.primary_drive ?? drives[0] ?? 'current',
        tip: pointTip(d, cfg, driveName, extra),
      };
    });
}

/**
 * What the row draws.  The chips are a choice only with two or more points; an
 * editor keeps the controls (new point, rename, …) even with one, because that
 * is where a second point is made.
 */
export function switcherLayout(points: readonly Point[], canWrite: boolean,
                               loaded: string | null): { chips: boolean; controls: boolean } {
  const here = !!loaded && points.some((p) => p.name === loaded);
  return { chips: points.length >= 2, controls: canWrite && (here || points.length > 0) };
}

/**
 * Which excitation to show after the point changed: the one on screen before
 * when the new point has stored it, else the new point's primary (Sine).
 * `null` = keep what the load already put on screen (the primary).
 */
export function driveAfterSwitch(previous: string | null | undefined,
                                 next: Pick<Point, 'drives' | 'primaryDrive'>): string | null {
  if (!previous || previous === next.primaryDrive) return null;
  return next.drives.includes(previous) ? previous : null;
}
