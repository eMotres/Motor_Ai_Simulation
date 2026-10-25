// node --test: the specification's switches and menus (owner 2026-10-25) — cooling medium, load switch, suggestions.
// All values are made up.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import * as C from '../specChoices.ts';
import { readLoadDraft, writeLoadDraft } from '../loadDraftAdapter.ts';
import * as W from '../wizardState.ts';

const schema = JSON.parse(readFileSync(new URL('../../../../../src/motor_ai_sim/development/schema.json', import.meta.url), 'utf8'));
const field = (id) => schema.sections.flatMap((s) => s.fields ?? []).find((f) => f.id === id);
const prop = (fid, pid) => field(fid).properties.find((p) => p.id === pid);
const en = JSON.parse(readFileSync(new URL('../../../locales/en/development.json', import.meta.url), 'utf8'));
const zh = JSON.parse(readFileSync(new URL('../../../locales/zh-CN/development.json', import.meta.url), 'utf8'));

test('a stored cooling_medium wins; otherwise the medium is inferred from the stored values', () => {
  assert.deepEqual(C.coolingMedium({ cooling_medium: 'submerged', airflow: { speed_mps: 4 } }), { medium: 'submerged', inferred: false });
  assert.deepEqual(C.coolingMedium({ airflow: { mode: 'Fan' } }), { medium: 'air', inferred: true });
  assert.deepEqual(C.coolingMedium({ cooling_type: 'forced air from the propeller', inlet_temperature_C: 40 }), { medium: 'air', inferred: true });
  assert.deepEqual(C.coolingMedium({ coolant: 'Water-glycol 50/50' }), { medium: 'liquid', inferred: true });
  assert.deepEqual(C.coolingMedium({ flow_rate: 4 }), { medium: 'liquid', inferred: true });
  assert.deepEqual(C.coolingMedium({ cooling_type: 'submerged in sea water' }), { medium: 'submerged', inferred: true });
  assert.deepEqual(C.coolingMedium({ cooling_type: 'underwater thruster' }), { medium: 'submerged', inferred: true });
  assert.deepEqual(C.coolingMedium({ depth_m: 30 }), { medium: 'submerged', inferred: true });
  assert.deepEqual(C.coolingMedium({ cooling_type: 'water-glycol jacket' }), { medium: 'liquid', inferred: true });
  assert.deepEqual(C.coolingMedium({ cooling_type: 'repair later' }), { medium: null, inferred: false });
  assert.deepEqual(C.coolingMedium({}), { medium: null, inferred: false });
  assert.deepEqual(C.coolingMedium(undefined), { medium: null, inferred: false });
  assert.deepEqual(C.coolingMedium({ cooling_medium: 'invented', airflow: { speed_mps: 3 } }), { medium: 'air', inferred: true });
});

test('each cooling medium lists only its own schema properties, and 3_1 is complete with a medium OR the old text', () => {
  const ids = new Set(field('3_1').properties.map((p) => p.id));
  for (const list of Object.values(C.COOLING_PROPERTIES)) for (const id of list) assert.ok(ids.has(id), id);
  assert.deepEqual(prop('3_1', 'cooling_medium').options.map((o) => o.value), C.COOLING_MEDIA);
  const filled = (value) => ({ status: 'filled', value });
  assert.equal(W.fieldAnswerComplete(field('3_1'), filled({ cooling_medium: 'liquid' })), true);
  assert.equal(W.fieldAnswerComplete(field('3_1'), filled({ cooling_type: 'still air' })), true);
  // an inferred medium is never written, so a draft with only conditions stays incomplete until the click
  assert.equal(W.fieldAnswerComplete(field('3_1'), filled({ airflow: { speed_mps: 5 } })), false);
});

test('the load switch offers air propeller / water propeller / shaft and keeps any other stored type', () => {
  assert.deepEqual(C.loadSwitchChoices('air_propeller'), ['air_propeller', 'marine_propeller', 'shaft']);
  assert.deepEqual(C.loadSwitchChoices('unknown'), ['air_propeller', 'marine_propeller', 'shaft']);
  assert.deepEqual(C.loadSwitchChoices(undefined), ['air_propeller', 'marine_propeller', 'shaft']);
  for (const other of ['wheel', 'pump', 'engine_driven_generator', 'turbine', 'robot_joint', 'water_jet']) assert.deepEqual(C.loadSwitchChoices(other), ['air_propeller', 'marine_propeller', 'shaft', other]);
  const options = field('load_type').options.map((o) => o.value);
  for (const type of C.LOAD_SWITCH) assert.ok(options.includes(type), type);
  assert.equal(en.load.types.shaft, 'Shaft (coupling / gearbox)'); assert.equal(zh.load.types.shaft, '轴输出（联轴器/齿轮箱）');
});

test('reading a draft with an unknown-to-the-switch type keeps it; a switch change resets the curve source like LoadSection', () => {
  const draft = { schema_version: 2, fields: { load_type: { status: 'filled', value: 'wheel' }, load_source: { status: 'filled', value: 'manual' }, load_inertia_kg_m2: { status: 'filled', value: 0.02 } } };
  const load = readLoadDraft(draft);
  assert.equal(load.load_type, 'wheel');
  assert.deepEqual(writeLoadDraft(draft, load).fields.load_type, { status: 'filled', value: 'wheel' });
  const changed = writeLoadDraft(draft, C.withLoadType(load, 'shaft'));
  assert.deepEqual(changed.fields.load_type, { status: 'filled', value: 'shaft' });
  assert.equal(changed.fields.load_source.status, 'missing');
  assert.equal(changed.fields.load_source.value, 'manual'); // the stored source stays, only asked again
  assert.deepEqual(changed.fields.load_inertia_kg_m2, { status: 'filled', value: 0.02 });
  assert.equal(C.withLoadType({ ...load, answer_status: { ...load.answer_status, load_type: 'filled' } }, 'wheel').source, 'manual');
});

test('4_6 shows the axial load first, the propeller or shaft loads, and any other load only while it holds a value', () => {
  const ids = field('4_6').properties.map((p) => p.id);
  assert.equal(prop('4_6', 'axial_thrust_N').required, true); assert.equal(field('4_6').required, true);
  assert.deepEqual(C.loadPropertyIds('air_propeller', ids, {}), ['axial_thrust_N', 'reverse_thrust_N', 'gyroscopic_moment_Nm', 'tilt_rate']);
  assert.deepEqual(C.loadPropertyIds('marine_propeller', ids, { radial_load_N: 40 }), ['axial_thrust_N', 'reverse_thrust_N', 'gyroscopic_moment_Nm', 'tilt_rate', 'radial_load_N']);
  assert.deepEqual(C.loadPropertyIds('shaft', ids, { acceleration: 3, tilt_rate: '' }), ['axial_thrust_N', 'radial_load_N', 'bending_moment_Nm', 'acceleration']);
  assert.deepEqual(C.loadPropertyIds('unknown', ids, {}), ['axial_thrust_N']);
});

test('suggestions: the menu shows the localized label, the EN value is stored, other text is kept as typed', () => {
  const coolant = prop('3_1', 'coolant').suggestions;
  const options = C.suggestionOptions(coolant, 'zh-CN');
  assert.deepEqual(options[0].value, 'Water-glycol 50/50'); assert.equal(options[0].label, '水-乙二醇 50/50');
  assert.equal(C.suggestionDisplay(coolant, 'Water-glycol 50/50', 'zh-CN'), '水-乙二醇 50/50');
  assert.equal(C.suggestionDisplay(coolant, 'Water-glycol 50/50', 'en'), 'Water-glycol 50/50');
  assert.equal(C.suggestionDisplay(coolant, 'Our own blend', 'zh-CN'), 'Our own blend');
  assert.equal(C.suggestionDisplay(coolant, '', 'en'), '');
  assert.equal(C.suggestionValue(coolant, '水-乙二醇 50/50'), 'Water-glycol 50/50');
  assert.equal(C.suggestionValue(coolant, 'Water-glycol 50/50'), 'Water-glycol 50/50');
  assert.equal(C.suggestionValue(coolant, 'Our own blend '), 'Our own blend ');
  assert.equal(C.suggestionValue(coolant, '   '), '');
  // plain numbers: stored as numbers, other numbers accepted, text rejected
  const voltage = prop('2_5', 'voltage_class_V').suggestions;
  assert.deepEqual(C.suggestionOptions(voltage, 'en').map((o) => o.label).slice(0, 2), ['100', '200']);
  assert.equal(C.suggestionDisplay(voltage, 1200, 'zh-CN'), '1200');
  assert.equal(C.suggestionValue(voltage, '1200', true), 1200);
  assert.equal(C.suggestionValue(voltage, '750', true), 750);
  assert.equal(C.suggestionValue(voltage, 'abc', true), undefined);
  assert.equal(C.suggestionValue(voltage, '', true), '');
});

test('every schema suggestion has an EN value and both labels, and sits on a free text or number value', () => {
  const walk = (items, path) => (items ?? []).flatMap((item) => [[item, `${path}${item.id}`], ...walk(item.properties, `${path}${item.id}.`)]);
  const all = walk(schema.sections.flatMap((s) => s.fields ?? []), '').filter(([item]) => item.suggestions);
  assert.ok(all.length >= 15);
  for (const [item, path] of all) {
    assert.ok(['string', 'number', undefined].includes(item.type), path);
    assert.ok(!item.options?.length, `${path} has options and suggestions`);
    for (const s of item.suggestions) {
      if (typeof s === 'number') { assert.equal(item.type, 'number', path); continue; }
      assert.ok(typeof s.value === 'string' && s.value.trim(), path);
      assert.ok(s.label.en && s.label['zh-CN'], `${path}: ${s.value}`);
      assert.equal(s.value, s.label.en, `${path}: the stored value is the EN label`);
    }
  }
});
