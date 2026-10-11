// node --test — the Configure line for the continuous (S1) limit with a propeller (owner 2026-10-11).
// The answer is the backend's (/api/propellers/{id}/s1); this pins only what the line says and when
// it says nothing (no answer, or an answer for another propeller / configuration).
import test from 'node:test';
import assert from 'node:assert/strict';

let S = null;
try { S = await import('../configuratorPropellerS1.ts'); } catch { /* old Node */ }
const t = S ? test : test.skip;

const LIMIT = {
  rpm: 8479, thrust_N: 16.95, shaft_power_W: 265, electrical_power_W: 292, battery_power_W: 302,
  current_A: 21, T_winding_C: 156.2, T_magnet_C: 150, T_housing_C: 144, air_speed_ms: 9.1,
  binding: 'magnet', extrapolated: false, power_estimated: false, below_loss_grid: false,
};
const R = {
  propeller_id: 'tmotor_fpv_10x5', die: 'CIANO14 40 new', config: 'L12', ambient_c: 25,
  limit: LIMIT, no_continuous_point: false, first_failure: 'magnet', monotone: true,
  limits: { winding_C: 180, magnet_C: 150, winding_basis: 'class H', magnet_basis: 'card' },
};

t('a limit reads speed, thrust in kgf, shaft power and the binding key', () => {
  const l = S.s1Line(R, 'tmotor_fpv_10x5', 'L12');
  assert.equal(l.kind, 'limit');
  assert.equal(l.rpm, 8479);
  assert.ok(Math.abs(l.thrust_kgf - 16.95 / 9.80665) < 1e-12);
  assert.equal(l.shaft_W, 265);
  assert.equal(l.bindingKey, 'configurePropeller.s1BindMagnet');
  assert.deepEqual(l.flagKeys, []);
});

t('no line for another propeller, another configuration, or no answer', () => {
  assert.equal(S.s1Line(R, 'tmotor_p12x4', 'L12'), null);
  assert.equal(S.s1Line(R, 'tmotor_fpv_10x5', 'L20'), null);
  assert.equal(S.s1Line(null, 'tmotor_fpv_10x5', 'L12'), null);
  assert.equal(S.s1Line(R, null, 'L12'), null);
});

t('the catalogue and grid flags become tooltip sentences', () => {
  const r = { ...R, monotone: false, limit: { ...LIMIT, extrapolated: true, power_estimated: true, below_loss_grid: true } };
  assert.deepEqual(S.s1Line(r, 'tmotor_fpv_10x5', 'L12').flagKeys, [
    'configurePropeller.s1FlagExtrapolated', 'configurePropeller.s1FlagEstimated',
    'configurePropeller.s1FlagBelowGrid', 'configurePropeller.s1FlagBands']);
});

t('field weakening and a calibrated thermal model are stated', () => {
  const r = { ...R, model: { thermal_calibration: { housing_film_factor: 0.17 } },
    limit: { ...LIMIT, fw_floor: true } };
  assert.deepEqual(S.s1Line(r, 'tmotor_fpv_10x5', 'L12').flagKeys, [
    'configurePropeller.s1FlagFwFloor', 'configurePropeller.s1FlagCalibrated']);
  assert.deepEqual(S.s1Line({ ...R, model: { thermal_calibration: null } }, 'tmotor_fpv_10x5', 'L12').flagKeys, []);
});

t('no continuous point names what failed first', () => {
  const l = S.s1Line({ ...R, limit: null, no_continuous_point: true, first_failure: 'winding' }, 'tmotor_fpv_10x5', 'L12');
  assert.deepEqual(l, { kind: 'none', bindingKey: 'configurePropeller.s1BindWinding' });
  for (const [b, k] of [['current', 's1BindCurrent'], ['voltage', 's1BindVoltage'], ['speed', 's1BindVoltage'],
    ['speed_range', 's1BindGrid'], ['runaway', 's1BindRunaway'], [null, 's1BindUnknown']]) {
    assert.equal(S.s1BindingKey(b), `configurePropeller.${k}`);
  }
});
