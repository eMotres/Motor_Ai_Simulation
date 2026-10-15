/**
 * Undo / redo for the OPTIMIZATION VARIABLES list (Optimize / Sweep study / DOE
 * share it).  Owner 2026-10-15: "+ Add all geometry" by accident added 16
 * variables and the only way back was "Remove all", which also deleted the
 * variables he had before.
 *
 * Pure and dependency-free (no React, no store): `node --test` loads this file
 * directly (lib/__tests__/optVarsHistory.test.mjs) and the panel wires it to
 * the zustand store.
 *
 * A snapshot is the WHOLE variations map as it was before an action, plus the
 * label of that action.  The store replaces variation objects immutably, so
 * snapshots are plain references - restoring one gives back exactly the list the
 * user had.
 */

export type Vars = Record<string, unknown>;

export interface Snap {
  /** the variations map as it was BEFORE the action */
  vars: Vars;
  /** what the action was ("Add all geometry (16 variables)") */
  label: string;
  /** coalescing key (a range field being typed in) or null */
  key: string | null;
  /** wall-clock ms of the action */
  at: number;
}

export interface History {
  past: Snap[];
  future: Snap[];
}

/** Most recent states kept. */
export const HISTORY_LIMIT = 30;
/** Edits to the same field closer together than this are ONE undo step. */
export const COALESCE_MS = 1500;

export const emptyHistory = (): History => ({ past: [], future: [] });

export const canUndo = (h: History): boolean => h.past.length > 0;
export const canRedo = (h: History): boolean => h.future.length > 0;
export const undoLabel = (h: History): string | null =>
  h.past.length ? h.past[h.past.length - 1].label : null;
export const redoLabel = (h: History): string | null =>
  h.future.length ? h.future[h.future.length - 1].label : null;

/**
 * Record an action about to change `before` (the variations map as it is NOW).
 * A new action clears the redo branch.  With a `key`, a second edit of the same
 * field within COALESCE_MS extends the previous step instead of adding one, so
 * typing "7.45" is one Ctrl+Z, not four.
 */
export function record(
  h: History,
  before: Vars,
  label: string,
  opts: { key?: string | null; now?: number; limit?: number } = {},
): History {
  const key = opts.key ?? null;
  const now = opts.now ?? Date.now();
  const limit = opts.limit ?? HISTORY_LIMIT;
  const last = h.past[h.past.length - 1];
  if (key !== null && last && last.key === key && now - last.at <= COALESCE_MS && h.future.length === 0) {
    // extend: keep the ORIGINAL pre-edit snapshot, move the clock forward
    const past = h.past.slice(0, -1).concat({ ...last, at: now, label });
    return { past, future: [] };
  }
  let past = h.past.concat({ vars: before, label, key, at: now });
  if (past.length > limit) past = past.slice(past.length - limit);
  return { past, future: [] };
}

export interface Step { history: History; vars: Vars; label: string }

/** Step back: returns the variations map to restore, or null if nothing to undo. */
export function undo(h: History, current: Vars): Step | null {
  const last = h.past[h.past.length - 1];
  if (!last) return null;
  return {
    history: {
      past: h.past.slice(0, -1),
      future: h.future.concat({ vars: current, label: last.label, key: null, at: last.at }),
    },
    vars: last.vars,
    label: last.label,
  };
}

/** Step forward again after an undo. */
export function redo(h: History, current: Vars): Step | null {
  const next = h.future[h.future.length - 1];
  if (!next) return null;
  return {
    history: {
      past: h.past.concat({ vars: current, label: next.label, key: null, at: next.at }),
      future: h.future.slice(0, -1),
    },
    vars: next.vars,
    label: next.label,
  };
}

// ── Keyboard guard ───────────────────────────────────────────────────────────

export interface KeyLike {
  key: string;
  ctrlKey?: boolean;
  metaKey?: boolean;
  shiftKey?: boolean;
  altKey?: boolean;
}

export interface TargetLike {
  tagName?: string;
  type?: string;
  isContentEditable?: boolean;
  readOnly?: boolean;
  disabled?: boolean;
}

// <input> types where the browser has its own text undo.
const TEXT_INPUT_TYPES = new Set([
  '', 'text', 'search', 'number', 'email', 'url', 'tel', 'password',
]);

/** True when Ctrl+Z belongs to the browser (a field the user is typing in). */
export function isEditableTarget(t: TargetLike | null | undefined): boolean {
  if (!t) return false;
  if (t.isContentEditable) return true;
  const tag = String(t.tagName ?? '').toUpperCase();
  if (tag === 'TEXTAREA') return !t.disabled;
  if (tag === 'INPUT') {
    if (t.disabled) return false;
    return TEXT_INPUT_TYPES.has(String(t.type ?? '').toLowerCase());
  }
  return false;
}

/**
 * What a keydown inside the optimization panel means: 'undo' (Ctrl/Cmd+Z),
 * 'redo' (Ctrl/Cmd+Shift+Z, Ctrl+Y), or null (not ours - including every
 * undo/redo typed into an editable field, where the browser's own undo applies).
 */
export function undoKeyAction(e: KeyLike, target: TargetLike | null | undefined): 'undo' | 'redo' | null {
  if (e.altKey) return null;
  const mod = !!(e.ctrlKey || e.metaKey);
  if (!mod) return null;
  const k = String(e.key ?? '').toLowerCase();
  let act: 'undo' | 'redo' | null = null;
  if (k === 'z') act = e.shiftKey ? 'redo' : 'undo';
  else if (k === 'y' && !e.shiftKey && e.ctrlKey) act = 'redo';
  if (!act) return null;
  if (isEditableTarget(target)) return null;
  return act;
}
