import test from 'node:test';
import assert from 'node:assert/strict';
import { buildComparisonModel, bestValueColor, comparisonTableColumns, shouldSuppressMixedBasisColor } from '../comparisonModel.ts';

// Exact Stage 1 algorithms from the immutable ComparePanel baseline. These are
// executed as an oracle; the assertions below compare production model output,
// rather than checking source text or a second set of expected constants.
const baselineValEq = (a, b) => typeof a === 'number' && typeof b === 'number'
  ? Math.abs(a - b) < 1e-6 : String(a) === String(b);
const baselineDiffKeys = (rows, keys) => keys.filter((key) => {
  const values = rows.map((row) => row.params?.[key]);
  return values.some((value) => !baselineValEq(value, values[0]));
});
const baselineExtent = (values) => {
  const finite = values.map(Number).filter(Number.isFinite);
  return finite.length ? { min: Math.min(...finite), max: Math.max(...finite) } : null;
};
const baselineColor = (value, direction, extent) => {
  if (!Number.isFinite(value) || !extent || extent.min === extent.max || !direction) return null;
  const best = direction === 'hi' ? extent.max : extent.min;
  const worst = direction === 'hi' ? extent.min : extent.max;
  if (Math.abs(value - best) < 1e-9) return 'best';
  if (Math.abs(value - worst) < 1e-9) return 'worst';
  return null;
};

test('shared model preserves Compare differing inputs and result extents', () => {
  const rows = [
    { id: 'a', params: { rpm: 900, mesh: { size: 1 } }, result: { torque: 4, loss: 20 } },
    { id: 'b', params: { rpm: 900, mesh: { size: 2 } }, result: { torque: 5, loss: 10 } },
  ];
  const columns = [
    { key: 'rpm', kind: 'input', value: r => r.params.rpm },
    { key: 'mesh', kind: 'input', value: r => r.params.mesh },
    { key: 'torque', kind: 'result', better: 'hi', value: r => r.result.torque },
    { key: 'loss', kind: 'result', better: 'lo', value: r => r.result.loss },
  ];
  const model = buildComparisonModel(rows, columns, (a, b) => JSON.stringify(a) === JSON.stringify(b));
  assert.deepEqual(model.differingInputKeys, ['mesh']);
  assert.deepEqual(model.extents.torque, { min: 4, max: 5 });
  assert.equal(bestValueColor(5, 'hi', model.extents.torque), 'best');
  assert.equal(bestValueColor(20, 'lo', model.extents.loss), 'worst');
});

test('missing values do not enter extents and ties do not get best/worst colors', () => {
  const rows = [{ x: null }, { x: 0 }, { x: undefined }];
  const model = buildComparisonModel(rows, [{ key: 'x', kind: 'result', better: 'hi', value: r => r.x }]);
  assert.deepEqual(model.extents.x, { min: 0, max: 0 });
  assert.equal(bestValueColor(0, 'hi', model.extents.x), null);
  assert.equal(bestValueColor(null, 'hi', model.extents.x), null);
});

test('Compare direction, dynamic columns, full-mass ranking, and legacy tolerance stay aligned', () => {
  const rows = [
    { name: 'full', params: { cooling: 'air', therm_flow_lpm: 0 }, result: { torque: 5, power: 2, efficiency: 0.9, losses: 10, mass: 2, torqueDensity: 2.5, ripple: 3, voltage: 40, currentDensity: 5, dynamicTemp: 60 } },
    { name: 'partial', params: { cooling: 'liquid', therm_flow_lpm: 0 }, result: { torque: 5 + 5e-10, power: 3, efficiency: 0.91, losses: 9, mass: 1, torqueDensity: 10, ripple: 2, voltage: 30, currentDensity: 4, dynamicTemp: 50 } },
  ];
  const higher = ['torque', 'power', 'efficiency'];
  const lower = ['losses', 'mass', 'ripple', 'voltage', 'currentDensity', 'dynamicTemp'];
  const columns = [
    { key: 'cooling', kind: 'input', value: r => r.params.cooling },
    { key: 'therm_flow_lpm', kind: 'input', value: r => r.params.therm_flow_lpm },
    ...higher.map(key => ({ key, kind: 'result', better: 'hi', value: r => r.result[key] })),
    ...lower.map(key => ({ key, kind: 'result', better: 'lo', value: r => r.result[key] })),
    { key: 'torqueDensity', kind: 'result', better: 'hi', value: r => r.result.torqueDensity,
      rankValues: rs => rs.filter(x => x.name === 'full').map(x => x.result.torqueDensity),
      noColor: r => r.name === 'partial' },
  ];
  const model = buildComparisonModel(rows, columns, baselineValEq);
  assert.deepEqual(model.differingInputKeys, ['cooling']);
  assert.deepEqual(model.extents.torque, { min: 5, max: 5 + 5e-10 });
  assert.equal(bestValueColor(rows[0].result.torque, 'hi', model.extents.torque, 1e-9), 'best'); // baseline best-first 1e-9 tolerance
  for (const key of higher) assert.equal(bestValueColor(model.extents[key].max, 'hi', model.extents[key]), 'best');
  for (const key of lower) assert.equal(bestValueColor(model.extents[key].min, 'lo', model.extents[key]), 'best');
  assert.deepEqual(model.extents.torqueDensity, { min: 2.5, max: 2.5 }); // mixed mass bases rank only full-mass rows
  assert.equal(columns.find(c => c.key === 'torqueDensity').noColor(rows[1]), true);
  assert.ok(model.extents.dynamicTemp); // dynamically added result columns remain available
});

test('production model matches Stage 1 algorithms for namespaced EM, thermal, mechanical, and input columns', () => {
  const rows = [
    { params: { rpm: 1000, gamma_deg: 10, mesh: 'fine' }, result: { torque: 5, loss: 20, thermal: { winding_max: 88 }, mechanical: { rpm: 12000 } } },
    { params: { rpm: 1000 + 5e-7, gamma_deg: 20, mesh: 'fine' }, result: { torque: 6, loss: 15, thermal: { winding_max: 75 }, mechanical: { rpm: 13000 } } },
    { params: { rpm: 1000, gamma_deg: 20, mesh: 'coarse' }, result: { torque: undefined, loss: 25, thermal: {}, mechanical: { rpm: 11000 } } },
  ];
  const resultDefs = [
    { key: 'r:em:torque', better: 'hi', read: r => r.result.torque },
    { key: 'r:em:loss', better: 'lo', read: r => r.result.loss },
    { key: 'r:thermal:winding_max', better: 'lo', read: r => r.result.thermal.winding_max },
    { key: 'r:mechanical:rpm', read: r => r.result.mechanical.rpm },
  ];
  const inputKeys = ['rpm', 'gamma_deg', 'mesh'];
  const columns = [
    ...resultDefs.map((def) => ({ key: def.key, kind: 'result', better: def.better, value: def.read })),
    ...inputKeys.map((key) => ({ key: `p:${key}`, kind: 'input', value: row => row.params[key] })),
  ];
  const model = buildComparisonModel(rows, columns, baselineValEq);
  assert.deepEqual(model.differingInputKeys, baselineDiffKeys(rows, inputKeys).map(key => `p:${key}`));
  for (const def of resultDefs) {
    const expected = baselineExtent(rows.map(def.read));
    assert.deepEqual(model.extents[def.key], expected, def.key);
    for (const row of rows) assert.equal(bestValueColor(def.read(row), def.better, expected, 1e-9), baselineColor(def.read(row), def.better, expected), `${def.key} color`);
  }
  assert.ok(model.extents['r:mechanical:rpm']);
  assert.ok(model.extents['r:thermal:winding_max']);
});

test('picker candidates keep results and Development can force equal or entirely missing identity inputs visible', () => {
  const rows = [{ die: 'Synthetic', current: undefined, torque: 1 }, { die: 'Synthetic', current: undefined, torque: 2 }];
  const columns = [
    { key: 'die', kind: 'input', value: row => row.die },
    { key: 'current', kind: 'input', value: row => row.current },
    { key: 'torque', kind: 'result', better: 'hi', value: row => row.torque },
  ];
  const model = buildComparisonModel(rows, columns);
  assert.deepEqual(comparisonTableColumns(columns, model).map(c => c.key), ['torque']);
  assert.deepEqual(comparisonTableColumns(columns, model, true).map(c => c.key), ['die', 'current', 'torque']);
});

test('Stage 1 mixed-mass warning uses the whole library even when the selected extent is active-only', () => {
  const selectedValue = 8;
  assert.equal(shouldSuppressMixedBasisColor(selectedValue, true, true), true, 'an unselected full-mass library row suppresses colour');
  assert.equal(shouldSuppressMixedBasisColor(selectedValue, true, false), false);
  assert.equal(shouldSuppressMixedBasisColor(undefined, true, true), false);
});
