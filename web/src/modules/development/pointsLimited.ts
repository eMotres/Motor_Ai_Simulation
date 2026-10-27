/** Pure decisions of the ONE motor display system (owner 2026-10-26).
 *
 *  A motor opened from the Motors tab and a project motor opened from Development load the same way and are shown the
 *  same way.  Configure opens in its ordinary (full-passport) form when the motor has a passport; when it has none but
 *  stores computed duty points, it opens POINTS-LIMITED: only the stored points, a discrete mode switch, no
 *  interpolation, the continuous knobs disabled.  The server decides (`MotorView.mode`); these helpers turn that into
 *  what the page does.  No React, no window: `node --test` imports this file. */
import { pointOperating } from './pointsOnly.ts';
import type { FieldKind, MotorView, MotorViewDuty, ProjectMotor, StoredMechanical, StoredProvenance, StoredRotorStress, StoredThermal } from './types';

export type ConfigureShow = 'full' | 'points-limited';

/** What Configure shows for the loaded motor.  Anything but a clear "points" answer keeps the ordinary Configure
 *  (still loading, failed, no view, empty): the full path is never replaced on a guess. */
export function configureShow(view: MotorView | null | undefined): ConfigureShow {
  return view && view.mode === 'points' && view.configure_available !== true ? 'points-limited' : 'full';
}

/** Duties that carry a stored point (any EM / thermal / mechanical / field result). */
export function pointDuties(view: MotorView | null | undefined): MotorViewDuty[] {
  return (view?.duties ?? []).filter((d) => d.has_result === true);
}

/** The duty the points-limited view stands on: the one already chosen if it still has a point, else the duty that
 *  was loaded if it has one, else the first stored point.  null when the motor stores no point. */
export function pickPointDuty(view: MotorView | null | undefined, loaded: string | null | undefined, current?: string | null): string | null {
  const points = pointDuties(view);
  for (const wanted of [current, loaded]) {
    if (wanted && points.some((d) => d.duty === wanted)) return wanted;
  }
  return points[0]?.duty ?? null;
}

/** Field kinds a duty has stored, in the viewer's order. */
export const FIELD_KIND_ORDER: readonly FieldKind[] = ['em', 'thermal', 'rotor_stress'];

// ── what belongs to the selected point (owner 2026-10-26) ────────────────────────────────────────────────────────────
// "I chose a concrete configuration; if it has no calculation, show nothing - only real results that belong to each point."
// The server already sends only the records that belong to the duty (same duty, solved at its speed and current, current
// solver, physical numbers).  These helpers are the second belt: whatever still reaches the page is checked once more, and a
// block with nothing real in it is not drawn at all (no header, no empty tiles, no placeholder text).

/** Two speeds are the same point: within 1 % (at least 1 rpm). */
export const sameRpm = (a: number, b: number): boolean => Math.abs(a - b) <= Math.max(1, 0.01 * Math.max(Math.abs(a), Math.abs(b)));
/** A temperature a real result can show: not colder than -80 C (a part below that is a translated-map artefact).  A HIGH value is
 *  a real over-limit answer and is shown as it is. */
export const plausibleC = (v: number | null): boolean => v === null || v >= -80;

/** The mechanical answer of this point: its OWN, current stress record - or null.  A record solved at another speed than the
 *  point's, a superseded one and one of another machine print are not the point's result. */
export function ownRotorStress(duty: MotorViewDuty | null | undefined): StoredRotorStress | null {
  const r = duty?.mechanical?.rotor_stress;
  if (!r) return null;
  if (r.stale === true || r.solver_status === 'superseded' || r.geometry_status === 'differs') return null;
  const rpm = duty ? pointOperating(duty).rpm : null;
  if (rpm !== null) {
    const own = [r.request_rpm, r.worst_case?.rpm, r.proof_rpm].map(num).filter((x): x is number => x !== null);
    if (own.length > 0 && !own.some((x) => sameRpm(x, rpm))) return null;
  }
  return r;
}

/** The mechanical block of this point (own stress record + ring modes / critical speeds filed under it), or null. */
export function pointMechanical(duty: MotorViewDuty | null | undefined): StoredMechanical | null {
  const m = duty?.mechanical;
  if (!m) return null;
  const rs = ownRotorStress(duty);
  const out: StoredMechanical = { ...(rs ? { rotor_stress: rs } : {}), ...(m.modes ? { modes: m.modes } : {}), ...(m.critical_speeds ? { critical_speeds: m.critical_speeds } : {}) };
  return out.rotor_stress || out.modes || out.critical_speeds ? out : null;
}

/** The thermal block of this point, or null when it holds nothing real: tiles with a physical value, or a labelled limit search. */
export function pointThermal(duty: MotorViewDuty | null | undefined): StoredThermal | null {
  const t = duty?.thermal;
  if (!t) return null;
  return thermalTiles(t).length > 0 || (t.limit_search && plausibleC(num(t.limit_search.winding_c))) ? t : null;
}

/** Which blocks the points view draws for a point.  A block that is not listed is not drawn at all. */
export function pointBlocks(duty: MotorViewDuty | null | undefined): { thermal: boolean; mechanical: boolean; fields: boolean } {
  return {
    thermal: pointThermal(duty) !== null,
    mechanical: mechTiles(pointMechanical(duty)).length > 0,
    fields: storedFieldKinds(duty).length > 0,
  };
}

/** Field kinds this point has a stored map for: the map's own stamped speed must be the point's, and a temperature map with an
 *  impossible range is never offered.  The stress map also belongs when the point's own stress record does. */
export function storedFieldKinds(duty: MotorViewDuty | null | undefined): FieldKind[] {
  const rpm = duty ? pointOperating(duty).rpm : null;
  return FIELD_KIND_ORDER.filter((k) => {
    const f = duty?.fields?.[k];
    if (f === undefined) return false;
    if (k === 'rotor_stress' && duty && ownRotorStress(duty)) return true;
    const own = num(f?.point?.rpm);
    if (rpm !== null && own !== null && !sameRpm(own, rpm)) return false;
    if (k === 'thermal' && (!plausibleC(num(f?.point?.T_min)) || !plausibleC(num(f?.point?.T_max)))) return false;
    return true;
  });
}

/** Maps the server left out because they were solved at another speed than the point's, by kind: the two speeds, so the empty
 *  tab can say why (owner 2026-10-27: "why is the electromagnetic simulation not visible?"). */
export function otherPointFields(duty: MotorViewDuty | null | undefined): Partial<Record<FieldKind, { field: number; point: number }>> {
  const out: Partial<Record<FieldKind, { field: number; point: number }>> = {};
  for (const h of duty?.hidden_records ?? []) {
    const m = /^field:(.+)$/.exec(h.kind);
    const kind = m ? (m[1] as FieldKind) : null;
    if (!kind || !FIELD_KIND_ORDER.includes(kind) || h.reason !== 'other_point') continue;
    const field = num(h.field_rpm);
    const point = num(h.point_rpm);
    if (field !== null && point !== null) out[kind] = { field: Math.round(field), point: Math.round(point) };
  }
  return out;
}

/** What a limit-search line is: the S1 continuous limit, the run to the first limit (time to the limit), a plain search - or,
 *  when the run ended INSIDE every limit (`reached_limit` false), a steady state that merely used the limit machinery. */
export function limitKind(limit: StoredThermal['limit_search'] | null | undefined): 'continuous' | 'time' | 'search' | 'steady' {
  if (limit?.reached_limit === false) return 'steady';
  const to = String(limit?.solve_to ?? '').toLowerCase();
  return to === 'continuous' ? 'continuous' : to === 'limits' ? 'time' : 'search';
}

/** One compact provenance line: date, solver, mesh.  Missing parts are left out, never guessed. */
export function provenanceLine(p: StoredProvenance | null | undefined): string {
  if (!p) return '';
  const day = (p.computed_at ?? p.recorded_at ?? '').replace('T', ' ').slice(0, 16);
  const parts: string[] = [];
  if (day) parts.push(day);
  const solver = [p.code_version ? p.code_version.slice(0, 8) : '', p.app_version ? `v${p.app_version}` : ''].filter(Boolean).join(' ');
  if (solver) parts.push(solver);
  const mesh = [typeof p.mesh_size_mm === 'number' ? `${p.mesh_size_mm} mm` : '', typeof p.n_triangles === 'number' ? `${p.n_triangles.toLocaleString('en-US')} tri` : ''].filter(Boolean).join(', ');
  if (mesh) parts.push(mesh);
  return parts.join(' · ');
}

/** One value tile of the stored thermal / mechanical result. */
export interface ResultTile { id: string; value: number | null; unit: string; d: number }

const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);

/** The thermal tiles of a duty.  Temperatures are the DUTY's own (`duty_temps`: the converged coupled loop, else the
 *  steady map); a limit-search state is never turned into a tile.  S1 and S2 are tiles of their own.  Only tiles that hold
 *  a real, physical value are returned (owner 2026-10-26): a missing or impossible number is not drawn as "-". */
export function thermalTiles(t: MotorViewDuty['thermal']): ResultTile[] {
  if (!t) return [];
  const d = t.duty_temps;
  const c = (v: unknown): number | null => { const n = num(v); return plausibleC(n) ? n : null; };      // an impossible temperature is never a tile
  const out: ResultTile[] = d ? [
    { id: 'winding', value: c(d.winding_c), unit: '°C', d: 0 },
    { id: 'magnet', value: c(d.magnet_max_c) ?? c(d.magnet_c), unit: '°C', d: 0 },
    { id: 'housing', value: c(d.housing_c), unit: '°C', d: 0 },
  ] : [];
  const emTorque = num(d?.em?.torque_nm);
  if (emTorque !== null) out.push({ id: 'emTorque', value: emTorque, unit: 'N·m', d: 2 });
  const bearing = num(d?.bearing_c);
  if (bearing !== null && plausibleC(bearing)) out.push({ id: 'bearing', value: bearing, unit: '°C', d: 0 });
  if (t.s1 && num(t.s1.I_cont_A_rms) !== null) out.push({ id: 's1', value: num(t.s1.I_cont_A_rms), unit: 'A', d: 1 });
  if (t.s2 && num(t.s2.time_to_limit_s) !== null) out.push({ id: 's2', value: num(t.s2.time_to_limit_s), unit: 's', d: 0 });
  return out.filter((tile) => tile.value !== null);      // only real values: a tile without one is not drawn
}

/** Which run the duty's temperatures are (and when), for the block title: `coupled | thermal`, date.  null without temperatures. */
export function thermalSource(t: MotorViewDuty['thermal']): { kind: 'coupled' | 'thermal'; day: string } | null {
  const d = t?.duty_temps;
  if (!d) return null;
  return { kind: d.kind, day: (d.provenance?.computed_at ?? d.provenance?.recorded_at ?? '').replace('T', ' ').slice(0, 16) };
}

/** The mechanical tiles of ONE stress record: smallest averaged SF, largest displacement, proof speed (only the stored ones). */
export function mechTiles(m: MotorViewDuty['mechanical']): ResultTile[] {
  const r = m?.rotor_stress;
  if (!r) return [];
  const out: ResultTile[] = [
    { id: 'sfMin', value: num(r.sf_min), unit: '', d: 2 },
    { id: 'displacement', value: num(r.max_displacement_um), unit: 'µm', d: 1 },
    { id: 'proofRpm', value: num(r.proof_rpm), unit: 'rpm', d: 0 },
  ];
  const growth = num(r.rotor_od_growth_um);
  if (growth !== null) out.push({ id: 'odGrowth', value: growth, unit: 'µm', d: 1 });
  const limit = num(r.limit_speed_rpm);
  if (limit !== null) out.push({ id: 'limitRpm', value: limit, unit: 'rpm', d: 0 });
  return out.filter((tile) => tile.value !== null);
}

/** "<rpm>, <torque>" - the load case the stored record was solved at (its own speed and torque), the parts that are known. */
export function worstCaseLabel(m: MotorViewDuty['mechanical']): string {
  const w = m?.rotor_stress?.worst_case;
  if (!w) return '';
  const bits: string[] = [];
  if (num(w.rpm) !== null) bits.push(`${Math.round(w.rpm as number).toLocaleString('en-US')} rpm`);
  if (num(w.torque_nm) !== null) bits.push(`${(w.torque_nm as number).toFixed(2)} N·m`);
  // the magnetic pull the case carried (2026-10-26): its size and whether it
  // was the duty's own field or a stated estimate
  if (num(w.pull_mpa) !== null) {
    bits.push(`pull ${(w.pull_mpa as number).toFixed(2)} MPa (${w.pull_source === 'em' ? 'EM field' : 'estimate'})`);
  }
  return bits.join(', ');
}

/** Why a stored mechanical answer is not the current one: `superseded` solver, changed `geometry`, or both; null = current. */
export function mechStaleReasons(m: MotorViewDuty['mechanical']): Array<'superseded' | 'geometry'> {
  const r = m?.rotor_stress;
  const out: Array<'superseded' | 'geometry'> = [];
  if (r?.solver_status === 'superseded') out.push('superseded');
  if (r?.geometry_status === 'differs') out.push('geometry');
  return out;
}

/** The stored field kind a name means (the server's `mech` / `electromagnetic` spellings included); null = not a stored kind. */
export function fieldKindFromName(name: unknown): FieldKind | null {
  const n = String(name ?? '').trim().toLowerCase();
  const map: Record<string, FieldKind> = { em: 'em', electromagnetic: 'em', thermal: 'thermal', rotor_stress: 'rotor_stress', mech: 'rotor_stress', mechanical: 'rotor_stress', stress: 'rotor_stress' };
  return map[n] ?? null;
}

/** A failed map request that only means "never computed" (404 / "Not computed"), not an error to show. */
export function isNotComputed(error: unknown): boolean {
  const e = error as { status?: number; message?: string } | null;
  return e?.status === 404 || /not computed/i.test(String(e?.message ?? ''));
}

/** Retention verdict word of a stored rotor-stress result: pass | fail | unknown. */
export function retentionVerdict(m: MotorViewDuty['mechanical']): 'pass' | 'fail' | 'unknown' {
  const s = String(m?.rotor_stress?.retention?.status ?? m?.rotor_stress?.retention?.verdict ?? '').toLowerCase();
  if (!s) return 'unknown';
  return /fail|not retained|lost|open/.test(s) ? 'fail' : /pass|ok|retain|hold/.test(s) ? 'pass' : 'unknown';
}

/** Safety factors per part, smallest first (the part that decides is the top row). */
export function partSafetyFactors(m: MotorViewDuty['mechanical']): Array<{ part: string; sf: number; material?: string }> {
  const parts = m?.rotor_stress?.parts ?? {};
  return Object.entries(parts)
    .map(([part, p]) => ({ part, sf: num(p.safety_factor), material: p.material }))
    .filter((r): r is { part: string; sf: number; material: string | undefined } => r.sf !== null)
    .sort((a, b) => a.sf - b.sf);
}

/** Which duty a click on a project motor loads: the one asked for, else the duty Configure can open, else the first
 *  duty with a stored point, else the motor's default duty, else its first duty.  null when it has no duty at all
 *  (nothing to load).  The load itself is the Motors tab's (`applyCatalogDuty`) - there is no second path. */
export function dutyToOpen(motor: Pick<ProjectMotor, 'configure_duty' | 'default_duty' | 'duties' | 'results'>, preferred?: string | null): string | null {
  const ok = (v: unknown): v is string => typeof v === 'string' && v.trim() !== '';
  if (ok(preferred)) return preferred;
  if (ok(motor.configure_duty)) return motor.configure_duty;
  const withResult = (motor.results ?? []).find((r) => r.has_result === true && ok(r.duty));
  if (withResult && ok(withResult.duty)) return withResult.duty;
  if (ok(motor.default_duty)) return motor.default_duty;
  for (const d of motor.duties ?? []) {
    const name = typeof d === 'string' ? d : d?.name;
    if (ok(name)) return name;
  }
  return null;
}

/** Whether a point's mass is the ACTIVE mass (EM parts x stack, no holders / housing / bearings) or the whole assembled motor:
 *  the server's stamp `mass_partial` - anything but an explicit `false` is active (never claim a full mass on a guess). */
export function massKind(point: { comparison?: Record<string, unknown> } | null | undefined): 'active' | 'full' {
  return point?.comparison?.mass_partial === false ? 'full' : 'active';
}

/** The Configure label key (`controller` namespace) of a points-limited tile: the SAME names the ordinary Configure uses
 *  (owner 2026-10-26).  null = Configure has no such tile; the caller keeps its own wording. */
export function configureTileKey(tileId: string, point?: { comparison?: Record<string, unknown> } | null): string | null {
  const map: Record<string, string> = {
    torque: 'configure.torque', power: 'configure.power', efficiency: 'configure.efficiency', ripple: 'configure.tRipple',
    losses: 'configure.totalLoss', pCu: 'configure.copperLoss', pFe: 'configure.ironLoss', pMag: 'configure.magnetLoss',
    vLine: 'configure.vLinePeak', vPhase: 'configure.vPhasePeak', vDc: 'configure.dcBusMin', j: 'configure.currDensity',
  };
  if (tileId === 'mass') return massKind(point) === 'active' ? 'configure.massPartial' : 'configure.mass';
  if (tileId === 'torqueMass') return massKind(point) === 'active' ? 'configure.tPerMassActive' : 'configure.tPerMass';
  return map[tileId] ?? null;
}
