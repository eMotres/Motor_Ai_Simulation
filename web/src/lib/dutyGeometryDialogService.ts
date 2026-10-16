/**
 * The "duty saved on a different geometry" dialog's imperative half.
 *
 * lib/dutyApply.ts is a plain async function shared by two render trees
 * (the Motors catalog's ▶ and the header strip's reload offer,
 * lib/dutyApply.applyDutyEverywhere) — it is not itself a component, so it
 * cannot own dialog state the way ConfigHistoryDialog or PromptDialogs do.
 * This module is the bridge: `askDutyGeometryChoice` (called from
 * dutyApply.ts) publishes a request into a tiny zustand store and returns a
 * Promise; <DutyGeometryDialog/> (mounted once, in ActiveFamilyStrip, which
 * is on screen whenever a duty could be loaded) renders whatever request is
 * live and calls `resolveDutyGeometryChoice` when the owner picks a button.
 *
 * Replaces `window.confirm()` (incident 2026-09-29: an unrounded float
 * dump behind an ambiguous OK/Cancel — the owner read Cancel as "abort"
 * when it meant "keep die geometry", picked OK, and hit a second, unrelated-
 * looking failure because the die was locked).
 */
import { create } from 'zustand';
import type { DutyGeometryChoice, DutyGeometryDiffRow } from './dutyGeometryDiff';

export interface DutyGeometryRequest {
  message: string;
  diffs: DutyGeometryDiffRow[];
  dieLocked: boolean;
}

interface DutyGeometryDialogState {
  request: DutyGeometryRequest | null;
}

export const useDutyGeometryDialogStore = create<DutyGeometryDialogState>(() => ({
  request: null,
}));

// The resolver lives OUTSIDE the store on purpose: it is a function, not
// serialisable state, and only one such dialog is ever open at a time (the
// server that raises the 409 IS the one activate() the owner just awaited).
let resolver: ((choice: DutyGeometryChoice) => void) | null = null;

/** Ask the owner which geometry wins.  Resolves to the owner's choice, or
 *  `null` only when the request was abandoned without an answer (component
 *  unmounted mid-dialog) — `applyDutyEverywhere` treats that exactly like
 *  Cancel: nothing is written. */
export function askDutyGeometryChoice(req: DutyGeometryRequest): Promise<DutyGeometryChoice> {
  return new Promise((resolve) => {
    // A second request while one is already open would strand the first
    // caller forever — resolve it as abandoned before publishing the new one.
    if (resolver) { const prev = resolver; resolver = null; prev(null); }
    resolver = resolve;
    useDutyGeometryDialogStore.setState({ request: req });
  });
}

/** Called by <DutyGeometryDialog/> when the owner picks a button (or closes
 *  it without one, which resolves `null`). */
export function resolveDutyGeometryChoice(choice: DutyGeometryChoice): void {
  const r = resolver;
  resolver = null;
  useDutyGeometryDialogStore.setState({ request: null });
  r?.(choice);
}
