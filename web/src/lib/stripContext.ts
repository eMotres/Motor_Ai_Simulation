/** Which context the line under the tabs shows for the open tab (owner 2026-10-26).
 *
 *  The strip used to name the LIVE machine (die / configuration / duty) on every tab.  That is right only for the
 *  machine-bound tabs; on Development and Production the user works with OTHER motors, so the line there names the
 *  project (and its motors) or the production, and on tabs with no motor it shows nothing.
 *
 *  Pure functions (no React, no window): `node --test` imports this file directly. */

export type StripKind = 'live' | 'project' | 'production' | 'none';

/** One linked motor of a project as the strip needs it. */
export interface StripMotor {
  die: string;
  config: string;
  status?: 'current' | 'trial';
  /** false: the stored-points Configure card cannot open this motor (known from the project's motors list);
   *  undefined: not known yet (the list is loaded lazily) - the click is allowed and the card says if it fails */
  pointsReady?: boolean;
}

/** The project open on the Development tab, published by the Development page. */
export interface StripProject { id: string; title: string; motors: StripMotor[] }

/** The production open on the Production tab, published by the Production tab. */
export interface StripProduction { die: string; config: string; /** the production's title, only when it is short (see shortTitle) */ title?: string }

export type StripContext =
  | { kind: 'live' }
  | { kind: 'none' }
  | { kind: 'production'; die: string; config: string; title?: string }
  | { kind: 'project'; projectId: string; title: string | null; motors: StripMotor[]; selected: { die: string; config: string } | null };

/** Tabs that show NO machine at all. */
const NO_MOTOR_TABS = new Set(['admin', 'designer']);

/** Which kind of context a tab owns by default.  Every machine-bound tab (Configure, 3-D model, Simulation, Thermal,
 *  Mechanical, Controller, Geometry, Optimization, ...) and every tab not listed here keeps the live machine. */
export function stripKindForTab(tab: string): StripKind {
  // Development shows no strip for now (owner 2026-10-26: «убери пока, только вводит в заблуждение»).  Production shows the
  // motor of the production being viewed, in the Configure strip's style (owner 2026-10-26: «нужно подсвечивать мотор,
  // который загружен в производстве, в том же стиле, как и для Configure, между этими меню»).
  if (tab === 'development') return 'none';
  if (tab === 'production') return 'production';
  if (NO_MOTOR_TABS.has(tab)) return 'none';
  return 'live';
}

const sameMotor = (a: { die: string; config: string }, b: { die: string; config: string }) => a.die === b.die && a.config === b.config;

/** The project's latest "current" linked motor: the last non-trial one in the project's order; if every motor is a
 *  trial, the last one.  null for a project without motors. */
export function latestCurrentMotor(motors: readonly StripMotor[]): StripMotor | null {
  for (let i = motors.length - 1; i >= 0; i--) if (motors[i].status !== 'trial') return motors[i];
  return motors.length > 0 ? motors[motors.length - 1] : null;
}

export interface StripInput {
  tab: string;
  project: StripProject | null;
  production: StripProduction | null;
}

/** The context the strip shows for `tab`. */
export function stripContextFor({ tab, project, production }: StripInput): StripContext {
  const kind = stripKindForTab(tab);
  if (kind === 'none') return { kind: 'none' };
  if (kind === 'production') {
    return production
      ? { kind: 'production', die: production.die, config: production.config, ...(production.title ? { title: production.title } : {}) }
      : { kind: 'none' };
  }
  if (kind === 'project') {
    if (!project) return { kind: 'none' };
    const chosen = latestCurrentMotor(project.motors);
    return { kind: 'project', projectId: project.id, title: project.title, motors: project.motors, selected: chosen ? { die: chosen.die, config: chosen.config } : null };
  }
  // A project motor opened in Configure IS the live machine now (one load path, owner 2026-10-26).
  return { kind: 'live' };
}

/** Chips shown on the one line: the current motors (trial ones only when selected), at most `max`; the rest fold into "+N". */
export function visibleChips(motors: readonly StripMotor[], selected: { die: string; config: string } | null, max = 5): { shown: StripMotor[]; hidden: StripMotor[] } {
  const wanted = motors.filter((m) => m.status !== 'trial' || (selected !== null && sameMotor(m, selected)));
  if (wanted.length <= max) return { shown: wanted, hidden: [] };
  // keep the selected one on screen: show the newest ones, moving the window back if the selection is older
  const selIndex = selected ? wanted.findIndex((m) => sameMotor(m, selected)) : -1;
  let start = Math.max(0, wanted.length - max);
  if (selIndex >= 0 && selIndex < start) start = selIndex;
  const shown = wanted.slice(start, start + max);
  return { shown, hidden: wanted.filter((m) => !shown.includes(m)) };
}

/** A title short enough for the one-line strip (the full title stays on the production page); '' otherwise. */
export function shortTitle(text: string | null | undefined, max = 40): string {
  const t = String(text ?? '').replace(/\s+/g, ' ').trim();
  return t.length > 0 && t.length <= max ? t : '';
}

export const motorLabel = (m: { die: string; config: string }) => `${m.die} / ${m.config}`;
