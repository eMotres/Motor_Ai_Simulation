/** The optimization-steps view of a project motor (owner 2026-10-26): step chip labels.  Pure functions (no React, no window): `node --test` imports this file. */
import type { MotorPointVariant } from './types';

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
