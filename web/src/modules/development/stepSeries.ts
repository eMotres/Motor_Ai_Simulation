/** The optimization-steps view of a project motor (owner 2026-10-26): step chip labels and the "how a value changed over the
 *  steps" series.  Pure functions (no React, no window): `node --test` imports this file. */
import { POINT_TILES, finite, pointOperating, tileValue, type PointTileSpec } from './pointsOnly.ts';
import type { MotorPoint, MotorPointVariant } from './types';

/** Tokens of a die name: words split at spaces, underscores and slashes. */
const tokens = (name: string): string[] => name.split(/[\s_/]+/).filter(Boolean);

/** The short name of each die: the shortest token that no OTHER die of the list has; when there is none (one die, or dies
 *  that share every token) the last token of the name.  `dies` may repeat (one entry per step); the answer is per distinct die. */
export function dieShortNames(dies: ReadonlyArray<string>): Map<string, string> {
  const distinct = Array.from(new Set(dies));
  const out = new Map<string, string>();
  for (const die of distinct) {
    const mine = tokens(die);
    const others = new Set(distinct.filter((d) => d !== die).flatMap((d) => tokens(d).map((x) => x.toLowerCase())));
    // one die for every step: nothing tells it apart from the others, so the last token of its name
    const unique = distinct.length === 1 ? [] : mine.filter((tk) => !others.has(tk.toLowerCase()));
    const pick = unique.length ? unique.reduce((a, b) => (b.length < a.length ? b : a)) : (mine[mine.length - 1] ?? die);
    out.set(die, pick);
  }
  return out;
}

/** "<die short> . <configuration>" of every step, same order as `variants`. */
export function stepLabels(variants: ReadonlyArray<Pick<MotorPointVariant, 'die' | 'canonical_die' | 'config'>>): string[] {
  const short = dieShortNames(variants.map((v) => v.canonical_die || v.die));
  return variants.map((v) => `${short.get(v.canonical_die || v.die) ?? v.die} · ${v.config}`);
}

/** One selectable value of the steps chart. */
export interface SeriesSpec { id: string; unit: string; d: number; value: (point: MotorPoint | null | undefined) => number | null }

const fromTile = (tileId: string, id = tileId): SeriesSpec => {
  const spec = POINT_TILES.find((t) => t.id === tileId) as PointTileSpec;
  return { id, unit: spec.unit, d: spec.d, value: (p) => tileValue(p, spec) };
};

/** The values the chart can show, in the switch's order.  A value appears only when at least two steps store it. */
export const SERIES: SeriesSpec[] = [
  fromTile('torque'),
  fromTile('efficiency'),
  fromTile('losses'),
  fromTile('ripple'),
  // the current is the duty's INPUT: a step enters this series only when the same duty also holds a real result (a computed torque)
  { id: 'current', unit: 'A', d: 1, value: (p) => (tileValue(p, POINT_TILES.find((t) => t.id === 'torque') as PointTileSpec) === null ? null : pointOperating(p).currentA) },
  fromTile('j'),
  fromTile('vPhase'),
  fromTile('vLine'),
  fromTile('mass'),
  fromTile('torqueMass'),
];

export interface StepSeriesPoint { step: number; label: string; value: number | null; die: string }

/** The chosen value for the chosen mode (duty) at every step: a step without it is a GAP (null), never 0. */
export function stepSeries(variants: ReadonlyArray<MotorPointVariant>, duty: string | null, spec: SeriesSpec): StepSeriesPoint[] {
  const labels = stepLabels(variants);
  return variants.map((v, i) => {
    const point = duty ? v.points.find((p) => p.duty === duty) : null;
    const raw = point ? spec.value(point) : null;
    return { step: v.step, label: labels[i], value: finite(raw), die: v.canonical_die || v.die };
  });
}

/** The values stored for at least two steps in this mode: what the switch offers. */
export function availableSeries(variants: ReadonlyArray<MotorPointVariant>, duty: string | null): SeriesSpec[] {
  return SERIES.filter((spec) => stepSeries(variants, duty, spec).filter((p) => p.value !== null).length >= 2);
}

/** The value to show: the remembered one while it is still offered, else the first offered, else null. */
export function pickSeries(available: ReadonlyArray<SeriesSpec>, wanted: string | null | undefined): SeriesSpec | null {
  return available.find((s) => s.id === wanted) ?? available[0] ?? null;
}
