import test from 'node:test';
import assert from 'node:assert/strict';
import { currentPerInverter, equivalentPhaseCurrent, phaseSetCount, safeConfigureParallelPaths, scaleMotor } from '../motorScaling.ts';

test('six-phase Configure presents half current per inverter and restores equivalent total internally', () => {
  const six = { phase_count: 6 };
  assert.equal(phaseSetCount(six), 2);
  assert.equal(currentPerInverter(six, 80), 40);
  assert.equal(equivalentPhaseCurrent(six, 40), 80);
});

test('three-phase current display and scaling basis stay unchanged', () => {
  const three = {};
  assert.equal(phaseSetCount(three), 1);
  assert.equal(currentPerInverter(three, 40), 40);
  assert.equal(equivalentPhaseCurrent(three, 40), 40);
  const p = { N0: 8, L0_mm: 20, wireH0_mm: 0.5, I0_A: 40, rpm0: 3000, nP0: 1,
    T0_Nm: 1, Vemf0_peak_V: 20, R0_ohm: 0.02, endWindFrac: 0.2, Pfe0_W: 4, Pmag0_W: 2 };
  const k = { N: 8, L_mm: 20, wireH_mm: 0.5, nP: 1, I_A: 40, rpm: 3000 };
  assert.deepEqual(scaleMotor(p, k), scaleMotor({ ...p, phase_count: 3, phase_sets: 1 }, k));
});

test('six-phase UI entry maps to the same analytic result as its 3-phase equivalent point', () => {
  const p3 = { N0: 8, L0_mm: 20, wireH0_mm: 0.5, I0_A: 80, rpm0: 3000, nP0: 2,
    T0_Nm: 1, Vemf0_peak_V: 20, R0_ohm: 0.02, endWindFrac: 0.2, Pfe0_W: 4, Pmag0_W: 2 };
  const p6 = { ...p3, phase_count: 6, phase_sets: 2,
    current_basis: 'equivalent_three_phase_total',
    six_phase: { current_per_inverter_A: 40, voltage_per_set_V: 31 } };
  const sixPoint = { N: 8, L_mm: 20, wireH_mm: 0.5, nP: 1,
    I_A: equivalentPhaseCurrent({ phase_count: 6 }, 40), rpm: 3000 };
  sixPoint.nP = 2;
  const threePoint = { ...sixPoint, I_A: 80 };
  const sixResult = scaleMotor(p6, sixPoint, 14);
  const threeResult = scaleMotor(p3, threePoint, 14);
  for (const key of ['T_Nm', 'Vphase_peak_V', 'Vline_peak_V', 'psi_pm_mWb', 'P_cu_W', 'P_fe_W', 'P_mag_W']) {
    assert.ok(Math.abs(sixResult[key] - threeResult[key]) <= 1e-12, `${key}: six=${sixResult[key]}, three=${threeResult[key]}`);
  }
  assert.equal(currentPerInverter(p6, 80), 40);
  assert.equal(sixResult.Vphase_peak_V, threeResult.Vphase_peak_V);  // full phase voltage per inverter set
  assert.throws(() => scaleMotor(p6, { ...sixPoint, nP: 1 }, 14), /odd or single parallel path/);
});

test('stale six-phase path metadata gets a render-safe path but remains invalid physics input', () => {
  const stale = { phase_count: 6, nP0: 1 };
  assert.equal(safeConfigureParallelPaths(stale, 1), 2);
  assert.equal(safeConfigureParallelPaths({ phase_count: 6, nP0: 4 }, 2), 4);
  assert.equal(safeConfigureParallelPaths({ phase_count: 3, nP0: 1 }, 3), 3);
});
