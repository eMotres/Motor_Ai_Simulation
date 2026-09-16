/**
 * What the Run button says when a solve does not happen.
 *
 * 2026-09-16, production.  "Coupled thermal" was on and the loaded duty was
 * `peak` — an S3 duty — so `POST /api/coupled/run` was refused, correctly and
 * with a sentence an engineer can act on:
 *
 *   422 duty 'peak' is an S3 duty — 25.0 % of a 60.0 s cycle, not a point the
 *       machine sits at. The coupled loop iterates … until they SETTLE …
 *
 * The message was caught and stored — and then rendered inside the waveforms
 * panel, thousands of pixels BELOW the fold, while the Run button sits at the
 * bottom of the left rail.  On screen, pressing Run did nothing at all: the
 * button flicked back to "Re-run Simulation" and the reason reached only the
 * browser console.
 *
 * This module is the pure half of the fix: TransientCharts publishes whatever
 * it caught, SimulationPanel renders THIS function's answer next to the button.
 * Keeping it pure is what makes the routing testable without a DOM.
 */

export type RunNoticeKind = 'error' | 'info';

export interface RunNotice {
  /** One line, short enough for the rail — the full text goes in the tooltip. */
  text: string;
  /** The whole message, untruncated, for the `title=` tooltip. */
  full: string;
  kind: RunNoticeKind;
}

/** How much fits on one line under the button before the tooltip takes over. */
const LINE = 160;

/**
 * Turn a caught run failure into the notice shown beside the Run button.
 *
 * `null` means "say nothing": no failure, or a state the panel already shows
 * somewhere better (the progress strip, the Stop/Continue dialog).
 */
export function runNoticeFor(raw: string | null | undefined): RunNotice | null {
  if (raw == null) return null;
  let m = String(raw).trim();
  if (!m) return null;

  // The user's own Stop.  Not a failure — and the button's own caption already
  // says "Stopped — Run to resume…", so a red line under it would be noise.
  if (/^cancelled\.?$/i.test(m)) return null;

  // Strip the envelopes the message arrives in.  `String(new Error(x))` is
  // "Error: x"; the backend's own wrapper is "HTTPException: 422: x".
  m = m.replace(/^Error:\s*/i, '').replace(/^HTTPException:\s*\d+:\s*/i, '').trim();

  // A refusal that reached us as the raw body rather than the sentence inside
  // it — {"detail": "…"} or {"detail": {"error": "…"}}.  Show the sentence.
  if (m.startsWith('{')) {
    try {
      const d = JSON.parse(m)?.detail;
      const inner = typeof d === 'string' ? d : d?.error;
      if (typeof inner === 'string' && inner.trim()) m = inner.trim();
    } catch { /* not JSON after all — keep the text we have */ }
  }
  if (!m) return null;

  // A retry in flight is progress being made, not a failure to report as one.
  const kind: RunNoticeKind =
    /reconnecting and re-solving/i.test(m) ? 'info' : 'error';

  const text = m.length > LINE ? `${m.slice(0, LINE - 1).trimEnd()}…` : m;
  return { text, full: m, kind };
}
