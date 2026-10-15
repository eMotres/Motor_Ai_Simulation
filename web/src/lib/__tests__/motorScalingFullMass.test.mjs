// node --test - THE MOTOR MASS IS THE FULL ASSEMBLED MASS (owner 2026-10-14): Configure adds the parts that do not scale
// with the stack to the scaled active mass; without a CAD model (`mass_partial`) it shows the active mass and says so.
import test from 'node:test';
import assert from 'node:assert/strict';

let M = null;
try { M = await import('../motorScaling.ts'); } catch { /* old Node */ }
const t = M ? test : test.skip;

const P = {
  L0_mm: 12, wireH0_mm: 0.6, I0_A: 40, rpm0: 14400, nP0: 1, N0: 7, T0_Nm: 0.5, Vemf0_peak_V: 10, R0_ohm: 0.01,
  endWindFrac: 0.3, Pfe0_W: 10, Pmag0_W: 1, mass0_kg: 0.09,
};
const K = (L) => ({ N: 7, L_mm: L, wireH_mm: 0.6, nP: 1, I_A: 40, rpm: 14400 });

t('without a CAD model the mass is the active mass and is flagged partial', () => {
  for (const p of [P, { ...P, mass_partial: true, mass_fixed_kg: 0.02 }, { ...P, mass_partial: false, mass_fixed_kg: 0 }]) {
    const r = M.scaleMotor(p, K(12), 14);
    assert.equal(r.mass_partial, true);
    assert.ok(Math.abs(r.mass_kg - 0.09) < 1e-12 && r.mass_kg === r.mass_active_kg);
  }
});

t('with a CAD model the fixed parts are added and do NOT scale with the stack; every per-kg figure follows', () => {
  const p = { ...P, mass_partial: false, mass_fixed_kg: 0.024 };
  const a = M.scaleMotor(p, K(12), 14);
  const b = M.scaleMotor(p, K(24), 14);
  assert.equal(a.mass_partial, false);
  assert.ok(Math.abs(a.mass_kg - 0.114) < 1e-12 && Math.abs(a.mass_active_kg - 0.09) < 1e-12);
  assert.ok(Math.abs(b.mass_active_kg - 0.18) < 1e-12 && Math.abs(b.mass_kg - 0.204) < 1e-12);      // 2 x active + the same fixed
  assert.ok(Math.abs(a.torque_per_mass - a.T_Nm / 0.114) < 1e-9);
  assert.ok(Math.abs(a.power_per_mass_W_kg - a.P_mech_W / 0.114) < 1e-6);
  // the electromagnetic numbers do not see the fixed mass at all
  const q = M.scaleMotor(P, K(12), 14);
  assert.equal(a.T_Nm, q.T_Nm); assert.equal(a.P_loss_W, q.P_loss_W); assert.equal(a.P_mech_W, q.P_mech_W);
});

t('a frameless kit is LIGHTER than the active mass (no shaft tube): a negative fixed part is a real CAD mass, not "no model"', () => {
  const p = { ...P, mass_partial: false, mass_fixed_kg: -0.0026 };
  const a = M.scaleMotor(p, K(12), 14);
  assert.equal(a.mass_partial, false);
  assert.ok(Math.abs(a.mass_kg - (0.09 - 0.0026)) < 1e-12 && Math.abs(a.mass_active_kg - 0.09) < 1e-12);
  for (const bad of [null, undefined, NaN, 0]) {                                 // still partial without a usable CAD mass
    assert.equal(M.scaleMotor({ ...P, mass_partial: false, mass_fixed_kg: bad }, K(12), 14).mass_partial, true);
  }
});

t('copper mass follows wire rows, wire height, stack and fixed end turns; full mass keeps CAD rest', () => {
  const p = { ...P, mass_partial: false, mass_fixed_kg: 0.024, mass_cu0_kg: 0.02 };
  const base = M.scaleMotor(p, K(12), 14);
  const longer = M.scaleMotor(p, { ...K(12), L_mm: 24 }, 14);
  const thinner = M.scaleMotor(p, { ...K(12), wireH_mm: 0.3 }, 14);
  const moreRows = M.scaleMotor(p, { ...K(12), N: 8 }, 14);
  assert.ok(Math.abs(base.mass_kg - 0.114) < 1e-12);
  assert.ok(Math.abs(longer.mass_active_kg - 0.174) < 1e-12);
  assert.ok(Math.abs(longer.mass_kg - 0.198) < 1e-12);
  assert.ok(Math.abs(thinner.mass_kg - 0.104) < 1e-12);
  assert.ok(Math.abs(moreRows.mass_kg - (0.07 + 0.02 * 8 / 7 + 0.024)) < 1e-12);
  assert.ok(Math.abs(thinner.power_per_mass_W_kg - thinner.P_mech_W / thinner.mass_kg) < 1e-9);
});

t('a missing/null copper mass does not get misread as a measured zero', () => {
  for (const missing of [undefined, null]) {
    const r = M.scaleMotor({ ...P, mass_partial: false, mass_fixed_kg: 0.024, mass_cu0_kg: missing }, K(24), 14);
    assert.ok(Math.abs(r.mass_active_kg - 0.18) < 1e-12);
  }
});

t('a legacy passport flags the winding mass basis only after wire geometry changes', () => {
  assert.equal(M.scaleMotor(P, K(24), 14).mass_winding_known, true);
  assert.equal(M.scaleMotor(P, { ...K(12), N: 8 }, 14).mass_winding_known, false);
  assert.equal(M.scaleMotor(P, { ...K(12), wireH_mm: 0.5 }, 14).mass_winding_known, false);
  assert.equal(M.scaleMotor({ ...P, mass_cu0_kg: 0.02 }, { ...K(12), N: 8 }, 14).mass_winding_known, true);
});
