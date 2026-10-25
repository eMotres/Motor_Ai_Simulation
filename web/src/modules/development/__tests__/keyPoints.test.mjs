// node --test: key operating points - validation, derivation (shared cases with the backend), legacy read, completeness.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import * as K from '../keyPoints.ts';
import * as W from '../wizardState.ts';
import { buildSpecificationCards } from '../specificationLayout.ts';

const row = (name, torque_Nm, speed_rpm, duration_s, voltage_V = null) => ({ row_id: `id-${name}`, name, torque_Nm, speed_rpm, duration_s, voltage_V });
const names = { continuous: 'Continuous', peak: 'Peak' };
const schema = JSON.parse(readFileSync(new URL('../../../../../src/motor_ai_sim/development/schema.json', import.meta.url), 'utf8'));
const cases = JSON.parse(readFileSync(new URL('../../../../../tests/fixtures/development_key_points_cases.json', import.meta.url), 'utf8'));

test('derivation agrees with the shared cases the backend test also runs', () => {
  for (const c of cases) {
    const { continuous, peak } = K.deriveContinuousPeak(c.rows.map((r) => row(...r)));
    assert.equal(continuous?.name ?? null, c.continuous, c.id);
    assert.equal(peak?.name ?? null, c.peak, c.id);
  }
});

test('a point is valid with a name, torque, speed and a duration that is continuous or > 0 s', () => {
  assert.deepEqual(K.keyPointErrors(row('x', 5, 100, 10)), []);
  assert.deepEqual(K.keyPointErrors(row('x', 5, 100, 'continuous')), []);
  assert.deepEqual(K.keyPointErrors(row('', null, null, null)), ['name', 'torque_Nm', 'speed_rpm', 'duration_s']);
  assert.deepEqual(K.keyPointErrors(row('x', 5, 100, 0)), ['duration_s']);
  assert.deepEqual(K.keyPointErrors(row('x', 5, -1, 3)), ['speed_rpm']);
  // the voltage is optional
  assert.deepEqual(K.keyPointErrors(row('x', 5, 100, 3, null)), []);
});

test('a project with only S1 and peak reads as two points; nothing is stored by reading', () => {
  const draft = { fields: {
    '1_4': { status: 'filled', value: { torque_Nm: 12.5, speed_rpm: 3000, shaft_power_W: 3927 } },
    '1_5': { status: 'filled', value: { torque_Nm: { minimum: 20, maximum: 25 }, speed_rpm: 2800, duration_s: { minimum: 10, maximum: 30 }, period_s: 600 } },
  } };
  const rows = K.effectiveKeyPoints(draft, names, ['a']);
  assert.deepEqual(rows.map((r) => [r.name, r.torque_Nm, r.speed_rpm, r.duration_s]), [['Continuous', 12.5, 3000, 'continuous'], ['Peak', 25, 2800, 30]]);
  assert.equal(K.keyPointsStored(draft), false);
  assert.equal(draft.fields.key_points, undefined);
  assert.equal(K.keyPointsSlotState(draft), 'answered');
});

test('a new project shows the four default rows; they are not stored until edited', () => {
  const rows = K.effectiveKeyPoints({ fields: {} }, names, ['Takeoff', 'Cruise', 'Landing', 'Peak']);
  assert.deepEqual(rows.map((r) => r.name), ['Takeoff', 'Cruise', 'Landing', 'Peak']);
  assert.equal(K.keyPointsSlotState({ fields: {} }), 'missing');
  // a bare placeholder answer (nothing typed) keeps the defaults / legacy points in charge
  assert.equal(K.keyPointsStored({ fields: { key_points: { status: 'missing' } } }), false);
});

test('saving the migrated rows loses nothing: S1 and peak come back with their extras', () => {
  const draft = { fields: {
    '1_4': { status: 'filled', value: { torque_Nm: 12.5, speed_rpm: 3000 } },
    '1_5': { status: 'filled', value: { torque_Nm: 25, speed_rpm: 2800, duration_s: 30, frequency: 'every takeoff', period_s: 600 } },
  } };
  const saved = K.withKeyPoints(draft, K.effectiveKeyPoints(draft, names, []));
  assert.equal(saved.fields.key_points.status, 'filled');
  assert.equal(saved.fields['1_4'].value.torque_Nm, 12.5);
  assert.ok(Math.abs(saved.fields['1_4'].value.shaft_power_W - 12.5 * 2 * Math.PI * 3000 / 60) < 1e-9);
  const peak = saved.fields['1_5'].value;
  assert.deepEqual([peak.torque_Nm, peak.speed_rpm, peak.duration_s, peak.frequency, peak.period_s], [25, 2800, 30, 'every takeoff', 600]);
});

test('editing the list derives S1 and peak; an incomplete list never wipes the old values', () => {
  const four = [row('Takeoff', 80, 2400, 60, 700), row('Cruise', 40, 2800, 'continuous'), row('Landing', 55, 2000, 30), row('Peak', 110, 2200, 5)];
  const draft = K.withKeyPoints({ fields: {} }, four);
  assert.equal(draft.fields.key_points.status, 'filled');
  assert.equal(draft.fields['1_4'].value.torque_Nm, 40);
  assert.equal(draft.fields['1_5'].value.torque_Nm, 110);
  const half = K.withKeyPoints(draft, [row('Cruise', null, 2800, 'continuous')]);
  assert.equal(half.fields.key_points.status, 'missing');
  assert.equal(half.fields['1_4'].value.torque_Nm, 40);
  const noPeak = K.withKeyPoints({ fields: { '1_5': { status: 'filled', value: { torque_Nm: 9, period_s: 60 } } } }, [row('Cruise', 40, 2800, 'continuous')]);
  assert.deepEqual(noPeak.fields['1_5'], { value: { period_s: 60 }, status: 'missing' });
});

test('the key points slot counts toward completeness and is judged loudly', () => {
  const key = buildSpecificationCards(schema).find((card) => card.id === 'key');
  assert.ok(key.fields.some((f) => f.id === 'key_points' && f.required));
  assert.ok(!key.fields.some((f) => f.id === 'key_points' && !f.required));
  const base = { fields: { '1_1': { status: 'filled', value: 'Drone' }, '2_1': { status: 'not_known_yet' }, '3_1': { status: 'not_known_yet' }, '3_2': { status: 'not_known_yet' } } };
  const withPoints = (points, status = 'filled') => ({ ...base, fields: { ...base.fields, key_points: { status, value: points } } });
  const ids = (d) => W.requiredFieldIds([key], d);
  assert.ok(ids(base).includes('key_points'));
  assert.ok(!ids(withPoints([row('Cruise', 40, 2800, 'continuous')])).includes('key_points'));
  assert.ok(ids(withPoints([row('Cruise', 40, 2800, 0)])).includes('key_points'));
  assert.ok(ids(withPoints([row('Cruise', null, 2800, 'continuous')], 'missing')).includes('key_points'));
  assert.ok(!ids({ ...base, fields: { ...base.fields, key_points: { status: 'not_known_yet' } } }).includes('key_points'));
  const counts = W.sectionAnswerCounts(key, withPoints([row('Cruise', null, 2800, 'continuous')], 'missing'));
  assert.equal(counts.invalid, 1);
  // a legacy complete S1 still satisfies the slot while nothing is stored
  assert.ok(!ids({ ...base, fields: { ...base.fields, '1_4': { status: 'filled', value: { torque_Nm: 3, speed_rpm: 6000 } } } }).includes('key_points'));
  assert.ok(ids({ ...base, fields: { ...base.fields, '1_4': { status: 'filled', value: { speed_rpm: 6000 } } } }).includes('key_points'));
});

test('the key card holds the key points and no longer the separate S1 / peak fields', () => {
  const key = buildSpecificationCards(schema).find((card) => card.id === 'key');
  assert.deepEqual(key.fields.map((f) => f.id), ['1_1', 'key_points', '2_1', '3_1', '3_2', 'load_type']);
});
