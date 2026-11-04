// "With free-run losses" — the Electromagnetic tab's display switch between PURE SIMULATION (the default) and simulation
// plus the real no-load loss measured on the bench (owner 2026-11-04: «сделай галочку с учетом потерь от freerun и без
// (чистая симуляция)»).
//
// DISPLAY ONLY: nothing is solved, nothing stored changes, the report and the datasheet stay pure simulation.  This file is
// the pure half (no React, no fetch): the extra loss at a point, the balance and efficiencies with it, the reason the box is
// disabled, and the per-viewer switch in localStorage.
//
//   P_extra(rpm) = measured free-run shaft power - the MODEL's no-load loss at that bench speed
//
// piecewise-linear in rpm between bench speeds, NEVER extrapolated.  The model figure is the one the Tests tab already
// compares (src/motor_ai_sim/motor_tests/comparison.py `freerun_loss`): the stored no-load electromagnetic loss scaled to the
// bench speed + bearings + windage; nothing is solved for it.  P_extra may be negative and is shown as it is.
//
// The extra loss sits on the SHAFT side (bearings, windage, the bench's own friction): the terminals do not feel it.
//   motor:     P_shaft = P_rotor - X - P_extra     P_elec unchanged     eta = P_shaft / P_elec
//   generator: P_shaft = P_rotor + X + P_extra     P_elec unchanged     eta = P_elec  / P_shaft
// which is lib/shaftBalance.ts with X := X + P_extra - ONE efficiency, at the shaft (dutyEfficiencyPct follows the same rule).
import type { ShaftBalance } from './shaftBalance.ts';

/** The per-viewer switch (localStorage).  Survives switching points; the report never reads it. */
export const FREE_RUN_STORAGE_KEY = 'sim.freeRunLosses';

/** One bench speed of the newest free-run test: the measured shaft power and the model's no-load loss at that speed. */
export interface FreeRunPoint { rpm: number; measured_w: number | null; model_w: number | null }

/** `GET /api/motor-tests/{die}/{config}/freerun-loss` (and the project twin): `available: false` = nothing to offer. */
export interface FreeRunLossData {
  available: boolean;
  test_id?: string | null;
  date?: string | null;
  points?: FreeRunPoint[] | null;
}

/** Why the box is disabled at a point.  `none` = no free-run test at all (the box is not drawn then). */
export type FreeRunWhy = 'none' | 'no_rpm' | 'out_of_range' | 'no_model';

export type FreeRunExtra =
  | { ok: true; watts: number; rpm: number; measuredW: number | null; modelW: number | null }
  | { ok: false; why: FreeRunWhy; loRpm?: number; hiRpm?: number };

const fin = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);

/** A point's speed may sit a rounding hair off the bench's (3000 vs 2999.6): within 0.1 % it IS that bench speed. */
const RPM_TOL = 1e-3;

interface Delta { rpm: number; delta: number | null; measured: number; model: number | null }

/** One entry per bench speed (rows at the same speed are averaged); `delta` is null where the model has no figure. */
function deltasOf(points: readonly FreeRunPoint[] | null | undefined): Delta[] {
  const byRpm = new Map<number, { m: number[]; mod: (number | null)[] }>();
  for (const p of points ?? []) {
    const rpm = fin(p?.rpm), meas = fin(p?.measured_w);
    if (rpm == null || rpm <= 0 || meas == null) continue;
    const e = byRpm.get(rpm) ?? { m: [], mod: [] };
    e.m.push(meas); e.mod.push(fin(p.model_w));
    byRpm.set(rpm, e);
  }
  return [...byRpm.entries()].sort((a, b) => a[0] - b[0]).map(([rpm, e]) => {
    const measured = e.m.reduce((a, b) => a + b, 0) / e.m.length;
    const known = e.mod.every((x) => x != null);
    const model = known ? (e.mod as number[]).reduce((a, b) => a + b, 0) / e.mod.length : null;
    return { rpm, measured, model, delta: model == null ? null : measured - model };
  });
}

/** The extra loss at `rpm`, or why there is none.  No extrapolation: outside the bench speeds, or where a neighbouring bench
 *  speed has no model figure, the box is disabled with the reason. */
export function extraLossAt(rpm: number | null | undefined, data: FreeRunLossData | null | undefined): FreeRunExtra {
  const d = deltasOf(data?.available ? data.points : null);
  if (!d.length) return { ok: false, why: 'none' };
  const n = fin(rpm);
  if (n == null || n <= 0) return { ok: false, why: 'no_rpm' };
  const lo = d[0], hi = d[d.length - 1];
  if (n < lo.rpm * (1 - RPM_TOL) || n > hi.rpm * (1 + RPM_TOL)) {
    return { ok: false, why: 'out_of_range', loRpm: lo.rpm, hiRpm: hi.rpm };
  }
  const exact = d.find((x) => Math.abs(x.rpm - n) <= RPM_TOL * n);
  if (exact) {
    return exact.delta == null ? { ok: false, why: 'no_model' }
      : { ok: true, watts: exact.delta, rpm: n, measuredW: exact.measured, modelW: exact.model };
  }
  const b = d.findIndex((x) => x.rpm > n), a = b - 1;
  const A = d[a], B = d[b];
  if (A.delta == null || B.delta == null) return { ok: false, why: 'no_model' };
  const f = (n - A.rpm) / (B.rpm - A.rpm);
  return {
    ok: true, watts: A.delta + f * (B.delta - A.delta), rpm: n,
    measuredW: A.measured + f * (B.measured - A.measured),
    modelW: A.model != null && B.model != null ? A.model + f * (B.model - A.model) : null,
  };
}

/** The locale key of the one-line reason (namespace `results`). */
export const FREE_RUN_WHY_KEY: Record<Exclude<FreeRunWhy, 'none'>, string> = {
  no_rpm: 'freeRunWhyNoRpm',
  out_of_range: 'freeRunWhyOutOfRange',
  no_model: 'freeRunWhyNoModel',
};

/** The shaft balance with the extra loss on the shaft side: the terminals (`pElec`, `lossEm`) are untouched, the shaft power
 *  moves by `extraW` (down for a motor, up for a generator), the total loss grows by it and the efficiency is the shaft's.
 *  A machine without a bearing model (friction unknown) becomes "known" here: the bench figure is a shaft-side measurement. */
export function withFreeRunLoss(bal: ShaftBalance, extraW: number): ShaftBalance {
  if (!Number.isFinite(extraW)) return bal;
  const pShaft = bal.gen ? bal.pShaft + extraW : Math.max(0, bal.pShaft - extraW);
  const lossAll = bal.lossEm != null ? (bal.lossAll ?? bal.lossEm) + extraW : null;
  const etaShaft = bal.pElec == null ? null
    : (bal.gen ? (pShaft > 0 ? bal.pElec / pShaft : 0) : (bal.pElec > 0 ? pShaft / bal.pElec : 0));
  const xMech = bal.pRotor > 0 && lossAll != null && bal.lossEm != null ? (lossAll - bal.lossEm) / bal.pRotor : bal.xMech;
  return { ...bal, mechKnown: true, pShaft, lossAll, xMech, etaShaft, eta: etaShaft ?? bal.etaEm };
}

/** Efficiency of a point that is only known from its stored result (the point chips): the stored shaft efficiency, the shaft
 *  power it was saved with and the direction.  Same rule as above - motor: eta * (P - e) / P, generator: eta * P / (P + e). */
export function storedEfficiencyWithExtra(etaPct: number | null | undefined, shaftW: number | null | undefined,
                                          generator: boolean, extraW: number): number | null {
  const eta = fin(etaPct), p = fin(shaftW);
  if (eta == null || p == null || p <= 0 || !Number.isFinite(extraW)) return null;
  return generator ? eta * p / (p + extraW) : eta * Math.max(0, p - extraW) / p;
}

type StorageLike = Pick<Storage, 'getItem' | 'setItem'>;
const pickStorage = (s?: StorageLike | null): StorageLike | null => {
  if (s !== undefined) return s;
  try { return typeof localStorage === 'undefined' ? null : localStorage; } catch { return null; }
};

/** The viewer's choice; OFF (pure simulation) whenever storage is missing or throws. */
export function readFreeRunEnabled(storage?: StorageLike | null): boolean {
  try { return pickStorage(storage)?.getItem(FREE_RUN_STORAGE_KEY) === '1'; } catch { return false; }
}

/** Remember the choice; a throwing or full storage only means it is not remembered. */
export function writeFreeRunEnabled(on: boolean, storage?: StorageLike | null): void {
  try { pickStorage(storage)?.setItem(FREE_RUN_STORAGE_KEY, on ? '1' : '0'); } catch { /* a per-viewer nicety only */ }
}
