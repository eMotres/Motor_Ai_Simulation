/**
 * "Loaded from history — computed DD.MM HH:MM" — the one line every panel's
 * result header shows when the backend answered from
 * `motor_ai_sim.run_history` (or, for the EM transient, its own older
 * ``.run_ledger`` — same response vocabulary, see routes/simulation.py's
 * `served_from_history` alias) instead of solving.
 *
 * Owner, 2026-09-22: a repeat launch of the same parameters must say so, in
 * one line, with a Recompute the panel wires to a solve carrying `fresh:
 * true`.  This module is the pure half — string formatting only, no fetch,
 * no store — the same split `runNotice.ts` uses, so it is testable with
 * `node --test` and no DOM.
 */

export interface HistoryNotice {
  /** "Loaded from history — computed 21.09 18:20" — no "· Recompute": the
   *  panel renders that half as its own clickable element, since it is an
   *  action and not text. */
  text: string;
}

/** Every backend that stamps `served_from_history` also stamps `computed_at`
 *  as an ISO-8601 string (UTC). `null` on a malformed/missing timestamp —
 *  never a guess: an unreadable date is worse than none. */
export function formatHistoryComputedAt(computedAt: string | null | undefined): string | null {
  if (!computedAt) return null;
  const d = new Date(computedAt);
  if (Number.isNaN(d.getTime())) return null;
  const dd = String(d.getDate()).padStart(2, '0');
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const hh = String(d.getHours()).padStart(2, '0');
  const mi = String(d.getMinutes()).padStart(2, '0');
  return `${dd}.${mm} ${hh}:${mi}`;
}

/**
 * `null` means "say nothing" — the result did not come from history, or it
 * has no readable timestamp (an old-shaped entry, or a clock the browser
 * could not parse) and a notice with no time on it would be a claim this
 * function cannot back up.
 */
export function historyNoticeFor(result: {
  served_from_history?: boolean | null;
  computed_at?: string | null;
} | null | undefined): HistoryNotice | null {
  if (!result || !result.served_from_history) return null;
  const when = formatHistoryComputedAt(result.computed_at);
  if (!when) return null;
  return { text: `Loaded from history — computed ${when}` };
}

/** One line for a History-popover row (Simulation / Coupled panels):
 *  "23 000 rpm, sector, SF 25.13 — 21.09 18:20". `summary` is whatever the
 *  backend's own one-line summary said (mechanical.py's `_rotor_stress_summary`
 *  and siblings); this only appends the readable time, or drops it silently
 *  rather than showing "Invalid Date". */
export function historyRowLabel(entry: {
  summary?: string | null;
  computed_at?: string | null;
} | null | undefined): string {
  if (!entry) return '';
  const summary = (entry.summary ?? '').trim();
  const when = formatHistoryComputedAt(entry.computed_at);
  if (!summary) return when ?? '';
  return when ? `${summary} — ${when}` : summary;
}
