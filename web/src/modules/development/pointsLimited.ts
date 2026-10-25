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

/** The thermal tiles of a duty (coupled loop result first, the temperature map where the loop did not run).
 *  Every tile is shown even when missing (value null -> "-" with the "not stored" tooltip): nothing is invented. */
export function thermalTiles(t: MotorViewDuty['thermal']): ResultTile[] {
  if (!t) return [];
  const comp = (name: string) => num(t.components_c?.[name]?.max);
  const out: ResultTile[] = [
    { id: 'winding', value: num(t.coupled?.coil_temp_c) ?? comp('winding'), unit: '°C', d: 0 },
    { id: 'magnet', value: num(t.coupled?.magnet_temp_max_c) ?? num(t.coupled?.magnet_temp_c) ?? comp('magnet'), unit: '°C', d: 0 },
    { id: 'housing', value: num(t.housing_c), unit: '°C', d: 0 },
  ];
  const bearing = num(t.coupled?.bearing_temp_c);
  if (bearing !== null) out.push({ id: 'bearing', value: bearing, unit: '°C', d: 0 });
  if (t.s1) out.push({ id: 's1', value: num(t.s1.I_cont_A_rms), unit: 'A', d: 1 });
  if (t.s2) out.push({ id: 's2', value: num(t.s2.time_to_limit_s), unit: 's', d: 0 });
  return out;
}

/** The mechanical tiles of a duty: smallest averaged SF, retention, largest displacement, proof speed. */
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
