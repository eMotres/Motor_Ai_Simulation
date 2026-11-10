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
 *   3. operating point, ONE duty per spec point (the closest): same speed (1 %) and same |torque| - a point given in power
 *      (kW at rpm) is converted to torque.  A torque / power within 3 % of the target fits; failing that, ANY torque of the duty
 *      (a stored duty's operating_point.torque_nm is usually the SOLVED value) may lie from -3 % to +15 % of it (a point solved at a
 *      fixed current lands above the target).  A spec row naming BOTH motor and generator is two points.  Torque is
 *      compared by magnitude (a generator may store it negative).  Like with like: when both the spec row and the duty say
 *      motor or generator (row mode / name, duty mode / name) they must agree.
 *  No spec, no spec points, or no duty that matches: nothing is filtered (never an empty strip).
 *  The no-load duty: name has no-load / no_load / free-run; the coldest magnet / coil temperature first (unknown temperature
 *  ranks last), then the highest speed; one only.
 *  Pure functions: `node --test` imports this file. */
import { legacyKeyPoints, KEY_POINTS_ID } from './keyPoints.ts';
import type { SpecDraft } from './wizardState';

export type MachineMode = 'motor' | 'generator';
export interface SpecPointRef { rowId: string | null; name: string; torqueNm: number | null; powerW: number | null; speedRpm: number | null; mode: MachineMode | null }
/** What the matcher needs to know about a duty.  `tempC` = the warmer of its magnet / coil temperatures, null when unknown. */
export interface DutyInfo {
  name: string; rpm: number | null; mode: MachineMode | null;
  /** the duty's requested targets (DutySpec) and its solved numbers */
  reqTorqueNm: number | null; reqPowerW: number | null; solvedTorqueNm: number | null; solvedPowerW: number | null;
  tempC: number | null; specRowId?: string | null;
}

const rec = (v: unknown): Record<string, unknown> => (v && typeof v === 'object' && !Array.isArray(v) ? v as Record<string, unknown> : {});
const num = (v: unknown): number | null => {
  if (typeof v === 'boolean' || v === null || v === undefined || v === '') return null;
  const n = typeof v === 'number' ? v : Number(v);
  return Number.isFinite(n) ? n : null;
};
const first = (...v: unknown[]): number | null => { for (const x of v) { const n = num(x); if (n !== null) return n; } return null; };
/** A plain number, or the maximum of a range stored before the one-value rule. */
const worst = (v: unknown): number | null => num(v) ?? num(rec(v).maximum);
const words = (s: string): string[] => s.toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);

/** motor / generator from free text (a name) or an explicit mode value; null when it says neither. */
export function machineMode(...hints: unknown[]): MachineMode | null {
  for (const h of hints) {
    const w = words(String(h ?? ''));
    const gen = w.some((x) => /^(gen|generator|generating|generation|regen)$/.test(x));
    const mot = w.some((x) => /^(motor|motoring)$/.test(x));
    if (gen && mot) return null;                      // names both ("motor and generator"): no single mode
    if (gen) return 'generator';
    if (mot) return 'motor';
  }
  return null;
}
/** Does the text name both motor and generator? */
const namesBothModes = (text: string): boolean => { const w = words(text); return w.some((x) => /^(gen|generator|generating|generation|regen)$/.test(x)) && w.some((x) => /^(motor|motoring)$/.test(x)); };

/** The points of the specification: its key points (else the legacy S1 / peak pair) and its mission-profile rows. */
export function specPointRefs(draft: SpecDraft | null | undefined): SpecPointRef[] {
  if (!draft) return [];
  const answer = rec(draft.fields?.[KEY_POINTS_ID]);
  const stored = Array.isArray(answer.value) ? answer.value : null;
  const rows: Array<Record<string, unknown>> = stored
    ? stored.map(rec)
    : (answer.status === 'not_known_yet' ? [] : legacyKeyPoints(draft, { continuous: 's1', peak: 'peak' }).map((k) => ({ ...k })));
  if (draft.mission_profile_status !== 'not_known_yet') {
    for (const r of Array.isArray(draft.mission_profile) ? draft.mission_profile : []) { const row = rec(r); rows.push({ ...row, name: row.phase ?? row.name }); }
  }
  // a row that names BOTH motor and generator is two points (one per mode): each may keep its own duty
  const out = rows.flatMap((r): SpecPointRef[] => {
    const name = String(r.name ?? '');
    const kw = first(r.power_kW, r.power_kw);
    const base = {
      rowId: typeof r.row_id === 'string' ? r.row_id : null, name,
      torqueNm: worst(r.torque_Nm), speedRpm: worst(r.speed_rpm ?? r.rpm),
      powerW: first(r.power_W, r.power_w, kw === null ? null : kw * 1000),
    };
    const explicit = machineMode(r.mode, r.machine_mode, r.role);
    if (explicit === null && namesBothModes(name)) return [{ ...base, mode: 'motor' }, { ...base, mode: 'generator' }];
    return [{ ...base, mode: explicit ?? machineMode(name) }];
  });
  return out.filter((p) => p.name.trim() || p.torqueNm !== null || p.powerW !== null);
}

const close = (a: number, b: number, rel: number, abs: number) => Math.abs(a - b) <= Math.max(abs, rel * Math.max(Math.abs(a), Math.abs(b)));
const toTorque = (powerW: number | null, rpm: number | null): number | null => (powerW !== null && rpm !== null && rpm > 0 ? Math.abs(powerW) * 30 / (Math.PI * rpm) : null);

/** The spec point's target torque (N·m, magnitude): stated torque, else its power at its speed. */
const targetTorque = (p: SpecPointRef): number | null => (p.torqueNm !== null ? Math.abs(p.torqueNm) : toTorque(p.powerW, p.speedRpm));

/** How well the duty's operating point fits the spec point: a distance (smaller is closer), or null when it does not fit. */
export function numericDistance(d: DutyInfo, p: SpecPointRef): number | null {
  if (d.mode && p.mode && d.mode !== p.mode) return null;
  const target = targetTorque(p);
  if (d.rpm === null || p.speedRpm === null || target === null || !close(d.rpm, p.speedRpm, 0.01, 1)) return null;
  // a stored duty's operating_point.torque_nm is often the value the runner wrote back after SOLVING (not a request), so every
  // torque the duty carries may also be judged in the solved window
  const requested = [d.reqTorqueNm !== null ? Math.abs(d.reqTorqueNm) : null, toTorque(d.reqPowerW, d.rpm)].filter((v): v is number => v !== null);
  const tight = requested.filter((v) => close(v, target, 0.03, 0.05)).map((v) => Math.abs(v - target) / Math.max(target, 1e-9));
  if (tight.length) return Math.min(...tight);
  const solved = [...requested, d.solvedTorqueNm !== null ? Math.abs(d.solvedTorqueNm) : null, toTorque(d.solvedPowerW, d.rpm)].filter((v): v is number => v !== null);
  const loose = solved.map((v) => v / Math.max(target, 1e-9) - 1).filter((r) => r >= -0.03 && r <= 0.15).map((r) => 0.5 + Math.abs(r));
  return loose.length ? Math.min(...loose) : null;
}

const nameOrLink = (d: DutyInfo, p: SpecPointRef): boolean => {
  if (d.specRowId && p.rowId && d.specRowId === p.rowId) return true;
  const mine = words(d.name), theirs = words(p.name);
  return theirs.length > 0 && mine.length >= theirs.length && theirs.every((w, i) => mine[i] === w) && !(d.mode && p.mode && d.mode !== p.mode);
};

/** Indices of the duties that are spec points: link / name matches (any number), then each still-unserved spec point takes its closest
 *  unclaimed duty by operating point (one duty per point). */
export function matchedDutyIndices(infos: readonly DutyInfo[], points: readonly SpecPointRef[]): Set<number> {
  const kept = new Set<number>();
  const served = new Set<number>();
  infos.forEach((d, i) => points.forEach((p, j) => { if (nameOrLink(d, p)) { kept.add(i); served.add(j); } }));
  const pairs: Array<{ i: number; j: number; dist: number }> = [];
  infos.forEach((d, i) => { if (!kept.has(i)) points.forEach((p, j) => { if (!served.has(j)) { const dist = numericDistance(d, p); if (dist !== null) pairs.push({ i, j, dist }); } }); });
  pairs.sort((a, b) => a.dist - b.dist);
  for (const { i, j } of pairs) if (!kept.has(i) && !served.has(j)) { kept.add(i); served.add(j); }
  return kept;
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
  const matched = matchedDutyIndices(infos, points);
  if (matched.size === 0) return items.slice();
  if (![...matched].some((i) => isNoLoadDutyName(infos[i].name))) {
    const best = pickNoLoad(infos);
    if (best >= 0) matched.add(best);
  }
  return items.filter((_, i) => matched.has(i));
}

type DutyRow = { duty?: string | null; operating_point?: Record<string, unknown>; electromagnetic?: Record<string, unknown>; comparison?: Record<string, unknown>; sources?: { em?: Record<string, unknown> } | null; spec_row_id?: unknown };
/** Duty info of a stored point / result / duty row.  The run's temperature is read from where the API carries it: the EM run
 *  (`sources.em`), the point's operating point, its electromagnetic block and the comparison numbers. */
export function dutyInfoOf(row: DutyRow | null | undefined): DutyInfo {
  const op = rec(row?.operating_point), em = rec(row?.electromagnetic), cmp = rec(row?.comparison), src = rec(rec(row?.sources).em);
  const name = String(row?.duty ?? '');
  const temps = [
    first(src.magnet_temp_c, op.magnet_temp_c, em.magnet_temp_c, em.t_magnet_c, cmp.magnet_temp_c),
    first(src.coil_temp_c, op.coil_temp_c, em.coil_temp_c, em.t_coil_c, cmp.coil_temp_c),
  ].filter((t): t is number => t !== null);
  const reqKw = first(op.power_kw);
  return {
    name,
    rpm: first(op.rpm, op.speed_rpm, em.rpm),
    mode: machineMode(name) ?? machineMode(op.mode),
    reqTorqueNm: first(op.torque_nm, op.torque_Nm),
    reqPowerW: first(op.power_w, reqKw === null ? null : reqKw * 1000),
    solvedTorqueNm: first(cmp.torque_nm, em.torque_nm, em.t_em_avg_nm),
    solvedPowerW: first(cmp.power_w),
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
