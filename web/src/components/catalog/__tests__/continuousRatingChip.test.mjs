/**
 * node --test — THE CATALOG DUTY ROW's "continuous (S1) rating" chip.
 *
 * Owner, 2026-09-21: the coupled loop gained a third `solve_to` answer — the
 * largest current this machine may hold FOR EVER at this duty's own saved
 * cooling — and the catalog row gets the other chip for it, beside the
 * time-to-limit one (`__tests__/timeToLimitChip.test.mjs`):
 *
 *     S1 28.7 A · magnet
 *
 * Three rules are worth pinning and nothing else here is:
 *
 *   1. absent block (never asked `solve_to: continuous`) draws nothing — same
 *      presence rule as the time-to-limit chip;
 *   2. a refused or untrustworthy search draws no chip either — there is no
 *      current to quote, and a chip is one number wide — but its sentence
 *      still reaches the tooltip;
 *   3. the label is one short line; the block's own sentence is the tooltip,
 *      never a paraphrase that could disagree with the report and the
 *      datasheet.
 *
 * The module is dependency-free (no `import.meta.env`), so node imports the
 * SHIPPED file rather than a copy of it — see the header of
 * `continuousRatingChip`.
 */
import test from 'node:test';
import assert from 'node:assert/strict';

import { continuousRatingChip, continuousRatingChipTip }
  from '../../../lib/continuousRatingChip.ts';

/** The L13 peak duty's S1 rating, as `/api/family/tree` sends it. */
const S1 = {
  i_cont_A: 28.7,
  part: 'magnet',
  at_point_c: 149.7,
  limit_c: 150,
  torque_Nm: 4.2,
  cooling_label: 'air 10 m/s at 30 °C, bore air 40 m/s',
  feasible: true,
  note: 'continuous current at the saved cooling — air 10 m/s at 30 °C, bore '
    + 'air 40 m/s; limited by magnet 149.7 °C of 150 °C; torque est. 4.2 N·m',
};

/* ── nothing to say ──────────────────────────────────────────────────────── */

test('a duty that never asked solve_to: continuous draws no chip', () => {
  assert.equal(continuousRatingChip(null), null);
  assert.equal(continuousRatingChip(undefined), null);
  assert.equal(continuousRatingChipTip(null), '');
});

test('a refused or untrustworthy search draws no chip', () => {
  const refused = { feasible: false, i_cont_A: null,
    note: 'nothing on this machine states a temperature limit' };
  assert.equal(continuousRatingChip(refused), null,
    'a chip is one number wide; a refused search has no number');
  // …but the sentence itself survives, for whoever opens the report.
  assert.match(continuousRatingChipTip(refused), /states a temperature limit/);
});

test('a malformed row is not a chip', () => {
  assert.equal(continuousRatingChip({ part: 'magnet', i_cont_A: NaN }), null);
  assert.equal(continuousRatingChip({ part: 'magnet', i_cont_A: 'lots' }), null);
  assert.equal(continuousRatingChip({ part: 'magnet', i_cont_A: -4 }), null);
});

/* ── the label ───────────────────────────────────────────────────────────── */

test('the owner\'s own example, to the character', () => {
  assert.equal(continuousRatingChip(S1), 'S1 28.7 A · magnet');
});

// ── S1 VERIFICATION (owner 2026-09-21, second addendum) ────────────────────
test('a verified rating carries the checkmark', () => {
  assert.equal(continuousRatingChip({ ...S1, verified: true }),
    'S1 28.7 A · magnet ✓');
});

test('an unverified or never-tried rating stays exactly the chip it was', () => {
  assert.equal(continuousRatingChip({ ...S1, verified: false }),
    'S1 28.7 A · magnet');
  assert.equal(continuousRatingChip(S1), 'S1 28.7 A · magnet');   // no key at all
});

test('the chip stays one short line', () => {
  assert.ok(continuousRatingChip(S1).length <= 24,
    'a duty row has one line; everything else is the tooltip');
});

test('the current is always one decimal', () => {
  assert.equal(continuousRatingChip({ ...S1, i_cont_A: 5 }), 'S1 5.0 A · magnet');
  assert.equal(continuousRatingChip({ ...S1, i_cont_A: 112.36 }),
    'S1 112.4 A · magnet');
});

test('the winding and the bearing are named the same way', () => {
  assert.equal(continuousRatingChip({ i_cont_A: 41.2, part: 'winding' }),
    'S1 41.2 A · winding');
  assert.equal(continuousRatingChip({ i_cont_A: 9.6, part: 'bearing' }),
    'S1 9.6 A · bearing');
});

test('a row missing the part still says the current', () => {
  assert.equal(continuousRatingChip({ i_cont_A: 28.7 }), 'S1 28.7 A');
});

/* ── the tooltip ─────────────────────────────────────────────────────────── */

test('the tooltip is the block\'s OWN sentence, verbatim', () => {
  assert.equal(continuousRatingChipTip(S1), S1.note);
});

test('a row whose note was lost still reads as a sentence', () => {
  assert.equal(continuousRatingChipTip({ part: 'magnet', limit_c: 150 }),
    'Continuous current at the saved cooling — limited by magnet (150 °C).');
  assert.equal(continuousRatingChipTip({}),
    'Continuous current at the saved cooling — limited by a part.');
});
