/**
 * node --test — THE CATALOG DUTY ROW's "how long may it run" chip.
 *
 * Owner, 2026-09-17: *«для каплинга: если где-то выходим за лимиты, нужно
 * посчитать время, за какое мотор проработает до этого лимита»*, and the chip
 * is where that answer meets the reader in the catalog:
 *
 *     ⚠ winding 212 °C · 2 m 40 s to limit
 *
 * Five rules are worth pinning and nothing else here is:
 *
 *   1. a point INSIDE every limit draws nothing.  The backend sends no row for
 *      it at all, and a chip quoting a time for a limit the machine respects
 *      would invite planning around a number that is not a constraint;
 *   2. an over-limit point with NO time draws nothing either — the block itself
 *      refuses to extrapolate a step response that settles below the limit, and
 *      a chip is one number wide;
 *   3. the number is the COLD one (the block's own headline), and a chip that
 *      had to fall back to the warm start SAYS so — a pull from rated is always
 *      the shorter of the two;
 *   4. the label is one short line; the block's own sentence is the tooltip,
 *      never a paraphrase that could disagree with the Thermal tab and the PDF;
 *   5. the duration wording is `coupled_time_to_limit.fmt_seconds`' wording.
 *
 * The module is dependency-free (no `import.meta.env`), so node imports the
 * SHIPPED file rather than a copy of it — see the header of `timeToLimitChip`.
 */
import test from 'node:test';
import assert from 'node:assert/strict';

import { secsWords, timeToLimitChip, timeToLimitChipTip }
  from '../../../lib/timeToLimitChip.ts';

/** The L13 peak row, as `/api/family/tree` sends it. */
const PEAK = {
  part: 'winding',
  at_point_c: 212.4,
  limit_c: 200,
  over_by_K: 12.4,
  cold_s: 160.2,
  rated_s: 65.4,
  note: 'over the winding limit by 12 K — reaches 200 °C after 2 m 40 s from '
    + 'cold, 1 m 05 s from rated',
};

/* ── nothing to say ──────────────────────────────────────────────────────── */

test('a duty with no coupled record draws no chip', () => {
  assert.equal(timeToLimitChip(null), null);
  assert.equal(timeToLimitChip(undefined), null);
  assert.equal(timeToLimitChipTip(null), '');
});

test('a point inside every limit draws no chip', () => {
  // The backend sends no row at all for it (`_time_to_limit_row` returns None),
  // which arrives here as an absent key.
  assert.equal(timeToLimitChip(undefined), null);
});

test('an over-limit point the network never reaches quotes no time', () => {
  const settles = { ...PEAK, cold_s: null, rated_s: null,
    note: 'over the winding limit by 12 K — the step response of this network '
      + 'never reaches 200 °C, so no time is quoted' };
  assert.equal(timeToLimitChip(settles), null,
    'a chip is one number wide; with no number there is nothing to draw');
  // …but the sentence itself survives, for whoever opens the Thermal tab.
  assert.match(timeToLimitChipTip(settles), /never reaches 200 °C/);
});

test('a malformed row is not a chip', () => {
  assert.equal(timeToLimitChip({ part: 'winding', cold_s: NaN }), null);
  assert.equal(timeToLimitChip({ part: 'winding', cold_s: 'soon' }), null);
  assert.equal(timeToLimitChip({ part: 'winding', cold_s: -4 }), null);
});

/* ── the label ───────────────────────────────────────────────────────────── */

test('the owner\'s own example, to the character', () => {
  assert.equal(timeToLimitChip(PEAK), '⚠ winding 212 °C · 2 m 40 s to limit');
});

test('the chip stays one short line', () => {
  assert.ok(timeToLimitChip(PEAK).length <= 44,
    'a duty row has one line; everything else is the tooltip');
});

test('the number is the COLD one — the block\'s own headline', () => {
  // The warm start is always shorter, and the panel, the report and this chip
  // must not disagree about which of the two "the time" is.
  assert.match(timeToLimitChip(PEAK), /2 m 40 s/);
  assert.doesNotMatch(timeToLimitChip(PEAK), /1 m 05 s/);
});

test('a row with only a warm start says which start it is quoting', () => {
  const warm = { ...PEAK, cold_s: null };
  assert.equal(timeToLimitChip(warm),
    '⚠ winding 212 °C · 1 m 05 s to limit from rated');
});

test('the magnet and the bearing are named the same way', () => {
  assert.equal(timeToLimitChip({ part: 'magnet', at_point_c: 154.6,
    limit_c: 150, cold_s: 48.2 }), '⚠ magnet 155 °C · 48 s to limit');
  assert.equal(timeToLimitChip({ part: 'bearing', at_point_c: 131.2,
    limit_c: 120, cold_s: 903 }), '⚠ bearing 131 °C · 15 m 03 s to limit');
});

test('a row missing the temperature still says the time', () => {
  assert.equal(timeToLimitChip({ part: 'winding', cold_s: 12.5 }),
    '⚠ winding · 13 s to limit');
  assert.equal(timeToLimitChip({ cold_s: 12.5 }), '⚠ 13 s to limit');
});

/* ── the tooltip ─────────────────────────────────────────────────────────── */

test('the tooltip is the block\'s OWN sentence, verbatim', () => {
  assert.equal(timeToLimitChipTip(PEAK), PEAK.note);
});

test('a row whose note was lost still reads as a sentence', () => {
  const t = timeToLimitChipTip({ ...PEAK, note: null });
  assert.equal(t, 'Runs 2 m 40 s from cold, 1 m 05 s from rated, then the '
    + 'winding reaches 200 °C.');
});

/* ── the duration wording, shared with the python side ───────────────────── */

test('the same durations the panel, the log and the PDF print', () => {
  assert.equal(secsWords(0.84), '0.8 s');
  assert.equal(secsWords(9.99), '10.0 s');   // < 10 is one decimal
  assert.equal(secsWords(10), '10 s');
  assert.equal(secsWords(48.4), '48 s');
  assert.equal(secsWords(59.6), '60 s');
  assert.equal(secsWords(60), '1 m 00 s');
  assert.equal(secsWords(160.2), '2 m 40 s');
  assert.equal(secsWords(3600), '60 m 00 s');
  assert.equal(secsWords(null), '—');
  assert.equal(secsWords(-1), '—');
});
