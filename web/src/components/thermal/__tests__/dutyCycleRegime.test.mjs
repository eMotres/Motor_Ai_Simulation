/**
 * node --test — THE FOUND REGIME, as the panel words it (2026-09-15).
 *
 * The editor stopped grading a duty ratio and started answering with one.  Four
 * things are worth pinning, and nothing else here is:
 *
 *   • the HEADLINE leads with the allowable ED at the period it is a ratio of,
 *     and a number this machine does not have is left OUT rather than printed
 *     as a dash — a "—" in the one line the user reads at a glance reads as a
 *     failure when it is usually just a limit nobody set;
 *   • the pass/fail chip exists ONLY when "check ED %" was filled.  A verdict on
 *     every answer is exactly the framing the user asked to be rid of;
 *   • a period with NO feasible ratio is dropped from the curve, never drawn at
 *     zero — a point on the floor of an ED axis reads as "0 % is allowed";
 *   • an IMPULSE duty as the calibration point is REFUSED: every conductance is
 *     divided out of that one map, and the steady map of a peak point is a
 *     machine that would have burned.
 */
import test from 'node:test';
import assert from 'node:assert/strict';

import {
  calibrationIssue, checkVerdict, edCycleRows, regimeLine,
} from '../dutyCycleRegime.ts';

/* The L13 Ø85 robot joint, 2026-09-15: 21.6 % of 60 s, 26.6 s from cold. */
const L13 = {
  winding_limit_c: 200,
  ed_allowable_pct: 21.61,
  ed_requested_pct: null,
  ed_limiting_part: 'winding',
  ed_found: true,
  ed_cycle_s: 60,
  at_allowable: { winding_hot_peak_c: 199.88, magnet_peak_c: 111.24,
                  peak_c: { winding: 198.58, stator: 140.69, rotor: 111.45,
                            magnet: 111.24 } },
  ed_vs_cycle: [
    { cycle_s: 10, ed_allowable_pct: 31.59, t_on_s: 3.159,
      winding_hot_peak_c: 198.17, magnet_peak_c: 133.67,
      limiting_part: 'winding' },
    { cycle_s: 30, ed_allowable_pct: 26.54, t_on_s: 7.962,
      winding_hot_peak_c: 199.38, magnet_peak_c: 122.69,
      limiting_part: 'winding' },
    { cycle_s: 60, ed_allowable_pct: 21.49, t_on_s: 12.894,
      winding_hot_peak_c: 199.2, magnet_peak_c: 110.93,
      limiting_part: 'winding' },
  ],
  s2_time_to_limit_s: 26.621,
  s2_limiting_part: 'winding',
  s2_from_rated_s: 20.672,
  s2_from_cycle_mean_s: 14.2,
};

test('the headline is the regime, at the period it is a ratio of', () => {
  assert.equal(
    regimeLine(L13),
    'Allowable ED at 60 s: 21.6 % (limit: winding 200 °C) · S2 from cold 26.6 s'
    + ' · from rated 20.7 s · magnets at the allowable point 111 °C');
});

test('a number this machine does not have is left out, not dashed', () => {
  const bare = regimeLine({ ed_allowable_pct: 40, ed_cycle_s: 30,
                            ed_limiting_part: 'winding', winding_limit_c: 200 });
  assert.equal(bare, 'Allowable ED at 30 s: 40 % (limit: winding 200 °C)');
  assert.ok(!bare.includes('—'));
  // the magnets are the limit when the card carries a maximum and it binds
  assert.match(
    regimeLine({ ed_allowable_pct: 12, ed_cycle_s: 60, magnet_limit_c: 120,
                 ed_limiting_part: 'magnet' }),
    /limit: magnet 120 °C/);
  assert.equal(regimeLine(null), '');
  assert.equal(regimeLine({}), '');
});

test('the pass/fail chip exists only when an ED was asked to be checked', () => {
  assert.equal(checkVerdict(L13), null);          // nothing was asked
  const pass = checkVerdict({ ...L13, ed_requested_pct: 15 });
  assert.equal(pass.ok, true);
  assert.match(pass.text, /^✓ ED 15 % fits — 21.6 % allowed$/);
  const fail = checkVerdict({ ...L13, ed_requested_pct: 25 });
  assert.equal(fail.ok, false);
  assert.match(fail.text, /^✗ ED 25 % over — 21.6 % allowed$/);
  assert.equal(fail.allowedPct, 21.61);
  // asking for exactly the allowable ratio passes — it SITS on the limit
  assert.equal(checkVerdict({ ...L13, ed_requested_pct: 21.61 }).ok, true);
  // …and an ED that could not be judged is not silently a pass
  assert.equal(checkVerdict({ ed_requested_pct: 25,
                              ed_allowable_pct: null }).ok, false);
});

test('the curve is the answer at five periods, and a gap is a gap', () => {
  const rows = edCycleRows(L13);
  assert.deepEqual(rows.map((r) => [r.cycleS, r.edPct, r.tOnS]),
                   [[10, 31.59, 3.159], [30, 26.54, 7.962],
                    [60, 21.49, 12.894]]);
  assert.equal(rows[0].magnetC, 133.67);
  assert.equal(rows[0].limitingPart, 'winding');
  // a period with no feasible ratio is DROPPED, never drawn at zero
  const holed = edCycleRows({ ed_vs_cycle: [
    { cycle_s: 10, ed_allowable_pct: 31.6, t_on_s: 3.16 },
    { cycle_s: 300, ed_allowable_pct: null, t_on_s: null }] });
  assert.deepEqual(holed.map((r) => r.cycleS), [10]);
  // …and the rows come back in period order whatever order they arrived in
  assert.deepEqual(edCycleRows({ ed_vs_cycle: [
    { cycle_s: 60, ed_allowable_pct: 21 },
    { cycle_s: 10, ed_allowable_pct: 31 }] }).map((r) => r.cycleS), [10, 60]);
  assert.deepEqual(edCycleRows(null), []);
  assert.deepEqual(edCycleRows({}), []);
});

test('an impulse duty as the calibration point is refused, not warned about', () => {
  assert.equal(calibrationIssue('', 'rated 120C wire 80C NdFeB').level, 'ok');
  assert.equal(calibrationIssue('rated 120C', 'rated 120C').level, 'ok');

  const peak = calibrationIssue('peak 200C wire 120C NdFeB', 'rated 120C');
  assert.equal(peak.level, 'refuse');
  assert.match(peak.text, /never reaches/);
  for (const n of ['impulse 2 s', 'boost duty', 'burst 5 s', 'overload 3x']) {
    assert.equal(calibrationIssue(n, 'rated').level, 'refuse', n);
  }
  // …but a plain working point is the user's choice, said out loud and allowed
  const other = calibrationIssue('cruise 3000 rpm', 'rated 120C');
  assert.equal(other.level, 'warn');
  assert.match(other.text, /not the rated duty/);
  // a name that merely CONTAINS the letters is not an impulse point
  assert.equal(calibrationIssue('speak-up 12A', 'rated').level, 'warn');
});
