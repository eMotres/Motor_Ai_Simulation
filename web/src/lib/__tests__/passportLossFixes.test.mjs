// node --test (Node >= 22.18: loads the real lib/motorScaling.ts by type
// stripping — no copy of the law to drift).  The tuner side of the three
// passport loss fixes (audit 2026-09-30):
//   1. the end-winding split scales copper with the stack correctly;
//   2. a delta passport with the corrected AC factor keeps its AC copper
//      (the old 1/3 factor was floored to zero);
//   3. EMF / KV read the stored fundamental, and the voltage limit gets the
//      loaded WAVEFORM peak back through the base crest factor.
// (The measured PWM-delta test was removed with the delta code, 2026-10-05.)
import test from 'node:test';
import assert from 'node:assert/strict';
import { scaleMotor } from '../motorScaling.ts';

const base = (over = {}) => ({
  N0: 10, L0_mm: 155, wireH0_mm: 1, I0_A: 562, rpm0: 14200, nP0: 2,
  T0_Nm: 178.3, Vemf0_peak_V: 228.6, Vload0_peak_V: 244.0, R0_ohm: 0.00187,
  endWindFrac: 1 - 1 / 1.355, Pfe0_W: 1286, Pmag0_W: 88, mass0_kg: 27.6,
  loss_grid: {
    I_A: [281, 562, 843], rpm: [7100, 14200, 21300],
    Pfe_W: [[500, 1230, 2130], [490, 1220, 2110], [516, 1280, 2215]],
    Pmag_W: [[8, 29, 57], [18, 66, 133], [37, 134, 271]],
    cuAC: [[1.16, 1.41, 1.75], [1.15, 1.38, 1.70], [1.14, 1.37, 1.67]],
  },
  ...over,
});
const K = (p, over = {}) => ({ N: p.N0, L_mm: p.L0_mm, wireH_mm: p.wireH0_mm,
  nP: p.nP0, I_A: p.I0_A, rpm: p.rpm0, ...over });
const near = (a, b, tol, msg) =>
  assert.ok(Math.abs(a - b) <= tol * Math.abs(b), `${msg}: ${a} vs ${b}`);

test('end winding: R scales as (1-e)·fL + e, not ∝ L', () => {
  const p = base();
  const e = p.endWindFrac;
  for (const f of [0.5, 2]) {
    const r = scaleMotor(p, K(p, { L_mm: p.L0_mm * f }));
    near(r.R_ohm, p.R0_ohm * ((1 - e) * f + e), 1e-12, `R at ${f}×L`);
  }
  // the zero split the bug stored makes R exactly ∝ L — the error it caused
  const p0 = base({ endWindFrac: 0 });
  near(scaleMotor(p0, K(p0, { L_mm: p0.L0_mm / 2 })).R_ohm, p0.R0_ohm / 2,
       1e-12, 'old: R ∝ L');
});

test('delta: the corrected AC factor keeps the AC copper', () => {
  const p = base({ star_delta: 'delta', extraction_rev: 2 });
  const r = scaleMotor(p, K(p));
  const dc = 3 * p.I0_A ** 2 * p.R0_ohm;
  near(r.P_cu_W - dc, dc * (1.38 - 1), 1e-9, 'AC watts at the base cell');
  // the pre-fix factor (a/3 < 1) is floored: no AC copper at all
  const old = base({ loss_grid: { ...p.loss_grid,
    cuAC: p.loss_grid.cuAC.map((row) => row.map((a) => a / 3)) } });
  near(scaleMotor(old, K(old)).P_cu_W, dc, 1e-12, 'old delta: DC only');
});

test('EMF and KV read the fundamental; the voltage limit gets the peak', () => {
  const p = base({ emf_basis: 'fundamental', Vload0_fund_V: 236.0,
                   extraction_rev: 2 });
  const r = scaleMotor(p, K(p));
  near(r.Vemf_peak_V, p.Vemf0_peak_V, 1e-12, 'EMF = stored fundamental');
  near(r.KV_rpm_per_Vline, p.rpm0 / (p.Vemf0_peak_V * Math.sqrt(3)), 1e-12, 'KV');
  // base point: the loaded waveform peak exactly
  near(r.Vphase_peak_V, p.Vload0_peak_V, 1e-12, 'loaded peak at base');
  // off base the drop is fund − fund, then × crest
  const r2 = scaleMotor(p, K(p, { rpm: p.rpm0 * 1.5 }));
  const crest = p.Vload0_peak_V / p.Vload0_fund_V;
  const drop = (p.Vload0_fund_V - p.Vemf0_peak_V) * 1.5;
  near(r2.Vphase_peak_V, (p.Vemf0_peak_V * 1.5 + drop) * crest, 1e-12, '1.5× rpm');
});

test('an older passport (peak − peak) is scaled exactly as before', () => {
  const p = base();                          // no Vload0_fund_V
  const r = scaleMotor(p, K(p, { rpm: p.rpm0 * 0.5 }));
  const drop = (p.Vload0_peak_V - p.Vemf0_peak_V) * 0.5;
  near(r.Vphase_peak_V, p.Vemf0_peak_V * 0.5 + drop, 1e-12, 'legacy law');
});

test('extraction_rev 2: the AC copper comes from the stored watts', () => {
  const p0 = base({ star_delta: 'delta', extraction_rev: 2 });
  const lg = p0.loss_grid;
  const dcw = lg.I_A.map((I) => lg.rpm.map(() => 3 * I * I * p0.R0_ohm));
  const acw = dcw.map((row, r) => row.map((w, c) => w * (lg.cuAC[r][c] - 1)));
  // deliberately inconsistent ratio: the watts must win
  const p = base({ ...p0, loss_grid: { ...lg, Pcu_dc_W: dcw, Pcu_ac_W: acw,
    cuAC: lg.cuAC.map((row) => row.map(() => 1)) } });
  const r = scaleMotor(p, K(p));
  near(r.P_cu_W - 3 * p.I0_A ** 2 * p.R0_ohm, acw[1][1], 1e-9, 'AC watts');
});

test('torque, Kt, Km: measured k_T, else k_flux, else 2-D — one factor', () => {
  const p0 = base();
  const r0 = scaleMotor(p0, K(p0));                     // no 3-D at all
  assert.equal(r0.kt_km_basis, '2-D');
  const p2 = base({ end3d: { k_flux: 0.95 } });
  const r2 = scaleMotor(p2, K(p2));
  near(r2.T_Nm, r0.T_Nm * 0.95, 1e-12, 'torque × k_flux');
  near(r2.Kt_Nm_per_A, r0.Kt_Nm_per_A * 0.95, 1e-12, 'Kt follows the torque');
  near(r2.Km_Nm_sqrtW, r0.Km_Nm_sqrtW * 0.95, 1e-12, 'Km follows the torque');
  assert.equal(r2.kt_km_basis, '3-D flux');
  const p3 = base({ end3d: { k_flux: 0.95, k_T: 0.98 } });
  const r3 = scaleMotor(p3, K(p3));
  near(r3.T_Nm, r0.T_Nm * 0.98, 1e-12, 'torque × k_T');
  near(r3.Kt_Nm_per_A, r0.Kt_Nm_per_A * 0.98, 1e-12, 'Kt × k_T');
  near(r3.Vemf_peak_V, r0.Vemf_peak_V * 0.95, 1e-12, 'EMF keeps k_flux');
  assert.equal(r3.kt_km_basis, '3-D');
});

test('a measured k_T(L) table is read at the tuned length (clamped)', () => {
  const p0 = base();
  const tab = { '77.5': 0.96, '155': 0.98, '310': 0.99 };
  const p = base({ end3d: { k_flux: 0.95, k_flux_vs_L: { '77.5': 0.93, '155': 0.95 },
                            k_T: 0.98, k_T_vs_L: tab } });
  for (const [L, kT] of [[155, 0.98], [77.5, 0.96], [232.5, 0.985], [40, 0.96], [400, 0.99]]) {
    const r = scaleMotor(p, K(p, { L_mm: L }));
    const r0 = scaleMotor(p0, K(p0, { L_mm: L }));
    near(r.T_Nm, r0.T_Nm * kT, 1e-12, `torque × k_T(${L})`);
    assert.equal(r.kt_km_basis, '3-D');
  }
});
