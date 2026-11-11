/**
 * WHAT an applied summary on the Electromagnetic card is (`sim-apply-summary`'s `detail.source`), and what it says about
 * the waveforms under it.  Pure and import-free, so a node test drives it.
 *
 * Owner 2026-11-11: after switching motors in one session the charts read "⚠ stale (computed before the Sweep design was
 * applied)" until a page reload.  A catalog load pushes its stored summary through the same event a Sweep design uses, and
 * the charts took every applied summary for a Sweep design - including a stored run whose own waveforms were on screen.
 */

/** 'sweep' - a Sweep / Optimization design: its numbers came without waveforms, the charts are from before it;
 *  'stored-run' - a catalog point's stored run: the summary AND its own waveforms (lib/dutyRuns.applyStoredRun);
 *  'stored-summary' - a catalog point's stored numbers with no stored waveforms (lib/dutyLocalApply). */
export type AppliedSource = 'sweep' | 'stored-run' | 'stored-summary';

/** The event's `source`, with anything else (an older sender says nothing) read as a Sweep design. */
export function appliedSourceOf(v: unknown): AppliedSource {
  return v === 'stored-run' || v === 'stored-summary' ? v : 'sweep';
}

/** Why the waveforms under an applied summary are not that summary's run, or `null` when they are (or nothing is
 *  applied).  A stored run's waveforms ARE its summary's: nothing to flag. */
export function appliedChartsNote(applied: boolean, source: AppliedSource): string | null {
  if (!applied) return null;
  if (source === 'sweep') return 'before the Sweep design was applied';
  if (source === 'stored-summary') return 'for another point - this point has stored numbers but no stored waveforms';
  return null;
}
