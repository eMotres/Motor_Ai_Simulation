import test from 'node:test';
import assert from 'node:assert/strict';

let C;
try { C = await import('../propellersCatalog.ts'); } catch { /* old Node */ }
const t = C ? test : test.skip;

const resp = { propellers: [
  { id: 'b', vendor: 'V', model: 'B 12', diameter_in: 12, pitch_in: 6, blades: 2, data_quality: 'torque_measured', selectable: true, rpm_range_tested: [3000, 9000] },
  { id: 'a', vendor: 'V', model: 'A 10', diameter_in: 10, pitch_in: 5, blades: null, data_quality: 'geometry_only', selectable: false, rpm_range_tested: null },
  { id: 'c', vendor: 'W', model: 'C 10', diameter_in: 10, data_quality: 'thrust_measured_power_estimated', selectable: true, rpm_range_tested: [2000, 7000] },
  { vendor: 'no id' }, null,
] };

t('rows are sorted by diameter then name; malformed entries are dropped', () => {
  const rows = C.catalogRows(resp);
  assert.deepEqual(rows.map((r) => r.id), ['a', 'c', 'b']);
  assert.equal(C.catalogRows({ propellers: [{ id: 'only-id' }] })[0].name, 'only-id');   // no vendor/model: the id
  assert.equal(rows[0].name, 'V A 10');
});

t('data quality is a three-way class and an unknown value reads as the weakest', () => {
  assert.equal(C.qualityOf({ data_quality: 'torque_measured' }), 'measured');
  assert.equal(C.qualityOf({ data_quality: 'thrust_measured_power_estimated' }), 'powerEstimated');
  assert.equal(C.qualityOf({ data_quality: 'geometry_only' }), 'geometryOnly');
  assert.equal(C.qualityOf({ data_quality: 'something_new' }), 'geometryOnly');
  assert.equal(C.qualityOf({}), 'geometryOnly');
});

t('one rpm value is kept (the top of the range), missing numbers stay null, never 0', () => {
  const rows = Object.fromEntries(C.catalogRows(resp).map((r) => [r.id, r]));
  assert.equal(rows.b.maxRpmTested, 9000);
  assert.equal(rows.a.maxRpmTested, null);
  assert.equal(rows.a.blades, null);
  assert.equal(rows.c.pitchIn, null);
});

t('a bad response gives no rows', () => {
  assert.deepEqual(C.catalogRows(null), []);
  assert.deepEqual(C.catalogRows({ propellers: 'x' }), []);
});
