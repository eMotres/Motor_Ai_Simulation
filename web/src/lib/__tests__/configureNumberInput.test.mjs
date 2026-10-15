import test from 'node:test';
import assert from 'node:assert/strict';
import { shouldCommitConfigureNumber } from '../configureNumberInput.ts';
import { currentForTorque } from '../configuratorPropeller.ts';
import { scaleMotor } from '../motorScaling.ts';

test('untouched or derived read-only Configure numbers never overwrite internal precision', () => {
  assert.equal(shouldCommitConfigureNumber(false, false), false);
  assert.equal(shouldCommitConfigureNumber(false, true), false);
  assert.equal(shouldCommitConfigureNumber(true, true), false);
  assert.equal(shouldCommitConfigureNumber(true, false), true);
});

test('rounded display blur preserves fractional derived current and all dependent results', () => {
  const P = {
    N0: 7, L0_mm: 20, wireH0_mm: 0.6, I0_A: 40, rpm0: 13000, nP0: 1,
    T0_Nm: 0.5, Vemf0_peak_V: 10, R0_ohm: 0.0196, endWindFrac: 0.3,
    Pfe0_W: 15, Pmag0_W: 5, mass0_kg: 0.18,
  };
  const K = { N: 7, L_mm: 20, wireH_mm: 0.6, nP: 1, I_A: 0, rpm: 13000 };
  const derived = currentForTorque(0.346, (I) => I * 0.01, 100);
  assert.equal(derived.ok, true);
  const internalA = derived.I_A;
  const visibleA = Math.round(internalA);
  assert.notEqual(internalA, visibleA);
  const before = scaleMotor(P, { ...K, I_A: internalA }, 14);
  const committedAfterBlur = shouldCommitConfigureNumber(false, false) ? visibleA : internalA;
  const after = scaleMotor(P, { ...K, I_A: committedAfterBlur }, 14);
  assert.equal(committedAfterBlur, internalA);
  assert.deepEqual(
    [after.P_mech_W, after.efficiency, after.P_cu_W],
    [before.P_mech_W, before.efficiency, before.P_cu_W],
  );
  const roundedCommit = scaleMotor(P, { ...K, I_A: visibleA }, 14);
  assert.notEqual(roundedCommit.P_mech_W, before.P_mech_W);
  assert.notEqual(roundedCommit.P_cu_W, before.P_cu_W);
});
