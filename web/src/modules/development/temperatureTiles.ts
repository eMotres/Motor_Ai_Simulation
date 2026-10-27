/** MAGNET TEMP and WINDING TEMP tiles of a stored point (owner 2026-10-27: "add the magnet and coil temperature here").
 *
 *  Every calculation must use the REAL magnet temperature, so the owner wants to SEE it next to the results:
 *    1. the duty's stored COUPLED result (`thermal.duty_temps.kind === 'coupled'`): the loop's settled mean magnet / winding
 *       temperatures; the hottest magnet goes to the tooltip;
 *    2. no coupled record but a steady temperature map: that map's hottest node (named so in the tooltip);
 *    3. neither: the temperature the EM run was SOLVED at (`sources.em`).  That is an INPUT (the card / setpoint), not a
 *       computed result, so the tile is muted and the tooltip says so.
 *  A limit-search state (S1 / time to the limit) is never the duty's temperature; it is not read here.  Pure functions:
 *  `node --test` imports this file; the component turns the tooltip keys into text (EN source, ZH mirror). */
import { plausibleC } from './pointsLimited.ts';
import type { MotorViewDuty } from './types.ts';

export type TempBasis = 'coupled' | 'thermal' | 'em_input';

export interface TempTile {
  id: 'magnetTemp' | 'windingTemp';
  /** locale key (development namespace) of the label */
  labelKey: string;
  value: number;
  unit: '°C';
  d: number;
  basis: TempBasis;
  /** an input, not a computed result: drawn muted */
  muted: boolean;
  /** tooltip sentences, joined by the component with a newline (locale key under `points.tempTile`, interpolation values) */
  tip: Array<{ key: string; args?: Record<string, string | number> }>;
}

const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);
const real = (v: unknown): number | null => { const n = num(v); return n !== null && plausibleC(n) ? n : null; };
const day = (p: { computed_at?: string; recorded_at?: string } | undefined): string => (p?.computed_at ?? p?.recorded_at ?? '').replace('T', ' ').slice(0, 16);

type Tip = TempTile['tip'];

/** What the stored thermal record says about HOW the temperatures were reached: S2 time to the limit, S1 rating, cooling air. */
function basisLines(t: NonNullable<MotorViewDuty['thermal']>): Tip {
  const out: Tip = [];
  const s2 = t.s2;
  const time = num(s2?.time_to_limit_s);
  if (time !== null) out.push({ key: 'points.tempTile.timeToLimit', args: { time: Math.round(time), part: s2?.limiting_part ?? '—' } });
  const s1 = num(t.s1?.I_cont_A_rms);
  if (t.s1 && s1 !== null) out.push({ key: 'points.tempTile.s1', args: { current: Math.round(s1 * 10) / 10, part: t.s1.limiting_part ?? '—' } });
  const air = num(t.cooling_air_speed_mps);
  if (air !== null && air > 0) out.push({ key: 'points.tempTile.air', args: { speed: air } });
  return out;
}

/** The temperature tiles of a stored duty point: `[]` when the point stores no temperature at all (nothing is drawn, never "-"). */
export function temperatureTiles(duty: MotorViewDuty | null | undefined): TempTile[] {
  if (!duty) return [];
  const d = duty.thermal?.duty_temps;
  const em = duty.sources?.em;
  const tiles: TempTile[] = [];
  const spec = [
    { id: 'magnetTemp' as const, labelKey: 'points.tempTile.magnet', what: 'magnet' },
    { id: 'windingTemp' as const, labelKey: 'points.tempTile.winding', what: 'winding' },
  ];
  for (const s of spec) {
    const isMagnet = s.what === 'magnet';
    const loop = isMagnet ? real(d?.magnet_c) : real(d?.winding_c);
    const hottest = isMagnet ? real(d?.magnet_max_c) : null;
    if (d && d.kind === 'coupled' && loop !== null) {
      const tip: Tip = [{ key: `points.tempTile.coupled.${s.what}`, args: { day: day(d.provenance) } }];
      if (hottest !== null && hottest !== loop) tip.push({ key: 'points.tempTile.hottestMagnet', args: { value: Math.round(hottest) } });
      tip.push(...basisLines(duty.thermal!));
      tiles.push({ id: s.id, labelKey: s.labelKey, value: loop, unit: '°C', d: 0, basis: 'coupled', muted: false, tip });
      continue;
    }
    if (d && d.kind === 'thermal' && loop !== null) {
      const tip: Tip = [{ key: `points.tempTile.thermal.${s.what}`, args: { day: day(d.provenance) } }, ...basisLines(duty.thermal!)];
      tiles.push({ id: s.id, labelKey: s.labelKey, value: loop, unit: '°C', d: 0, basis: 'thermal', muted: false, tip });
      continue;
    }
    // the EM run's own temperature: the setpoint it was solved at, not a result
    const input = real(isMagnet ? em?.magnet_temp_c : em?.coil_temp_c);
    if (input !== null && em?.kind !== 'coupled') {
      tiles.push({ id: s.id, labelKey: s.labelKey, value: input, unit: '°C', d: 0, basis: 'em_input', muted: true,
        tip: [{ key: `points.tempTile.emInput.${s.what}` }] });
    }
  }
  return tiles;
}

/** Where the temperatures row sits in the result grid: in the ordinary Configure it comes after the losses row and before the
 *  electrical row (DC bus, voltages, current density).  Returns the index of the first row that holds an electrical cell, else
 *  the number of rows (the row goes last). */
export function temperatureRowIndex(grid: Array<Array<{ id: string }>>): number {
  const electrical = new Set(['dcBus', 'vLine', 'vPhase', 'vLineRms', 'vPhaseRms', 'currDensity']);
  const at = grid.findIndex((row) => row.some((c) => electrical.has(c.id)));
  return at >= 0 ? at : grid.length;
}
