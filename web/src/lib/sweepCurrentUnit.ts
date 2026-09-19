/**
 * Phase current display unit — shared by the Sweep study range cards
 * (SweepConfigPanel's SweepVarCard / VarLine) and, since 2026-09-19, the
 * "Picked:" line and the applied-value message in SweepStudyPanel.
 *
 * The STORE always speaks Arms (RMS) — same convention as the geometry
 * store, the backend's operating-point fields and every persisted range.
 * "Peak" is a display-only convention the user picked (2026-08-22 standing
 * choice): the range card shows peak by default and converts at the edge
 * (kU = sqrt(2)), same as here.
 *
 * Bug (owner, 2026-09-19): the "Picked: I = 49.4975 A" line printed the raw
 * stored Arms value with no unit, right next to a card reading "70 A peak"
 * for the SAME operating point (49.4975 * sqrt(2) = 70) — two numbers for
 * one current, and nothing said which was which.  This module is the ONE
 * place both now read, so they can never show a different unit again.
 */

const UNIT_KEY = 'sweep.iUnit';

export type CurrentUnit = 'arms' | 'peak';

/** The unit the range card is currently showing — 'peak' unless the user
 *  explicitly picked RMS (persisted, same key the card writes). */
export function readCurrentUnit(): CurrentUnit {
  try {
    return localStorage.getItem(UNIT_KEY) === 'arms' ? 'arms' : 'peak';
  } catch {
    return 'peak';
  }
}

/** Stored Arms -> the display unit.  kU is the SAME factor the card and its
 *  RangeField boxes use (Arms * sqrt(2) = peak amplitude of a sinusoid). */
export function currentDisplay(rmsA: number, unit: CurrentUnit): { value: number; unit: string } {
  const kU = unit === 'peak' ? Math.SQRT2 : 1;
  const v = (Number.isFinite(rmsA) ? rmsA : 0) * kU;
  return { value: v, unit: unit === 'peak' ? 'A peak' : 'Arms' };
}

/** One-line "12.34 A peak" (or "Arms"), rounded to 2 decimals — what the
 *  Picked line and the applied-value message print. */
export function formatCurrent(rmsA: number, unit: CurrentUnit = readCurrentUnit()): string {
  const { value, unit: label } = currentDisplay(rmsA, unit);
  return `${value.toFixed(2)} ${label}`;
}
