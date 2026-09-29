/**
 * The one-line notice shown, without any user action, when a running sweep
 * got interrupted by an API restart and the backend picked it back up on its
 * own.  Owner 2026-09-19: "нужно, чтобы автоматом это было видно после
 * сбоя" ("it needs to be automatically visible after a crash").
 *
 * The backend already does the actual work — `sweep_journal.py` persists the
 * sweep's request + which points are done, `sweep_resume.py` re-enqueues it
 * at startup (single-user mode only), and the FEM eval cache
 * (`.scan_cache.jsonl`) makes the already-computed points free — and stamps
 * `resumed_from_restart: {at, done_before, total}` on the scan progress
 * payload (`GET /api/optimization/scan/progress`) while it does. This module
 * is just the sentence: pure, no DOM, no fetch, so it is testable with
 * `node --test` (see `lib/runNotice.ts`, the same pattern) and reusable both
 * for SweepStudyPanel's own line and for the cross-tab
 * `sim:run-notice` event, so the notice is seen on whatever tab is open, not
 * only the Sweep tab.
 */

export interface SweepResumeInfo {
  /** ISO timestamp (UTC, 'Z'-suffixed) of the moment the backend resumed. */
  at: string;
  /** How many points were already done before the restart. */
  done_before: number;
  /** Total points in the sweep. */
  total: number;
}

/** `at` (ISO/UTC) rendered as the viewer's own local clock, HH:MM. Falls back
 *  to the raw string when it does not parse — a notice with a garbled time is
 *  still better than one that throws and shows nothing at all. */
export function formatLocalTime(at: string): string {
  const d = new Date(at);
  if (Number.isNaN(d.getTime())) return String(at);
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

/** The sentence itself, shared by the panel's inline line and the run-notice
 *  event. Deliberately plain, lower-case, one clause — the project's "no text
 *  walls" rule; the HelpTip next to it carries the "why". */
export function sweepResumeNoticeText(info: SweepResumeInfo): string {
  const when = formatLocalTime(info.at);
  const done = Math.max(0, Math.trunc(Number(info.done_before) || 0));
  const total = Math.max(0, Math.trunc(Number(info.total) || 0));
  return `sweep resumed after a restart at ${when}: ${done} of ${total} points were already done`;
}
