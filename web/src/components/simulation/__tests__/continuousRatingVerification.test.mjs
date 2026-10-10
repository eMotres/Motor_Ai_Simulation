import test from 'node:test';
import assert from 'node:assert/strict';
import { continuousRatingLine, continuousRatingTip, s1ResultsAtLine } from './continuousRatingSource.mjs';

const failed = {
  continuous_rating: {
    ok: true, feasible: true, trustworthy: true, converged: true,
    verified: false, record_is_s1: true, I_cont_A_rms: 2.91,
    limiting_part: 'magnet', limits_c: { magnet: 150 },
    temperatures_c: { magnet: 156.1 }, duty_point: { I_phase_rms_A: 25.88 },
    note: 'still 6.1 K over its limit after 2 verification passes',
    headline: '3.0 A rms continuously — the magnet sits on 150 °C',
  },
};

test('legacy optimistic flags cannot override failed S1 verification', () => {
  assert.match(continuousRatingLine(failed), /NOT VERIFIED/);
  assert.equal(s1ResultsAtLine(failed), null);
  const tip = continuousRatingTip(failed);
  assert.match(tip, /156\.1 °C/);
  assert.doesNotMatch(tip, /continuously|holds for ever|sits on 150/);
});

test('failure is visible even without a limiting-part entry', () => {
  const c = { continuous_rating: { ...failed.continuous_rating, limiting_part: undefined } };
  assert.match(continuousRatingLine(c), /NOT VERIFIED/);
});

test('successful verification retains the S1 result-current label', () => {
  const c = { continuous_rating: { ...failed.continuous_rating, verified: true } };
  assert.match(continuousRatingLine(c), /FEM-verified/);
  assert.match(s1ResultsAtLine(c), /2\.9 A rms/);
});

test('missing verification cannot label record values as confirmed S1', () => {
  const c = { continuous_rating: { ...failed.continuous_rating, verified: undefined } };
  assert.equal(s1ResultsAtLine(c), null);
});

test('new failed trial shows its actual temperature, not the fitted-network estimate', () => {
  const c = { continuous_rating: { ...failed.continuous_rating,
    record_is_s1: false, temperatures_c: { magnet: 149.978 },
    verification_trial: { limiting_part: 'magnet', actual_c: 156.1 },
  } };
  const tip = continuousRatingTip(c);
  assert.match(tip, /Last verification magnet: 156\.1 °C/);
  assert.doesNotMatch(tip, /Last verification magnet: 150\.0/);
});
