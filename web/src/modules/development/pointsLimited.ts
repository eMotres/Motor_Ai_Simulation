/** Pure decisions of the ONE motor display system (owner 2026-10-26).
 *
 *  A motor opened from the Motors tab and a project motor opened from Development load the same way and are shown the
 *  same way.  Configure opens in its ordinary (full-passport) form when the motor has a passport; when it has none but
 *  stores computed duty points, it opens POINTS-LIMITED: only the stored points, a discrete mode switch, no
 *  interpolation, the continuous knobs disabled.  The server decides (`MotorView.mode`); these helpers turn that into
 *  what the page does.  No React, no window: `node --test` imports this file. */
import type { FieldKind, MotorView, MotorViewDuty, ProjectMotor, StoredProvenance } from './types';

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
export function storedFieldKinds(duty: MotorViewDuty | null | undefined): FieldKind[] {
  return FIELD_KIND_ORDER.filter((k) => duty?.fields?.[k] !== undefined);
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
 *  steady map); a limit-search state is never turned into a tile.  S1 and S2 are tiles of their own.  Every tile is shown
 *  even when missing (value null -> "-" with the "not stored" tooltip): nothing is invented. */
export function thermalTiles(t: MotorViewDuty['thermal']): ResultTile[] {
  if (!t) return [];
  const d = t.duty_temps;
  const out: ResultTile[] = d ? [
    { id: 'winding', value: num(d.winding_c), unit: '°C', d: 0 },
    { id: 'magnet', value: num(d.magnet_max_c) ?? num(d.magnet_c), unit: '°C', d: 0 },
    { id: 'housing', value: num(d.housing_c), unit: '°C', d: 0 },
  ] : [];
  const emTorque = num(d?.em?.torque_nm);
  if (emTorque !== null) out.push({ id: 'emTorque', value: emTorque, unit: 'N·m', d: 2 });
  const bearing = num(d?.bearing_c);
  if (bearing !== null) out.push({ id: 'bearing', value: bearing, unit: '°C', d: 0 });
  if (t.s1) out.push({ id: 's1', value: num(t.s1.I_cont_A_rms), unit: 'A', d: 1 });
  if (t.s2) out.push({ id: 's2', value: num(t.s2.time_to_limit_s), unit: 's', d: 0 });
  return out;
}

/** Which run the duty's temperatures are (and when), for the block title: `coupled | thermal`, date.  null without temperatures. */
export function thermalSource(t: MotorViewDuty['thermal']): { kind: 'coupled' | 'thermal'; day: string } | null {
  const d = t?.duty_temps;
  if (!d) return null;
  return { kind: d.kind, day: (d.provenance?.computed_at ?? d.provenance?.recorded_at ?? '').replace('T', ' ').slice(0, 16) };
}

/** The mechanical tiles of the motor's worst case: smallest averaged SF, largest displacement, proof speed. */
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
  return out;
}

/** "worst case: <rpm>, <torque>" of the stored record - the parts that are known. */
export function worstCaseLabel(m: MotorViewDuty['mechanical']): string {
  const w = m?.rotor_stress?.worst_case;
  if (!w) return '';
  const bits: string[] = [];
  if (num(w.rpm) !== null) bits.push(`${Math.round(w.rpm as number).toLocaleString('en-US')} rpm`);
  if (num(w.torque_nm) !== null) bits.push(`${(w.torque_nm as number).toFixed(2)} N·m`);
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
