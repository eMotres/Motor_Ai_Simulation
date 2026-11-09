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

// ── regression: seven duties, all spec rows (generic names; numbers of a 30 kW class machine) ─────────────────────────────────────
const sevenSpec = { fields: { key_points: { status: 'filled', value: [
  { row_id: 'r1', name: 'S1 motor', torque_Nm: 47.7, speed_rpm: 6000, duration_s: 'continuous' },
  { row_id: 'r2', name: 'S1 generator', torque_Nm: 47.7, speed_rpm: 6000, duration_s: 'continuous' },
  { row_id: 'r3', name: 'Motor peak 60 s', torque_Nm: 63.7, speed_rpm: 6000, duration_s: 60 },
  { row_id: 'r4', name: 'Generator peak 40 kW', torque_Nm: 63.7, speed_rpm: 6000, duration_s: 10 },
  { row_id: 'r5', name: 'Start 25 Nm', torque_Nm: 25, speed_rpm: 1000, duration_s: 10 },
  { row_id: 'r6', name: 'Start 6 kW', power_W: 6000, speed_rpm: 1000, duration_s: 10 },
] } } };
const solved = (name, rpm, mode, req, solvedTorque, extra = {}) => ({
  duty: name, operating_point: { rpm, mode, ...req }, comparison: solvedTorque === null ? {} : { torque_nm: solvedTorque }, ...extra,
});
const sevenDuties = [
  solved('rated_47.7Nm_6000', 6000, 'motor', { torque_nm: 47.7 }, 47.7),
  solved('gen_30kW_6000', 6000, 'generator', { power_kw: 30 }, -49.0),
  solved('peak_motor_40kW_6000', 6000, 'motor', {}, 65.3),
  solved('peak_gen_40kW_6000', 6000, 'generator', {}, -66.09),          // +3.75 % solved, negative torque: still the generator peak
  solved('start_25Nm_1000', 1000, 'motor', { torque_nm: 25 }, 25.2),
  solved('start_57Nm_1000', 1000, 'motor', { torque_nm: 57.3 }, 57.3),   // 6 kW at 1000 rpm = 57.3 N.m
  solved('noload_7200', 7200, 'motor', {}, null, { sources: { em: { magnet_temp_c: 20, coil_temp_c: 20 } } }),
  solved('noload_9000_hot', 9000, 'motor', {}, null, { sources: { em: { magnet_temp_c: 90, coil_temp_c: 120 } } }),
  solved('helper_mid', 4000, 'motor', { torque_nm: 5 }, 5),
];
test('regression: all seven duties of the example are kept, the helpers are not', () => {
  const refs = specPointRefs(sevenSpec);
  assert.equal(refs.length, 6);
  assert.equal(refs[1].mode, 'generator');
  const out = filterToSpecPoints(sevenDuties, dutyInfoOf, refs);
  assert.deepEqual(names(out), sevenDuties.slice(0, 7).map((d) => d.duty));
});
test('a motor peak duty cannot serve the generator peak row (like with like)', () => {
  const refs = specPointRefs(sevenSpec).filter((r) => r.rowId === 'r4');
  assert.equal(filterToSpecPoints([solved('peak_motor_40kW_6000', 6000, 'motor', {}, 65.3), solved('other', 1, 'motor', {}, 1)], dutyInfoOf, refs).length, 2); // no match: unfiltered
  const both = filterToSpecPoints([solved('peak_motor_40kW_6000', 6000, 'motor', {}, 65.3), solved('peak_gen', 6000, 'generator', {}, 65.3), solved('other', 1, 'motor', {}, 1)], dutyInfoOf, refs);
  assert.deepEqual(names(both), ['peak_gen']);
});
test('the cold no-load duty is read from the EM run temperature', () => {
  assert.equal(dutyInfoOf(sevenDuties[6]).tempC, 20);
  assert.equal(dutyInfoOf(sevenDuties[7]).tempC, 120);
});

// ── the two traps found on real data ────────────────────────────────────────────────────────────────────────────────────────────
test('trap 1: an S1 row naming both motor and generator is served by a motor duty AND a gen duty', () => {
  const spec = { fields: { key_points: { status: 'filled', value: [
    { row_id: 's1', name: 'S1 30 kW, motor and generator (continuous)', torque_Nm: 47.7, speed_rpm: 6000, duration_s: 'continuous' },
  ] } } };
  const refs = specPointRefs(spec);
  assert.deepEqual(refs.map((r) => r.mode), ['motor', 'generator']);
  const out = filterToSpecPoints([solved('rated_47.7Nm_6000', 6000, 'motor', { torque_nm: 47.7 }, 47.7), solved('gen_30kW_6000', 6000, 'generator', { power_kw: 30 }, -47.9), solved('other', 3000, 'motor', {}, 1)], dutyInfoOf, refs);
  assert.deepEqual(names(out), ['rated_47.7Nm_6000', 'gen_30kW_6000']);
});
test('trap 2: a stored op.torque_nm that is the SOLVED value (+3.75 %) still matches its row', () => {
  const refs = specPointRefs(sevenSpec).filter((r) => r.rowId === 'r4');
  const out = filterToSpecPoints([solved('peak_gen_40kW_6000', 6000, 'generator', { torque_nm: 66.09 }, null), solved('other', 1, 'motor', {}, 1)], dutyInfoOf, refs);
  assert.deepEqual(names(out), ['peak_gen_40kW_6000']);
});
test('regression with both traps in the full example: all seven kept', () => {
  const spec = { fields: { key_points: { status: 'filled', value: [
    { row_id: 'r1', name: 'S1 30 kW, motor and generator', torque_Nm: 47.7, speed_rpm: 6000, duration_s: 'continuous' },
    ...sevenSpec.fields.key_points.value.slice(2),
  ] } } };
  const duties = [
    solved('rated_47.7Nm_6000', 6000, 'motor', { torque_nm: 47.7 }, 47.7), solved('gen_30kW_6000', 6000, 'generator', { power_kw: 30 }, -49.0),
    solved('peak_motor_40kW_6000', 6000, 'motor', { torque_nm: 65.3 }, 65.3), solved('peak_gen_40kW_6000', 6000, 'generator', { torque_nm: 66.09 }, null),
    solved('start_25Nm_1000', 1000, 'motor', { torque_nm: 25.1 }, null), solved('start_57Nm_1000', 1000, 'motor', { torque_nm: 57.3 }, null),
    solved('noload_7200', 7200, 'motor', {}, null, { electromagnetic: { coil_temp_c: 22 } }), solved('helper', 4000, 'motor', { torque_nm: 5 }, 5),
  ];
  assert.deepEqual(names(filterToSpecPoints(duties, dutyInfoOf, specPointRefs(spec))), duties.slice(0, 7).map((d) => d.duty));
});
