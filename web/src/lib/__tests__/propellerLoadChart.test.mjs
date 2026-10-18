// node --test — lib/propellerLoadChart.ts: the Configure load chart (thrust per rotor on x, one switchable parameter).
import test from 'node:test';
import assert from 'node:assert/strict';

let L = null;
try { L = await import('../propellerLoadChart.ts'); } catch { /* old Node */ }
const t = L ? test : test.skip;
const G0 = 9.80665;

const rows = [
  { rpm: 6000, thrust_N: 2 * G0, torque_prop_Nm: 0.5, shaft_power_W: 300, electrical_power_W: 360, battery_power_W: 400,
    battery_current_A: 9, current_A: 12, T_winding_C: 80, T_magnet_C: 60, ok: true },
  { rpm: 5000, thrust_N: 1 * G0, torque_prop_Nm: 0.3, shaft_power_W: 150, electrical_power_W: 180, battery_power_W: 200,
    battery_current_A: 5, current_A: 7, T_winding_C: 50, T_magnet_C: 40, ok: true },
  { rpm: 7000, thrust_N: 3 * G0, torque_prop_Nm: 0.8, shaft_power_W: 500, electrical_power_W: 620, battery_power_W: 700,
    battery_current_A: 16, current_A: 20, T_winding_C: 230, T_magnet_C: 120, ok: false },
  { rpm: 8000, thrust_N: 4 * G0, torque_prop_Nm: null, shaft_power_W: 800, ok: false },
];

t('x is speed in rpm, sorted; the line above the limit is dashed and joins the continuous part', () => {
  const { ok, over } = L.loadLine(rows, 'torque');
  assert.deepEqual(ok.map((p) => p.x), [5000, 6000]);
  assert.deepEqual(ok.map((p) => p.y), [0.3, 0.5]);
  assert.deepEqual(over.map((p) => p.x), [6000, 7000]);      // starts at the last continuous point; null torque dropped
  assert.deepEqual(L.loadLine(rows, 'thrust').over.map((p) => p.y), [2, 3, 4]);   // thrust in kg
});

t('efficiency and g/W use the battery power when the drive is modelled, else the motor input', () => {
  assert.equal(L.metricValue(rows[1], 'efficiency'), 75);
  assert.equal(L.metricValue(rows[1], 'gPerW'), 5);          // 1000 g / 200 W
  const noDrive = { ...rows[1], battery_power_W: null };
  assert.equal(Math.round(L.metricValue(noDrive, 'efficiency') * 10) / 10, 83.3);
  assert.equal(L.metricValue({ rpm: 1, thrust_N: 1, ok: true }, 'gPerW'), null);
});

t('every metric has a unit and an all-continuous line has no dashed part', () => {
  for (const m of L.LOAD_METRICS) assert.ok(L.METRIC_UNIT[m], m);
  const { ok, over } = L.loadLine(rows.filter((r) => r.ok), 'thrust');
  assert.equal(ok.length, 2);
  assert.equal(over.length, 0);
  assert.deepEqual(L.loadLine(null, 'thrust'), { ok: [], over: [] });
});
