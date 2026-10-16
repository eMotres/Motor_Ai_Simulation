/**
 * THE CONTINUOUS (S1) RATING — the catalog duty row's other chip.
 *
 * Owner, 2026-09-21: the coupled loop gained a third `solve_to` answer beside
 * `steady` / `limits` — `continuous`, the largest current this machine may
 * hold FOR EVER at THIS duty's own saved cooling.  The report already prints
 * it (`Continuous rating (S1)…` rows, `report.continuous_rating_words`) and
 * the datasheet carries it; this is the catalog row's half, right beside the
 * time-to-limit chip (`lib/timeToLimitChip`) where the reader is when they ask
 * "how hard may I run this point":
 *
 *     S1 28.7 A · magnet
 *
 * SAME PRESENCE RULE, SAME DATA PATH as the time-to-limit chip: the backend
 * sends a row (`routes.family._continuous_rating_row`) ONLY when the duty's
 * stored coupled record carries a `continuous_rating` block — a duty that
 * never asked `solve_to: continuous` gets no key at all, which arrives here as
 * `undefined`, and the row draws nothing.
 *
 * ONE SHORT LINE, the block's own sentence in the tooltip (the project's
 * no-walls-of-text rule) — never a paraphrase written twice that could
 * disagree with the report and the datasheet.
 *
 * DEPENDENCY-FREE, same reason as `timeToLimitChip`: no `import.meta.env`, so
 * `node --test` imports the SHIPPED file rather than a copy of it.
 */

/** The duty row's half of a stored `continuous_rating` block, as
 *  `/api/family/tree` sends it (`routes.family._continuous_rating_row`).
 *
 *  FLATTENED, and named differently from the block on purpose — same reason
 *  as `DutyTimeToLimit`: the block carries `temperatures_c` / `limits_c` as a
 *  dict PER PART, this carries the LIMITING part's two scalars, and a reader
 *  of either shape must not be able to mistake one for the other. */
export interface DutyContinuousRating {
  /** the S1 current, A rms — `null` on a refused or untrustworthy search,
   *  which has a sentence to print and never a number */
  i_cont_A?: number | null;
  /** 'winding' | 'magnet' | 'bearing' — the part the rating is limited by */
  part?: string | null;
  /** that part's own temperature AT the rating, °C */
  at_point_c?: number | null;
  /** the limit it is judged against, °C */
  limit_c?: number | null;
  /** the linear torque estimate at the rating, N·m (never negative here) */
  torque_Nm?: number | null;
  /** the cooling the rating is conditional on — this duty's own saved one */
  cooling_label?: string | null;
  /** `false` on a refused search (no part limits, no capacities) or one the
   *  2-D thermal solve could not be trusted to iterate on (non-monotone) */
  feasible?: boolean | null;
  /** the block's OWN sentence — the tooltip, verbatim */
  note?: string | null;
  /** CONFIRMED WITH A REAL EM PASS (owner 2026-09-21, second addendum) —
   *  `true` only once a verification pass landed within 3 K of the card;
   *  absent when none was tried (no card limit to verify against), which
   *  reads exactly as `undefined` does everywhere else on this row: not
   *  drawn, never a false "not verified". */
  verified?: boolean | null;
}

/** PURE.  The chip's label, or `null` when the row must draw nothing.
 *
 *  `null` means one of three things, and every one of them is an answer:
 *
 *    • there is no block — this duty never asked `solve_to: continuous`, or
 *      its record predates the feature;
 *    • the search was refused or untrustworthy, so there IS no current to
 *      quote — the tooltip still carries the reason, but a chip is one number
 *      wide and there is no number for it;
 *    • the row is malformed.
 */
export function continuousRatingChip(
    r: DutyContinuousRating | null | undefined): string | null {
  if (!r) return null;
  if (r.feasible === false) return null;
  const i = Number(r.i_cont_A);
  if (!Number.isFinite(i) || i < 0) return null;
  const bits: string[] = [`S1 ${i.toFixed(1)} A`];
  const part = String(r.part ?? '').trim();
  if (part) bits.push(part);
  let label = bits.join(' · ');
  // A CHECKMARK, when a real electromagnetic pass confirmed this current
  // (owner 2026-09-21, second addendum) — one glyph, never a second chip: an
  // unverified rating stays exactly the chip it always was.
  if (r.verified === true) label += ' ✓';
  return label;
}

/** PURE.  The chip's tooltip: the block's OWN sentence.
 *
 *  Never a second sentence written here, same rule as `timeToLimitChipTip` —
 *  the block's `note` already names the cooling, the limiting part and the
 *  torque estimate, and a paraphrase could disagree with what the report and
 *  the datasheet show for the same run.  The fallback is used only by a row
 *  whose note was lost. */
export function continuousRatingChipTip(
    r: DutyContinuousRating | null | undefined): string {
  if (!r) return '';
  const note = String(r.note ?? '').trim();
  if (note) return note;
  const part = String(r.part ?? '').trim() || 'a part';
  const lim = Number(r.limit_c);
  return `Continuous current at the saved cooling — limited by ${part}`
    + `${Number.isFinite(lim) ? ` (${Math.round(lim)} °C)` : ''}.`;
}
