// What the result tables show, derived from the core's schema-v1 records (pure).  A missing metric is never a zero and never green.
import type { Availability, EvaluationResult, MinerLife, PairRating, Rec, StageResult, ValidityState } from './types';

export type Tone = 'ok' | 'estimated' | 'unsupported' | 'failed' | 'unknown';

export const toneOf = (state: ValidityState | undefined | null): Tone =>
  state === 'valid' ? 'ok' : state === 'estimated' ? 'estimated' : state === 'unsupported' ? 'unsupported' : state === 'failed' ? 'failed' : 'unknown';

/** Availability of one metric of a record, by field name ("/overall_efficiency"). */
export const availabilityOf = (rec: Rec | null | undefined, field: string): Availability | null => rec?.metric_availability?.[`/${field}`] ?? null;

export interface Shown { text: string; endurance: boolean; available: boolean; reason: string | null }

/** A number for the screen: null -> "-" with the reason; endurance (zero damage, null + status endurance) is named, never a number. */
export function show(value: number | null | undefined, avail: Availability | null, digits = 3): Shown {
  if (avail?.status === 'endurance') return { text: 'endurance', endurance: true, available: true, reason: avail.reason ?? null };
  if (value === null || value === undefined || !Number.isFinite(value)) return { text: '-', endurance: false, available: false, reason: avail?.reason ?? null };
  const a = Math.abs(value);
  const text = a !== 0 && (a >= 1e6 || a < 1e-3) ? value.toExponential(2) : String(Number(value.toFixed(digits)));
  return { text, endurance: false, available: true, reason: null };
}
export const showField = (rec: Rec | null | undefined, field: string, digits = 3): Shown =>
  show((rec as unknown as Record<string, number | null> | null | undefined)?.[field], availabilityOf(rec, field), digits);

export interface RatingRow { stage: string; mesh: string; member: string; S_F: number | null; S_H: number | null; bending: ValidityState; pitting: ValidityState; internal: boolean }

/** gear1 / gear2 of a mesh named "sun_planet" are the sun and the planet. */
export function memberLabel(mesh: string, member: string): string {
  const parts = mesh.split('_');
  if (parts.length === 2 && (member === 'gear1' || member === 'gear2')) return parts[member === 'gear1' ? 0 : 1];
  return member;
}

export function ratingRows(result: EvaluationResult | null): RatingRow[] {
  const rows: RatingRow[] = [];
  for (const st of result?.stage_results ?? []) {
    for (const pair of Object.values(st.pair_ratings ?? {}) as PairRating[]) {
      for (const key of ['gear1_rating', 'gear2_rating'] as const) {
        const m = pair[key];
        if (!m) continue;
        rows.push({ stage: st.stage_name, mesh: pair.mesh_name, member: memberLabel(pair.mesh_name, m.member_name), S_F: m.S_F, S_H: m.S_H,
          bending: m.bending_state, pitting: m.pitting_state, internal: !!m.is_internal });
      }
    }
  }
  return rows;
}

export interface BearingRow { stage: string; role: string; name: string; life_h: number | null; S0: number | null; state: ValidityState; spectrum: boolean }
export function bearingRows(result: EvaluationResult | null): BearingRow[] {
  const rows: BearingRow[] = [];
  for (const st of (result?.stage_results ?? []) as StageResult[])
    for (const b of st.bearing_ratings ?? [])
      rows.push({ stage: st.stage_name, role: b.role, name: b.bearing_name, life_h: b.L10h_h, S0: b.S0_static, state: b.state ?? 'valid', spectrum: false });
  for (const b of result?.bearing_spectrum_lives ?? [])
    rows.push({ stage: '', role: b.role, name: b.bearing_name, life_h: b.L10_spectrum_h, S0: b.min_S0_static, state: b.state ?? 'valid', spectrum: true });
  return rows;
}

export interface LubricationRow { stage: string; mesh: string; eta: number | null; lambda: number | null; h_min_um: number | null; boundary: boolean }
export function lubricationRows(result: EvaluationResult | null): LubricationRow[] {
  const rows: LubricationRow[] = [];
  for (const st of result?.stage_results ?? [])
    for (const pair of Object.values(st.pair_ratings ?? {}) as PairRating[]) {
      const l = pair.lubrication;
      rows.push({ stage: st.stage_name, mesh: pair.mesh_name, eta: l?.eta_mPas ?? null, lambda: l?.lambda_film ?? null,
        h_min_um: l?.h_min_um ?? null, boundary: !!l?.boundary_lubrication_flag });
    }
  return rows;
}

export const minerRows = (result: EvaluationResult | null): MinerLife[] => result?.gear_miner_lives ?? [];

/** pass_reasons "part:s1.x:unsupported" -> parts; "preset:S_F_duty:failed" etc. */
export function reasonParts(reason: string): { kind: string; subject: string; state: string } {
  const p = reason.split(':');
  return { kind: p[0], subject: p.slice(1, -1).join(':'), state: p.length > 1 ? p[p.length - 1] : '' };
}

/** The limitations the core lists (CORE_API_AND_LIMITATIONS.md "Limitations the UI must show"); text lives in locales gears.json. */
export const LIMITATIONS = [
  'internalRoot', 'reversal', 'staticAllowables', 'outOfDomain', 'helicalYF', 'unqualifiedModels', 'estimates',
  'forces', 'bearingAggregate', 'outlines', 'irMotoringOnly',
] as const;
export type LimitationId = typeof LIMITATIONS[number];
/** The two the owner named as MUST-show; the panel never lets them collapse. */
export const PRIMARY_LIMITATIONS: readonly LimitationId[] = ['internalRoot', 'reversal'];

/** Flags that call for a visible label next to a result (estimated / unsupported / failed come from the state itself). */
export const resultTone = (r: EvaluationResult | null): Tone => toneOf(r?.state);
