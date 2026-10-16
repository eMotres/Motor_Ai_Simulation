/**
 * What the Run button says when a solve does not happen.
 *
 * 2026-09-16, production.  "Coupled thermal" was on and the loaded duty was
 * `peak` — an S3 duty — so `POST /api/coupled/run` was refused with a sentence
 * an engineer can act on (the refusal itself is gone since that afternoon: the
 * loop now FINDS the duty ratio, and what arrives here instead is the outcome
 * sentence when the ratio the duty asks for does not fit):
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

/* ── the gateway, not the solver (2026-09-16, production) ───────────────────
   During an API redeploy nginx answers the run with its own 502 page and the
   notice printed it verbatim — `⚠ not solved — <html><head><title>502 Bad
   Gateway…`.  That is markup from a machine that never saw the request, in a
   line written for an engineer, and it says nothing about the motor.  A 502 /
   503 / 504 (or any body that arrives as an HTML page) means one thing here:
   the API is down for a moment.  The outage path already retries, so it is
   PROGRESS, not a failure — one sentence, in the info style. */
/** An HTML page, wherever it starts — the panel prefixes its own words ("⚠ not
 *  solved — ") in front of whatever it caught, so anchoring this at ^ would let
 *  exactly the reported case through. */
const HTML_BODY = /<!doctype\s|<html[\s>]/i;
const GATEWAY_WORDS = /bad gateway|gateway time-?out|service (temporarily )?unavailable/i;
/** A status code where a STATUS CODE goes — never a 502 inside a sentence. */
const GATEWAY_STATUS =
  /^(?:error:\s*)?(?:httpexception:\s*)?(?:http\s*)?(50[234])\b/i;

const RESTARTING = 'server is restarting — try again in a minute';

/** The code behind a gateway outage, when the message names one. */
function gatewayCode(raw: string, stripped: string): string | null {
  for (const s of [raw, stripped]) {
    const m = GATEWAY_STATUS.exec(s.trim());
    if (m) return m[1];
    const w = /\b(50[234])\b[^0-9]{0,24}(bad gateway|gateway time-?out|service (temporarily )?unavailable)/i.exec(s);
    if (w) return w[1];
  }
  return null;
}

/** Is this the gateway answering instead of the API? */
function isGatewayOutage(raw: string, stripped: string): boolean {
  return HTML_BODY.test(raw) || HTML_BODY.test(stripped)
    || GATEWAY_WORDS.test(raw) || GATEWAY_WORDS.test(stripped)
    || GATEWAY_STATUS.test(raw.trim()) || GATEWAY_STATUS.test(stripped.trim());
}

/**
 * Turn a caught run failure into the notice shown beside the Run button.
 *
 * `null` means "say nothing": no failure, or a state the panel already shows
 * somewhere better (the progress strip, the Stop/Continue dialog).
 */
export function runNoticeFor(raw: string | null | undefined): RunNotice | null {
  if (raw == null) return null;
  const original = String(raw).trim();
  let m = original;
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
  // THE GATEWAY, before anything else is read out of the text: an HTML page is
  // not a sentence, the code in it is nginx's rather than the solver's — and a
  // bare "502:" with NOTHING behind it strips to the empty string, so this has
  // to be asked before "nothing to say" is.
  if (isGatewayOutage(original, m)) {
    const code = gatewayCode(original, m);
    return {
      text: RESTARTING,
      full: `The API did not answer this run: the gateway replied `
        + `${code ? `${code} ` : 'with an error page '}`
        + `instead, which is what a redeploy or a restart looks like from here. `
        + `Nothing was solved and nothing was changed — press Run again in a `
        + `minute.`,
      kind: 'info',
    };
  }
  if (!m) return null;

  // A retry in flight is progress being made, not a failure to report as one.
  // …and so is a DUTY CYCLE that does not fit (2026-09-16): the coupled loop
  // solved the machine and the machine cannot hold the ratio the duty asks for,
  // which is the answer the user came for — printing "not solved" over it would
  // be the panel calling a result a failure.  The prefix is written by
  // `coupledApi.coupledRegimeNotice`, so the classification is a contract
  // between two modules and not a guess at prose.
  // A resumed sweep (2026-09-19: lib/sweepResumeNotice.ts) is progress being
  // reported, same as the retry/duty-cycle lines above it — not a failure.
  const kind: RunNoticeKind =
    /reconnecting and re-solving/i.test(m) || /^duty cycle:/i.test(m)
    || /^sweep resumed after a restart/i.test(m)
      ? 'info' : 'error';

  const text = m.length > LINE ? `${m.slice(0, LINE - 1).trimEnd()}…` : m;
  return { text, full: m, kind };
}
