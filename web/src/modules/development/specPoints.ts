/** What a project page shows (owner 2026-11-09, ALL projects, every viewer including admins): only clean, computed, optimised
 *  points - the working kitchen stays inside.
 *   - the Optimization-steps strip hides every trial variant (it stays in storage; the variant that is open is kept so the page
 *     stays coherent);
 *   - the MODE strip and the Motors comparison table show only the duties that are POINTS OF THE PROJECT SPECIFICATION, plus at
 *     most ONE no-load test duty: the COLD one (magnets / winding at ambient, the test that determines the coefficients).
 *  Nothing is deleted: engineers reach every duty through Simulation and the die's own pages, outside the project page.
 *
 *  Matching a duty to a spec point (no stored link exists today; the strongest available signal wins):
 *   1. explicit link: a duty that carries `spec_row_id` equal to a spec point's `row_id`;
 *   2. name: the duty name equals the spec point name, or starts with it as whole words (`cruise_5000` for `Cruise`);
 *      case, spaces, `_` and `-` are ignored;
 *   3. operating point: same speed (1 %) and same torque (3 %), the torque being the duty's requested or its solved one.
 *  No spec, no spec points, or no duty that matches: nothing is filtered (never an empty strip).
 *  The no-load duty: name has no-load / no_load / free-run; the coldest magnet / coil temperature first (unknown temperature
 *  ranks last), then the highest speed; one only.
 *  Pure functions: `node --test` imports this file. */
import { legacyKeyPoints, KEY_POINTS_ID } from './keyPoints.ts';
import type { SpecDraft } from './wizardState';

export interface SpecPointRef { rowId: string | null; name: string; torqueNm: number | null; speedRpm: number | null }
/** What the matcher needs to know about a duty. `tempC` = the warmer of its magnet / coil temperatures, null when unknown. */
export interface DutyInfo { name: string; rpm: number | null; torqueNm: number | null; tempC?: number | null; specRowId?: string | null }

const rec = (v: unknown): Record<string, unknown> => (v && typeof v === 'object' && !Array.isArray(v) ? v as Record<string, unknown> : {});
const num = (v: unknown): number | null => {
  if (typeof v === 'boolean' || v === null || v === undefined || v === '') return null;
  const n = typeof v === 'number' ? v : Number(v);
  return Number.isFinite(n) ? n : null;
};
/** A plain number, or the maximum of a range stored before the one-value rule. */
const worst = (v: unknown): number | null => num(v) ?? num(rec(v).maximum);

/** The points of the specification: its key points (else the legacy S1 / peak pair) and its mission-profile rows. */
export function specPointRefs(draft: SpecDraft | null | undefined): SpecPointRef[] {
  if (!draft) return [];
  const out: SpecPointRef[] = [];
  const answer = rec(draft.fields?.[KEY_POINTS_ID]);
  const stored = Array.isArray(answer.value) ? answer.value : null;
  const rows: Array<Record<string, unknown>> = stored
    ? stored.map(rec)
    : (answer.status === 'not_known_yet' ? [] : legacyKeyPoints(draft, { continuous: 's1', peak: 'peak' }).map((k) => ({ ...k })));
  for (const r of rows) out.push({ rowId: typeof r.row_id === 'string' ? r.row_id : null, name: String(r.name ?? ''), torqueNm: worst(r.torque_Nm), speedRpm: worst(r.speed_rpm) });
  if (draft.mission_profile_status !== 'not_known_yet') {
    for (const r of Array.isArray(draft.mission_profile) ? draft.mission_profile : []) {
      const row = rec(r);
      out.push({ rowId: typeof row.row_id === 'string' ? row.row_id : null, name: String(row.phase ?? row.name ?? ''), torqueNm: worst(row.torque_Nm), speedRpm: worst(row.speed_rpm ?? row.rpm) });
    }
  }
  return out.filter((p) => p.name.trim() || (p.torqueNm !== null && p.speedRpm !== null));
}

const words = (s: string): string[] => s.toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);
const close = (a: number, b: number, rel: number, abs: number) => Math.abs(a - b) <= Math.max(abs, rel * Math.max(Math.abs(a), Math.abs(b)));

/** Does the duty correspond to this spec point (rules 1-3 above)? */
export function dutyMatchesSpecPoint(duty: DutyInfo, point: SpecPointRef): boolean {
  if (duty.specRowId && point.rowId && duty.specRowId === point.rowId) return true;
  const mine = words(duty.name), theirs = words(point.name);
  if (theirs.length > 0 && mine.length >= theirs.length && theirs.every((w, i) => mine[i] === w)) return true;
  return duty.rpm !== null && duty.torqueNm !== null && point.speedRpm !== null && point.torqueNm !== null
    && close(duty.rpm, point.speedRpm, 0.01, 1) && close(duty.torqueNm, point.torqueNm, 0.03, 0.05);
}

/** A no-load / free-run test duty, by name. */
export const isNoLoadDutyName = (name: string): boolean => /no[\s_-]*load|free[\s_-]*run/i.test(name);

/** Index of the no-load duty to keep: coldest first (unknown temperature last), then the highest speed, then the earlier one; -1 when none. */
export function pickNoLoad(infos: readonly DutyInfo[]): number {
  let best = -1;
  infos.forEach((d, i) => {
    if (!isNoLoadDutyName(d.name)) return;
    if (best < 0) { best = i; return; }
    const a = d.tempC ?? Infinity, b = infos[best].tempC ?? Infinity;
    if (a < b || (a === b && (d.rpm ?? -Infinity) > (infos[best].rpm ?? -Infinity))) best = i;
  });
  return best;
}

/** The duties a project page shows: those that match a spec point, plus ONE cold no-load duty when no matched duty is a no-load one
 *  already.  Order kept.  Unfiltered (the same list) when there are no spec points or no duty matches. */
export function filterToSpecPoints<T>(items: readonly T[], info: (item: T) => DutyInfo, points: readonly SpecPointRef[]): T[] {
  if (points.length === 0) return items.slice();
  const infos = items.map(info);
  const matched = new Set<number>();
  infos.forEach((d, i) => { if (points.some((p) => dutyMatchesSpecPoint(d, p))) matched.add(i); });
  if (matched.size === 0) return items.slice();
  if (![...matched].some((i) => isNoLoadDutyName(infos[i].name))) {
    const best = pickNoLoad(infos);
    if (best >= 0) matched.add(best);
  }
  return items.filter((_, i) => matched.has(i));
}

/** Duty info of a stored point / result / duty row (operating point, comparison, optional explicit link). */
export function dutyInfoOf(row: { duty?: string | null; operating_point?: Record<string, unknown>; electromagnetic?: Record<string, unknown>; comparison?: Record<string, unknown>; spec_row_id?: unknown } | null | undefined): DutyInfo {
  const op = rec(row?.operating_point), em = rec(row?.electromagnetic), cmp = rec(row?.comparison);
  const first = (...v: unknown[]) => { for (const x of v) { const n = num(x); if (n !== null) return n; } return null; };
  const temps = [first(cmp.magnet_temp_c, op.magnet_temp_c), first(cmp.coil_temp_c, op.coil_temp_c)].filter((t): t is number => t !== null);
  return {
    name: String(row?.duty ?? ''),
    rpm: first(op.rpm, op.speed_rpm, em.rpm),
    torqueNm: first(op.torque_nm, op.torque_Nm, cmp.torque_nm),
    tempC: temps.length ? Math.max(...temps) : null,
    specRowId: typeof row?.spec_row_id === 'string' ? row.spec_row_id : null,
  };
}

/** The optimization steps a project page shows: every variant that is not a trial, plus the one that is open (`die` / `config`, case
 *  and canonical die as everywhere).  Hidden variants stay in storage. */
export function visibleSteps<V extends { die: string; canonical_die?: string; config: string; status?: string }>(variants: readonly V[], die?: string | null, config?: string | null): V[] {
  const same = (a: string | undefined, b: string | null | undefined) => !!a && !!b && a.toLowerCase() === b.toLowerCase();
  return variants.filter((v) => v.status !== 'trial' || (same(v.config, config) && (same(v.die, die) || same(v.canonical_die, die))));
}
