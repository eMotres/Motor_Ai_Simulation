import test from 'node:test';
import assert from 'node:assert/strict';
import { torqueDisplaySign } from '../torqueSign.ts';

test('generator mode flips a negative torque to the positive braking magnitude', () => {
  assert.equal(torqueDisplaySign('generator', -0.1233), -1);
  assert.equal(torqueDisplaySign('generator', -175), -1);
});
test('a positive generator torque, and any motor-mode result, keep the solver sign', () => {
  assert.equal(torqueDisplaySign('generator', 0.5), 1);
  assert.equal(torqueDisplaySign('motor', -0.1), 1);
  assert.equal(torqueDisplaySign('motor', 12), 1);
});
test('unknown mode or value never flips', () => {
  assert.equal(torqueDisplaySign(undefined, -1), 1);
  assert.equal(torqueDisplaySign('generator', null), 1);
  assert.equal(torqueDisplaySign('generator', NaN), 1);
  assert.equal(torqueDisplaySign('generator', 0), 1);
});
