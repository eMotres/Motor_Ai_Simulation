// node --test: the Configure tuner's k(L) reader (motorScaling.end3dKTable / scaleMotor).
// A multi-row k_flux_vs_L table without k_flux_vs_L_method was written before the 2026-11-24 Stage A fix (its rows but
// the reference one used the reference length's mid-plane ratio): only the reference value is used at every stack
// length and the result says k_end3d_stale.  A marked table is interpolated as before; marked and unmarked rows never mix.
import test from 'node:test';
import assert from 'node:assert/strict';
import { end3dKTable, scaleMotor } from '../motorScaling.ts';

// shape of a catalog quick passport (0.75 / 1.0 / 1.5 x 12 mm) as stored before the fix
const OLD = { k_flux: 0.9518297570494533, k_flux_vs_L: { '9.0': 0.94985, '12.0': 0.95183, '18.0': 0.95737 } };
const P = (end3d) => ({
  N0: 7, L0_mm: 12, wireH0_mm: 0.6, I0_A: 40, rpm0: 13000, nP0: 1,
  T0_Nm: 0.5, Vemf0_peak_V: 10, R0_ohm: 0.0196, endWindFrac: 0.3,
  Pfe0_W: 15, Pmag0_W: 5, mass0_kg: 0.18, end3d,
});
const K = (L) => ({ N: 7, L_mm: L, wireH_mm: 0.6, nP: 1, I_A: 40, rpm: 13000 });

test('an unmarked multi-row table keeps only its reference row and is stale', () => {
  assert.deepEqual(end3dKTable(OLD), { pts: [[12, OLD.k_flux]], stale: true });
  assert.deepEqual(end3dKTable({ ...OLD, stack_mm: 12 }), { pts: [[12, OLD.k_flux]], stale: true });
  assert.deepEqual(end3dKTable({ k_flux: 0.9, k_flux_vs_L: { '9': 0.8, '12': 0.85 } }), { pts: [], stale: true });
});

test('a marked table, a single row and no table are not stale', () => {
  for (const m of ['own_mid_ratio', 'self_no_2d']) {
    const t = end3dKTable({ ...OLD, k_flux_vs_L_method: m });
    assert.equal(t.stale, false);
    assert.deepEqual(t.pts, [[9, 0.94985], [12, 0.95183], [18, 0.95737]]);
  }
  assert.deepEqual(end3dKTable({ k_flux: 0.96, k_flux_vs_L: { '20.0': 0.96269 } }), { pts: [[20, 0.96269]], stale: false });
  assert.deepEqual(end3dKTable({ k_flux: 0.96 }), { pts: [], stale: false });
  assert.deepEqual(end3dKTable(null), { pts: [], stale: false });
});

test('scaleMotor: a stale table gives the scalar at every length and the flag', () => {
  for (const L of [9, 12, 15, 18]) {
    const r = scaleMotor(P(OLD), K(L), 14);
    assert.equal(r.k_end3d, OLD.k_flux, `k at ${L} mm`);
    assert.equal(r.k_end3d_stale, true);
  }
});

test('scaleMotor: a marked table is interpolated exactly as before, no flag', () => {
  const marked = { ...OLD, k_flux_vs_L_method: 'own_mid_ratio' };
  const r9 = scaleMotor(P(marked), K(9), 14);
  const r15 = scaleMotor(P(marked), K(15), 14);
  assert.equal(r9.k_end3d, 0.94985);
  assert.ok(Math.abs(r15.k_end3d - (0.95183 + (0.95737 - 0.95183) * 0.5)) < 1e-12);
  assert.equal(r15.k_end3d_stale, false);
  const none = scaleMotor(P(null), K(15), 14);
  assert.equal(none.k_end3d, null);
  assert.equal(none.k_end3d_stale, false);
});

// the base length rule follows charge_analytic.k_end_info: the scalar (the factor at the passport's own stack_mm) when
// L0 is within 5 % of it, else the marked table at L0
test('scaleMotor: marked passport with L0 != stack_mm uses the scalar within 5 %, the table beyond', () => {
  const marked = { ...OLD, stack_mm: 12, k_flux_vs_L_method: 'own_mid_ratio' };
  const near = { ...P(marked), L0_mm: 12.4 };
  const rn = scaleMotor(near, K(12.4), 14);
  assert.equal(rn.k_end3d, OLD.k_flux);                       // scalar, not the table at 12.4 mm
  assert.equal(rn.k_end3d_stale, false);
  const far = { ...P(marked), L0_mm: 15 };
  const rf = scaleMotor(far, K(15), 14);
  assert.ok(Math.abs(rf.k_end3d - (0.95183 + (0.95737 - 0.95183) * 0.5)) < 1e-12);   // table at L0 = 15
  // L0 == stack_mm: unchanged
  assert.equal(scaleMotor(P(marked), K(12), 14).k_end3d, 0.95183);
});
