/**
 * HOW LONG MAY IT RUN — the catalog duty row's chip.
 *
 * Owner, 2026-09-17: *«для каплинга: если где-то выходим за лимиты, нужно
 * посчитать время, за какое мотор проработает до этого лимита»*.  The coupled
 * loop computes it (`coupled_time_to_limit`), the Thermal tab prints it as a
 * line — and the catalog row is where the reader ALREADY is when the question
 * comes up ("can I pull this point?"), so the duty row gets one amber chip:
 *
 *     ⚠ winding 212 °C · 2 m 40 s to limit
 *
 * ONE SHORT LINE, the block's own sentence in the tooltip (the project's
 * no-walls-of-text rule).
 *
 * NOT GATED BY THE DUTY-CYCLE FLAG.  This is not a duty cycle: it reads no
 * cycle block, needs no duty ratio, and the backend computes it on every
 * coupled loop whether `DUTY_CYCLE_ENABLED` is on or off.  A chip that vanished
 * with that flag would hide the answer to a question the owner asked for
 * separately.
 *
 * DEPENDENCY-FREE ON PURPOSE.  `coupledApi` already owns `fmtSecs` and the full
 * `TimeToLimit` shape, but it reads `import.meta.env` at module level and
 * therefore cannot be imported by `node --test`; this module is imported by its
 * test directly, so the rule it encodes is asserted on the SHIPPED code rather
 * than on a copy of it.  The duration wording below is the one rule spelled in
 * four places — `coupled_time_to_limit.fmt_seconds`, `report._secs_words`,
 * `coupledApi.fmtSecs` and here — so one number is called one thing in the
 * panel, the log, the PDF and the catalog.
 */

/** The duty row's half of a stored `time_to_limit` block, as `/api/family/tree`
 *  sends it (`routes.family._time_to_limit_row`).
 *
 *  FLATTENED, and named differently from the block on purpose: the block
 *  carries `limits_c` / `at_point_c` as a dict PER PART, this carries the
 *  LIMITING part's two scalars, and a reader of either shape must not be able
 *  to mistake one for the other. */
export interface DutyTimeToLimit {
  /** 'winding' | 'magnet' | 'bearing' — the part that is reached FIRST */
  part?: string | null;
  /** what that part's own quantity is at this operating point, °C */
  at_point_c?: number | null;
  /** the limit it is judged against, °C */
  limit_c?: number | null;
  over_by_K?: number | null;
  /** seconds to the first crossing, switched on COLD */
  cold_s?: number | null;
  /** …and from the temperatures the rated duty settled at (always shorter);
   *  absent when this configuration has no rated duty with a coupled record */
  rated_s?: number | null;
  /** the block's OWN headline sentence — the tooltip, verbatim */
  note?: string | null;
}

/** A duration a human reads at a glance: "0.8 s", "48 s", "2 m 40 s". */
export function secsWords(s: number | null | undefined): string {
  if (s == null || !Number.isFinite(Number(s)) || Number(s) < 0) return '—';
  const v = Number(s);
  if (v < 10) return `${v.toFixed(1)} s`;
  if (v < 60) return `${Math.round(v)} s`;
  const m = Math.floor(v / 60);
  return `${m} m ${String(Math.round(v - m * 60)).padStart(2, '0')} s`;
}

/** PURE.  The chip's label, or `null` when the row must draw nothing.
 *
 *  `null` means one of four things, and every one of them is an answer:
 *
 *    • there is no block — this duty has never been through the coupled loop,
 *      or its record predates the feature;
 *    • the point is INSIDE every limit it has (the backend then sends no row at
 *      all).  A machine that is not over anything has no time to a limit, and a
 *      chip quoting one would invite planning around a number that is not a
 *      constraint;
 *    • the step response of this network never reaches the limit, so the block
 *      itself quotes NO time (`coupled_time_to_limit`: an asymptote below the
 *      limit is said out loud, never extrapolated).  A chip is one number wide;
 *      with no number there is nothing to put in it, and the Thermal tab is
 *      where that sentence is read;
 *    • the row is malformed.
 *
 *  THE NUMBER IS THE COLD ONE, which is the block's own headline
 *  (`time_to_limit_s`) and what the report prints — with the warm start as the
 *  fallback, and then SAID, because a pull from rated is always the shorter of
 *  the two and a chip that quoted it silently would read as the cold one. */
export function timeToLimitChip(t: DutyTimeToLimit | null | undefined):
    string | null {
  if (!t) return null;
  // `Number(null)` is 0 and 0 is finite, so the null check comes FIRST: a
  // record with no warm start would otherwise report a pull of zero seconds.
  const secsOf = (v: unknown): number | null => {
    if (v == null) return null;
    const n = Number(v);
    return Number.isFinite(n) && n >= 0 ? n : null;
  };
  const cold = secsOf(t.cold_s);
  const rated = secsOf(t.rated_s);
  const secs = cold ?? rated;
  if (secs == null) return null;
  const bits: string[] = [];
  const part = String(t.part ?? '').trim();
  if (part) bits.push(part);
  const at = Number(t.at_point_c);
  if (Number.isFinite(at)) bits.push(`${Math.round(at)} °C`);
  const when = `${secsWords(secs)} to limit${cold == null ? ' from rated' : ''}`;
  return `⚠ ${[bits.join(' '), when].filter(Boolean).join(' · ')}`;
}

/** PURE.  The chip's tooltip: the block's OWN sentence.
 *
 *  Never a second sentence written here — the block's `note` already names the
 *  part, how far over it is, the limit and both starts, and a paraphrase could
 *  disagree with what the Thermal tab and the report show for the same run.
 *  The fallback is used only by a row whose note was lost. */
export function timeToLimitChipTip(t: DutyTimeToLimit | null | undefined):
    string {
  if (!t) return '';
  const note = String(t.note ?? '').trim();
  if (note) return note;
  const part = String(t.part ?? '').trim() || 'a part';
  const lim = Number(t.limit_c);
  const runs: string[] = [];
  if (t.cold_s != null) runs.push(`${secsWords(t.cold_s)} from cold`);
  if (t.rated_s != null) runs.push(`${secsWords(t.rated_s)} from rated`);
  if (!runs.length) return `The ${part} is over its limit at this point.`;
  return `Runs ${runs.join(', ')}, then the ${part} reaches`
    + `${Number.isFinite(lim) ? ` ${Math.round(lim)} °C` : ' its limit'}.`;
}
