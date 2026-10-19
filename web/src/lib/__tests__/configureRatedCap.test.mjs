// node --test - the rated speed is bisected on the very predicate behind the red "overheats" line (owner 2026-10-19).
// Imports lib/configureRatedCap.ts + lib/thermalEstimate.ts (Node >= 22.18 / 24; else skipped).
import test from 'node:test';
import assert from 'node:assert/strict';

let C = null; let T = null;
try { C = await import('../configureRatedCap.ts'); T = await import('../thermalEstimate.ts'); } catch { /* old Node */ }
const t = C ? test : test.skip;

t('a fine start is kept as it is', () => {
  assert.equal(C.ratedSpeedCap(12930, () => false), 12930);
});

t('the cap is 1 % inside the border, round, and fine on the predicate itself', () => {
  const border = 9876.5;
  const bad = (r) => r > border;
  const cap = C.ratedSpeedCap(13000, bad);
  assert.equal(cap % 10, 0);
  assert.ok(!bad(cap));
  assert.ok(cap <= border * 0.99 + 1e-9 && cap > border * 0.99 - 10, `cap ${cap}`);
});

t('a predicate that is never fine gives null (the stored point is kept, the red line stays honest)', () => {
  assert.equal(C.ratedSpeedCap(13000, () => true), null);
  assert.equal(C.ratedSpeedCap(0, () => false), null);
});

t('a non-monotone pocket below the border is stepped over', () => {
  const bad = (r) => r > 8000 || (r > 7900 && r < 7925);
  const cap = C.ratedSpeedCap(13000, bad);
  assert.ok(!bad(cap) && cap <= 8000 * 0.99);
});

t('configurePointThermal is configureMotorHeat -> estimateThermal -> the winding / magnet verdict', () => {
  const geom = { statorOD_mm: 40, stackLength_mm: 12, numSlots: 12, slotHeight_mm: 6.1, slotWidth_mm: 2.82,
    insulation_mm: 0.06, coreThickness_mm: 1.9, airGap_mm: 0.25, magnetOD_mm: 23.5 };
  const base = { P_cu_W: 36, P_fe_W: 9, P_mag_W: 3, stator_W: 44, rotor_W: 4 };
  const lim = { winding_C: 200, magnet_C: 150 };
  const cool = { h_Wm2K: 69.57, ambient_C: 25 };
  const pwm = T.configurePointThermal('pwm', true, base, 5, cool, geom, lim);
  const heat = T.configureMotorHeat('pwm', true, base, 5);
  const ref = T.estimateThermal(geom, heat.thermalLosses, cool);
  assert.equal(pwm.thermal.T_winding_C, ref.T_winding_C);
  // the controller's loss is NOT motor heat: 36 + 9 + 3 W + 5 W of PWM extra = 53 W through the housing
  assert.ok(Math.abs(pwm.thermal.P_total_W - 53) < 1e-9);
  assert.equal(pwm.over, ref.T_winding_C > 200 || ref.T_magnet_C > 150);
  const sine = T.configurePointThermal('sine', true, base, 0, cool, geom, lim);
  assert.ok(sine.thermal.T_winding_C < pwm.thermal.T_winding_C);
  assert.equal(T.configurePointThermal('pwm', true, base, null, cool, geom, lim).thermal, null);   // PWM refused: nothing shown
  assert.equal(T.configurePointThermal('sine', true, base, 0, null, geom, lim).over, false);       // no cooling yet
});
