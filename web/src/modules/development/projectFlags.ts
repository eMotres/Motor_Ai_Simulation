/** The two staff marks of a project variant (owner 2026-10-26) and what they decide.  Pure functions: `node --test` imports this.
 *
 *  - RECOMMENDED: one variant of the project marked by staff (never an automatic metric); with none marked the latest current
 *    variant stands in and the tooltip says so.
 *  The report needs no review mark any more (owner 2026-11-26): every project member downloads it at any time. */
import type { ProjectMotor, StaffFlag } from './types';

const isFlag = (f: unknown): f is StaffFlag => !!f && typeof f === 'object';

export interface ReportAccess { enabled: boolean; reason: 'ok' | 'staff' }

/** May this caller download the variant's report?  Everybody who can open the variant, at any time. */
export function reportAccess(_motor: Pick<ProjectMotor, 'report_reviewed'> | null | undefined, staff: boolean): ReportAccess {
  return { enabled: true, reason: staff ? 'staff' : 'ok' };
}

export interface RecommendedChoice { motor: ProjectMotor; explicit: boolean }

const isCurrent = (m: ProjectMotor) => m.kind !== 'history' && m.status !== 'trial';

/** The recommended variant: the one staff marked; else the latest current variant (`explicit: false`, so the tooltip says it is
 *  only the fallback).  null for a project with no current variant. */
export function recommendedMotor(motors: ReadonlyArray<ProjectMotor>): RecommendedChoice | null {
  const current = motors.filter(isCurrent);
  if (!current.length) return null;
  const marked = current.find((m) => isFlag(m.recommended));
  return marked ? { motor: marked, explicit: true } : { motor: current[current.length - 1], explicit: false };
}

/** Is `motor` THE recommended variant by an explicit mark?  (the star / chip / accent are drawn only for a real mark) */
export const isRecommended = (motor: Pick<ProjectMotor, 'recommended'> | null | undefined): boolean => isFlag(motor?.recommended);

/** "who - when" of a mark, for a tooltip; the date part only. */
export function flagWho(flag: StaffFlag | null | undefined): string {
  if (!isFlag(flag)) return '';
  const day = String(flag.at ?? '').replace('T', ' ').slice(0, 16);
  return [flag.by, day].filter(Boolean).join(' · ');
}
