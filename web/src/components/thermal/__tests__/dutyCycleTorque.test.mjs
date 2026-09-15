/**
 * node --test — the duty cycle's TORQUE profile (2026-09-15).
 *
 * The panel under T(t) says what the shaft delivers while it heats up.  Three
 * things are worth pinning, and nothing else here is:
 *
 *   • the torque of a segment is the torque of the DUTY that runs in it, taken
 *     from the configuration's entries (never from the thermal solve, which is
 *     told losses and speeds and no torque at all);
 *   • a named duty with no stored torque is REFUSED, not drawn as a zero — the
 *     whole failure this guards against is a flat step that looks like an
 *     answer;
 *   • the mean is TIME-WEIGHTED over the cycle, because that is the number a
 *     gearbox behind the joint is sized on, and an arithmetic mean of two
 *     segments of unequal length is a different (wrong) number.
 */
import test from 'node:test';
import assert from 'node:assert/strict';

import {
  dutyTorqueNm, missingTorques, torqueByDuty, torqueProfile,
} from '../dutyCycleTorque.ts';

/* The live S3 of the Ø85 robot motor: 15 s at the peak duty, 45 s at rated. */
const SEGS = [{ duty: 'peak 200С wire 120C NdFeB', t_s: 15 },
              { duty: 'rated', t_s: 45 }];
const TQ = { 'peak 200С wire 120C NdFeB': 7.6, rated: 3.0 };

test('a duty states its own torque, or its run is asked', () => {
  assert.equal(dutyTorqueNm({ torque_nm: 7.6 }), 7.6);
  // no entry torque: the 2-D mean times the 3-D end-effect factor
  assert.equal(dutyTorqueNm({ summary: { T_em_avg_Nm: 2.5,
                                         end3d: { k_flux: 1.2 } } }), 3.0);
  // …and k = 1 when the passport has none
  assert.equal(dutyTorqueNm({ summary: { T_em_avg_Nm: 4 } }), 4);
  assert.equal(dutyTorqueNm({}), null);
  assert.equal(dutyTorqueNm(null), null);
  assert.deepEqual(
    torqueByDuty([{ name: 'rated', torque_nm: 3.0 },
                  { name: 'peak', torque_nm: 7.6 },
                  { name: 'nothing' }]),
    { rated: 3.0, peak: 7.6 });
});

test('the step is the duty running in each segment', () => {
  const p = torqueProfile(SEGS, TQ, 60);
  assert.deepEqual(p.steps.map((s) => [s.t0, s.t1, s.nm]),
                   [[0, 15, 7.6], [15, 60, 3.0]]);
  // stepAfter: the segment boundaries, each carrying its own segment's value
  assert.deepEqual(p.rows, [{ t: 0, nm: 7.6 }, { t: 15, nm: 3.0 },
                            { t: 60, nm: 3.0 }]);
  assert.equal(p.peakNm, 7.6);
  assert.equal(p.spanS, 60);
});

test('the cycle\'s own sample grid is read at any instant', () => {
  // the hover must be able to answer about t = 10 s, and the answer there is
  // the PEAK segment's torque — not the nearest boundary's
  const p = torqueProfile(SEGS, TQ, 60, [0, 10, 15, 30, 48, 60]);
  assert.deepEqual(p.rows.map((r) => [r.t, r.nm]),
                   [[0, 7.6], [10, 7.6], [15, 3.0], [30, 3.0], [48, 3.0],
                    [60, 3.0]]);
  // samples outside the axis are dropped, boundaries are never lost
  const clipped = torqueProfile(SEGS, TQ, 60, [-5, 15, 90]);
  assert.deepEqual(clipped.rows.map((r) => r.t), [0, 15, 60]);
  // …and the mean is the segments', not the grid's
  assert.equal(Math.round(p.meanNm * 100) / 100, 4.15);
});

test('the mean is time-weighted, not an average of the steps', () => {
  const p = torqueProfile(SEGS, TQ, 60);
  assert.equal(Math.round(p.meanNm * 100) / 100, 4.15);   // (7.6×15+3×45)/60
  assert.notEqual(Math.round(p.meanNm * 100) / 100, 5.3); // (7.6+3)/2
});

test('an unpowered pause is a real zero; an unknown duty is no chart', () => {
  const unpowered = torqueProfile(
    [{ duty: 'peak', t_s: 15 }, { duty: null, t_s: 45 }],
    { peak: 7.6 }, 60);
  assert.equal(unpowered.steps[1].nm, 0);
  assert.equal(unpowered.steps[1].duty, 'unpowered');
  assert.equal(Math.round(unpowered.meanNm * 100) / 100, 1.9);

  assert.equal(torqueProfile(SEGS, { rated: 3.0 }, 60), null);
  assert.deepEqual(missingTorques(SEGS, { rated: 3.0 }),
                   ['peak 200С wire 120C NdFeB']);
  assert.deepEqual(missingTorques(SEGS, TQ), []);
  assert.equal(torqueProfile([], TQ, 60), null);
  assert.equal(torqueProfile(undefined, TQ, 60), null);
});

test('the axis is the cycle result\'s span, not the segments\' sum', () => {
  // segments stop short: the last one is held out to the end of the axis
  const short = torqueProfile(SEGS, TQ, 80);
  assert.equal(short.steps[1].t1, 80);
  assert.equal(short.rows[short.rows.length - 1].t, 80);
  // …and a span shorter than the segments clips them
  const clipped = torqueProfile(SEGS, TQ, 20);
  assert.equal(clipped.steps.length, 2);
  assert.equal(clipped.steps[1].t1, 20);
  assert.equal(clipped.spanS, 20);
  // no span at all: the segments decide
  assert.equal(torqueProfile(SEGS, TQ, null).spanS, 60);
});
