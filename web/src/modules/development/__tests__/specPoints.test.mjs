// node --test: a project page shows only specification points + one cold no-load duty, and no trial steps (owner 2026-11-09).
import test from 'node:test';
import assert from 'node:assert/strict';
import { specPointRefs, filterToSpecPoints, dutyInfoOf, pickNoLoad, visibleSteps } from '../specPoints.ts';
import { developmentComparisonRows } from '../comparisonRows.ts';

const draft = { fields: { key_points: { status: 'filled', value: [
  { row_id: 'a', name: 'Takeoff', torque_Nm: 40, speed_rpm: 3000, duration_s: 60 },
  { row_id: 'b', name: 'Cruise', torque_Nm: 20, speed_rpm: 2500, duration_s: 'continuous' },
  { row_id: 'c', name: 'Climb', torque_Nm: 30, speed_rpm: 2800, duration_s: 300 },
  { row_id: 'd', name: 'Landing', torque_Nm: 10, speed_rpm: 2000, duration_s: 60 },
  { row_id: 'e', name: 'Peak', torque_Nm: 50, speed_rpm: 3200, duration_s: 10 },
] } } };
const duty = (name, rpm, torque, extra = {}) => ({ duty: name, operating_point: { rpm, ...(torque === null ? {} : { torque_nm: torque }) }, ...extra });
const hanLike = [
  duty('takeoff', 3000, 40), duty('cruise_2500', 2500, 20), duty('x_climb', 2800, 30.5), duty('p4', 2000, 10.1), duty('p5', 3200, 50),
  duty('tmp_trial', 4000, 5), duty('noload_7200', 7200, null, { comparison: { magnet_temp_c: 20, coil_temp_c: 20 } }),
];
const names = (rows) => rows.map((r) => r.duty);

test('Han-like: 5 spec duties (name or numbers) plus the no-load; the helper duty is hidden', () => {
  const refs = specPointRefs(draft);
  assert.equal(refs.length, 5);
  const out = filterToSpecPoints(hanLike, dutyInfoOf, refs);
  assert.deepEqual(names(out), ['takeoff', 'cruise_2500', 'x_climb', 'p4', 'p5', 'noload_7200']);
});

test('explicit spec_row_id link wins over a different name and numbers', () => {
  const refs = specPointRefs(draft);
  const out = filterToSpecPoints([duty('zzz', 1, 1, { spec_row_id: 'c' }), duty('other', 9, 9)], dutyInfoOf, refs);
  assert.deepEqual(names(out), ['zzz']);
});

test('no spec, or no matching duty: nothing is filtered', () => {
  assert.equal(filterToSpecPoints(hanLike, dutyInfoOf, specPointRefs({})).length, hanLike.length);
  assert.equal(filterToSpecPoints([duty('a', 1, 1), duty('b', 2, 2)], dutyInfoOf, specPointRefs(draft)).length, 2);
});

test('one no-load only: coldest first, then highest rpm; unknown temperature ranks last', () => {
  const warm = { ...duty('noload_9000', 9000, null, { comparison: { magnet_temp_c: 90, coil_temp_c: 90 } }) };
  const cold = duty('free_run_cold', 5000, null, { comparison: { magnet_temp_c: 20, coil_temp_c: 22 } });
  const cold2 = duty('no_load_6000', 6000, null, { comparison: { magnet_temp_c: 20, coil_temp_c: 22 } });
  const unknown = duty('noload_1', 12000, null);
  assert.equal(pickNoLoad([warm, cold, cold2, unknown].map(dutyInfoOf)), 2);
  assert.equal(pickNoLoad([warm, unknown].map(dutyInfoOf)), 0);          // none cold: still the coldest available, one only
  assert.equal(pickNoLoad([duty('cruise', 1, 1)].map(dutyInfoOf)), -1);
  const out = filterToSpecPoints([duty('takeoff', 3000, 40), warm, cold, cold2, unknown], dutyInfoOf, specPointRefs(draft));
  assert.deepEqual(names(out), ['takeoff', 'no_load_6000']);
});

test('legacy S1 / peak pair and mission-profile rows are spec points too', () => {
  const legacy = { fields: { '1_4': { value: { torque_Nm: 12, speed_rpm: 1000 } }, '1_5': { value: { torque_Nm: 24, speed_rpm: 900, duration_s: 10 } } } };
  assert.equal(specPointRefs(legacy).length, 2);
  const mission = { mission_profile: [{ phase: 'Hover', torque_Nm: 5, speed_rpm: 700, duration_s: 30, row_id: 'm1' }] };
  assert.equal(specPointRefs(mission)[0].name, 'Hover');
});

test('steps strip: trial variants are hidden, the open one is kept', () => {
  const v = [{ die: 'D1', canonical_die: 'D1', config: 'a', status: 'current' }, { die: 'D1', canonical_die: 'D1', config: 'b', status: 'trial' }, { die: 'D2', canonical_die: 'D2', config: 'c', status: 'trial' }];
  assert.deepEqual(visibleSteps(v).map((x) => x.config), ['a']);
  assert.deepEqual(visibleSteps(v, 'd2', 'C').map((x) => x.config), ['a', 'c']);
});

test('Motors table rows are cut the same way', () => {
  const motor = { die: 'D1', config: 'a', results: hanLike.map((r) => ({ ...r, has_result: true })) };
  assert.equal(developmentComparisonRows([motor]).length, 7);
  assert.equal(developmentComparisonRows([motor], specPointRefs(draft)).length, 6);
});
