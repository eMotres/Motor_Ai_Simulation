/** Which project the Motors tab highlights (owner 2026-10-26).
 *
 *  The Motors tab marks the geometries (die / configuration) that belong to the currently selected project - a project
 *  selector, defaulting to the LAST OPENED project (the one a motor was last opened from); choosing another project
 *  highlights its motors.  The highlight is a view aid only: access rules (grants, project membership) are untouched,
 *  the tab still lists exactly the motors the caller may see.
 *
 *  Pure functions (no React, no window): `node --test` imports this file. */
import { nullableText, readView, shape, text, writeView, type ViewStorage } from '../../lib/viewMemory.ts';

export interface FocusProject { id: string; /** unknown when the project was opened from a place that only knows its id */ title: string | null }
export interface MotorRef { die: string; config: string; canonical_die?: string }

export const focusScope = (email: string | null | undefined): string =>
  `projects.focus.${(email ?? 'anonymous').trim().toLowerCase() || 'anonymous'}`;

const validFocus = shape<FocusProject>({ id: text, title: nullableText });

export const readFocus = (email: string | null | undefined, storage?: ViewStorage | null): FocusProject | null =>
  readView<FocusProject>(focusScope(email), validFocus, storage);

export const writeFocus = (email: string | null | undefined, focus: FocusProject | null, storage?: ViewStorage | null): void =>
  writeView(focusScope(email), focus, storage);

const norm = (s: string) => s.trim().toLowerCase();
export const motorKey = (die: string, config: string): string => `${norm(die)}\u0000${norm(config)}`;

/** Index of a project's linked motors for O(1) lookups; both the ordinary alias and the canonical die name count. */
export interface HighlightIndex { configs: Set<string>; dies: Set<string> }

export function highlightIndex(motors: readonly MotorRef[] | null | undefined): HighlightIndex {
  const configs = new Set<string>();
  const dies = new Set<string>();
  for (const m of motors ?? []) {
    for (const die of [m.die, m.canonical_die]) {
      if (!die) continue;
      configs.add(motorKey(die, m.config));
      dies.add(norm(die));
    }
  }
  return { configs, dies };
}

export const isConfigHighlighted = (index: HighlightIndex, die: string, config: string): boolean => index.configs.has(motorKey(die, config));
export const isDieHighlighted = (index: HighlightIndex, die: string): boolean => index.dies.has(norm(die));

/** The project to highlight: the remembered one while the caller still sees it, else null (no highlight; never a guess). */
export function pickFocusProject(projects: ReadonlyArray<{ id: string; title: string }>, remembered: FocusProject | null): FocusProject | null {
  if (!remembered) return null;
  const found = projects.find((p) => p.id === remembered.id);
  return found ? { id: found.id, title: found.title } : null;
}
