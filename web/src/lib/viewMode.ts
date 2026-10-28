/**
 * List / Cards choice of an overview page (owner 2026-10-28): Development and Production both offer the two viewers and
 * remember the choice per page and per viewer in the browser (localStorage).  Pure functions (no React) so they run under
 * `node --test`; the toggle and the hook live in components/common/ViewModeToggle.tsx.
 *
 * Every read and write is wrapped in try/catch: blocked / full / missing storage never breaks a page, it just forgets.
 */

export type ViewMode = 'list' | 'cards';

export const VIEW_MODE_KEYS = {
  development: 'aerostator.view.development',
  production: 'aerostator.view.production',
} as const;

export interface ViewModeStorage { getItem(key: string): string | null; setItem(key: string, value: string): void }

/** The mode a stored value stands for, or null for anything else. */
export const parseViewMode = (raw: unknown): ViewMode | null => (raw === 'list' || raw === 'cards' ? raw : null);

function defaultStorage(): ViewModeStorage | null {
  try { return typeof localStorage === 'undefined' ? null : localStorage; } catch { return null; }
}

/** The remembered mode of a page, or `fallback` when nothing valid is stored (empty, stale or blocked storage). */
export function readViewMode(key: string, fallback: ViewMode, storage: ViewModeStorage | null = defaultStorage()): ViewMode {
  try { return parseViewMode(storage?.getItem(key)) ?? fallback; } catch { return fallback; }
}

export function writeViewMode(key: string, mode: ViewMode, storage: ViewModeStorage | null = defaultStorage()): void {
  try { storage?.setItem(key, mode); } catch { /* blocked or full - the choice just is not remembered */ }
}
