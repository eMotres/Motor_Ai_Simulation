// What a motor configuration is FOR (owner 2026-10-13): `applications: [...]` in its yaml, the
// server states it (family tree `applications` / `application`, Configure context and presets).
// The first entry is the default.  This file only reads that list: it gates Configure's
// propeller controls, drives the Motors badge + filter and the Admin "not set" audit.
//
// Pure, imports types only: web/src/lib/__tests__/applications.test.mjs imports this file.
import type { CoolingInfo } from './configuratorPropeller';

export const APPLICATIONS = ['propeller', 'robot', 'aviation', 'marine'] as const;
export type Application = typeof APPLICATIONS[number];
export type ApplicationFilter = 'all' | Application;

export const isApplication = (v: unknown): v is Application =>
  typeof v === 'string' && (APPLICATIONS as readonly string[]).includes(v);

/** The known entries of a list, in order (the server already refuses unknown ones). */
export function cleanApplications(v: unknown): Application[] {
  return Array.isArray(v) ? v.filter(isApplication) : [];
}

/** The application in force: the user's pick when it is one of the configuration's, else the
 *  first entry, else null (the field is not set: today's behaviour). */
export function selectedApplication(applications: unknown, choice?: string | null): Application | null {
  const apps = cleanApplications(applications);
  if (choice && (apps as string[]).includes(choice)) return choice as Application;
  return apps[0] ?? null;
}

/** Configure shows the application switch only when a configuration lists more than one. */
export const hasApplicationSwitch = (applications: unknown): boolean =>
  cleanApplications(applications).length > 1;

/** True when the propeller controls (picker, prop-wash cooling, propeller S1, thrust, rpm at the
 *  propeller) may be offered: the application is propeller, or not set. */
export const propellerOffered = (selected: Application | null): boolean =>
  selected == null || selected === 'propeller';

/** The cooling block with the application applied (the same rule as the server's
 *  `applications.restrict_cooling`; the server already applies the default application, this
 *  re-applies it for the application the user switched to).  A robot keeps the robotics mode;
 *  aviation and marine have neither a propeller nor the robotics block.  The registry file is
 *  not touched: switching back restores the propeller. */
export function coolingForApplication(c: CoolingInfo | null | undefined,
                                      selected: Application | null): CoolingInfo | null | undefined {
  if (!c || propellerOffered(selected)) return c;
  const opts = Array.isArray(c.cooling_options) ? c.cooling_options : null;
  const kept = opts ? opts.filter((o) => o !== 'propeller_air') : null;
  if (kept && selected === 'robot' && !kept.includes('robotics')) kept.push('robotics');
  return {
    ...c,
    cooling_options: kept && kept.length ? kept : null,
    restricted: !!(kept && kept.length),
    propellers: c.propellers ? [] : c.propellers,
    default_propeller: null,
    defaults: {},
  };
}

// ── Motors catalog ───────────────────────────────────────────────────────────────────────
export interface AppCfg { name: string; applications?: unknown; application_set?: boolean;
                          applications_error?: string | null; }
export interface AppDie<C extends AppCfg = AppCfg> { name: string; configs: C[]; }

/** A configuration matches the filter when any of its applications is the chosen one. */
export function matchesApplication(c: AppCfg, filter: ApplicationFilter): boolean {
  if (filter === 'all') return true;
  return (cleanApplications(c.applications) as string[]).includes(filter);
}

/** The dies that have a matching configuration, each narrowed to those configurations. */
export function filterDiesByApplication<D extends AppDie>(dies: readonly D[], filter: ApplicationFilter): D[] {
  if (filter === 'all') return [...dies];
  return dies
    .map((d) => ({ ...d, configs: d.configs.filter((c) => matchesApplication(c, filter)) }))
    .filter((d) => d.configs.length > 0);
}

/** Reads a stored filter back; anything unknown is "all". */
export const parseApplicationFilter = (v: unknown): ApplicationFilter =>
  isApplication(v) ? v : 'all';

// ── Admin audit ──────────────────────────────────────────────────────────────────────────
export interface AuditRow { die: string; config: string; applications: Application[];
                            state: 'set' | 'notSet' | 'invalid'; error: string | null; }

/** One row per configuration with its state; `notSet` is the "application not set" flag. */
export function applicationAudit(dies: readonly AppDie[]): AuditRow[] {
  const out: AuditRow[] = [];
  for (const d of dies) {
    for (const c of d.configs) {
      const apps = cleanApplications(c.applications);
      const err = c.applications_error ?? null;
      out.push({ die: d.name, config: c.name, applications: apps, error: err,
                 state: err ? 'invalid' : apps.length ? 'set' : 'notSet' });
    }
  }
  return out;
}
