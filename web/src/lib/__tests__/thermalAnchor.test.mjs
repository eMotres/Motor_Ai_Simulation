// node --test - the lumped network anchored to a duty's coupled FEM record (lib/thermalAnchor.ts) and the PWM reading
// that never refuses a loaded point (configuratorDrive.readVariantNear).  Parity with the server:
// tests/test_configure_anchor.py.  Node >= 22.18 / 24; else skipped.
import test from 'node:test';
import assert from 'node:assert/strict';

let A = null; let D = null;
try { A = await import('../thermalAnchor.ts'); D = await import('../configuratorDrive.ts'); } catch { /* old Node */ }
const t = A ? test : test.skip;

const anchor = {
  schema: 'configure-thermal-anchor-1', duty: 'rated', cooling_mode: 'robotics', air_speed_ms: 0, ambient_C: 40,
  point: { rpm: 1000, I_A: 6 }, drive: 'sine',
  record: { T_winding_C: 74, T_magnet_C: 70.5, T_housing_C: 69.5, P_total_W: 20.2, P_cu_W: 16, P_rotor_W: 1.6 },
  G_housing_W_K: 20.2 / 29.5, R_winding_K_W: 4.5 / 16, R_gap_K_W: 1 / 1.6,
};

t('the stored point IS the record, whatever the page\'s own losses there', () => {
  const page = { P_cu_W: 14, P_rotor_W: 0.5, P_total_W: 18 };
  const k = A.anchorLossRatios(anchor, page);
  const r = A.anchoredTemperatures(anchor, page.P_cu_W, page.P_rotor_W, page.P_total_W, 7, 7, 40, k);
  assert.ok(Math.abs(r.winding - 74) < 1e-9 && Math.abs(r.magnet - 70.5) < 1e-9 && Math.abs(r.housing - 69.5) < 1e-9);
  const cold = A.anchoredTemperatures(anchor, page.P_cu_W, page.P_rotor_W, page.P_total_W, 7, 7, 25, k);
  assert.ok(Math.abs(cold.winding - 59) < 1e-9);                       // 15 K cooler air, the same rises
  const more = A.anchoredTemperatures(anchor, 28, 0.9, 32, 7, 7, 40, k);
  assert.ok(more.winding > r.winding && more.housing > r.housing);
  const pwm = A.anchoredTemperatures(anchor, page.P_cu_W, page.P_rotor_W, page.P_total_W, 7, 7, 40, k, 2);
  assert.ok(pwm.winding > r.winding);                                   // the PWM extra heats the winding
});

const variant = {
  id: 'si_48k', device: 'X', carrier_hz: 48000,
  points: Object.fromEntries([500, 1000, 1500].flatMap((rpm) => [6.5, 13, 26].map((I) => [`${rpm}rpm_${I}A`, {
    rpm, I_A: I, motor_pwm_loss_W: 0.4 + rpm / 10000, board_copper_W: 0.01 * I * I,
    inverter_loss_W: { cond: 0.02 * I * I, sw: 0.05 * I, dead: 0.01 * I }, tj_C: 40 + I,
  }]))),
};

t('a PWM point outside the computed ones is read at the nearest one, never refused', () => {
  const old = D.readVariant(variant, 1440, 4.9);
  assert.equal(old.ok, false);                                          // below the computed currents
  const r = D.readVariantNear(variant, 1440, 4.9);
  assert.equal(r.ok, true);
  assert.deepEqual(r.near, { rpm: 1440, I: 6.5 });
  const edge = D.readVariant(variant, 1440, 6.5);
  const s = 4.9 / 6.5;
  assert.ok(Math.abs(r.values.motor_pwm_loss_W - edge.values.motor_pwm_loss_W) < 1e-12);   // the PWM delta kept
  assert.ok(Math.abs(r.values.inv_cond_W - edge.values.inv_cond_W * s * s) < 1e-12);       // I^2
  assert.ok(Math.abs(r.values.inv_sw_W - edge.values.inv_sw_W * s) < 1e-12);               // I
  const inside = D.readVariantNear(variant, 1000, 13);
  assert.equal(inside.ok, true); assert.equal(inside.near, undefined);
  assert.equal(D.readVariantNear({ id: 'x', device: 'X', carrier_hz: 1, points: {} }, 1000, 13).ok, false);
});
