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
  // liquid also needs the coolant inlet temperature (the thermal basis), underwater the water temperature (owner 2026-10-26)
  assert.equal(W.fieldAnswerComplete(field('3_1'), filled({ cooling_medium: 'liquid' })), false);
  assert.equal(W.fieldAnswerComplete(field('3_1'), filled({ cooling_medium: 'liquid', inlet_temperature_C: 50 })), true);
  assert.equal(W.fieldAnswerComplete(field('3_1'), filled({ cooling_medium: 'submerged', water_temperature_C: 20 })), true);
  assert.equal(W.fieldAnswerComplete(field('3_1'), filled({ cooling_medium: 'air' })), true);
  assert.equal(W.fieldAnswerComplete(field('3_1'), filled({ cooling_type: 'still air' })), true);
  // an inferred medium is never written, so a draft with only conditions stays incomplete until the click
  assert.equal(W.fieldAnswerComplete(field('3_1'), filled({ airflow: { speed_mps: 5 } })), false);
});

test('the load switch: five buttons; finer stored types sit under their button and stay stored', () => {
  const five = ['air_propeller', 'marine_propeller', 'wheel', 'turbine', 'shaft'];
  for (const stored of ['air_propeller', 'unknown', undefined, 'wheel', 'pump', 'engine_driven_generator', 'turbine', 'robot_joint', 'water_jet']) assert.deepEqual(C.loadSwitchChoices(stored), five);
  assert.equal(C.loadGroup('engine_driven_generator'), 'shaft'); assert.equal(C.loadGroup('pump'), 'shaft'); assert.equal(C.loadGroup('robot_joint'), 'shaft');
  assert.equal(C.loadGroup('water_jet'), 'marine_propeller'); assert.equal(C.loadGroup('unknown'), null); assert.equal(C.loadGroup(undefined), null);
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

test('load x cooling: every combination that occurs is offered, the impossible ones are not', () => {
  assert.deepEqual(C.coolingChoices('air_propeller'), ['air', 'liquid']);            // the one refusal: never under water
  assert.deepEqual(C.coolingChoices('wheel'), ['air', 'liquid']);                      // a wheel drive never runs under water (owner 2026-10-26)
  assert.deepEqual(C.coolingChoices('marine_propeller'), ['submerged', 'liquid', 'air']);
  assert.deepEqual(C.coolingChoices('water_jet'), ['submerged', 'liquid', 'air']);
  assert.deepEqual(C.coolingChoices('turbine'), ['air', 'submerged', 'liquid']);
  assert.deepEqual(C.coolingChoices('engine_driven_generator'), ['air', 'liquid', 'submerged']);
  assert.deepEqual(C.coolingChoices(undefined), C.COOLING_MEDIA);
  const modes = prop('3_1', 'airflow').properties.find((p) => p.id === 'mode').suggestions;
  const v = (l) => C.airflowSuggestions(l, undefined) === l && l.map((s) => s.value);
  assert.deepEqual(v(modes), ['Still air', 'Fan', 'Propeller wash', 'Ram air']);
  assert.deepEqual(C.airflowSuggestions(modes, 'air_propeller').map((s) => s.value), ['Propeller wash', 'Ram air', 'Still air', 'Fan']);
  assert.deepEqual(C.airflowSuggestions(modes, 'wheel').map((s) => s.value), ['Still air', 'Ram air', 'Fan']);
  assert.deepEqual(C.airflowSuggestions(modes, 'shaft').map((s) => s.value), ['Still air', 'Fan', 'Ram air']);
  assert.deepEqual(C.airflowSuggestions(modes, 'marine_propeller').map((s) => s.value), ['Still air', 'Fan']);
  // an air propeller still blows over a liquid-cooled housing
  assert.deepEqual(C.coolingPropertyIds('liquid', 'air_propeller'), [...C.COOLING_PROPERTIES.liquid, 'airflow']);
  assert.deepEqual(C.coolingPropertyIds('liquid', 'shaft'), [...C.COOLING_PROPERTIES.liquid, 'airflow']);   // a fan over a jacket
  // a liquid jacket inside a submerged pod: coolant AND the water around the pod
  assert.deepEqual(C.coolingPropertyIds('liquid', 'marine_propeller'), [...C.COOLING_PROPERTIES.liquid, ...C.COOLING_PROPERTIES.submerged]);
  assert.equal(C.naturalAirflow('liquid', 'air_propeller'), 'Propeller wash'); assert.equal(C.naturalAirflow('air', 'shaft'), 'Still air');
  assert.equal(C.naturalAirflow('submerged', 'turbine'), null); assert.equal(C.naturalAirflow('liquid', 'marine_propeller'), null);
  // airflow never hides a coolant circuit (Astra review 2026-10-25)
  assert.deepEqual(C.coolingMedium({ cooling_type: 'Liquid jacket with fan airflow', coolant: 'Glycol', flow_rate: 5, airflow: { mode: 'Fan' } }), { medium: 'liquid', inferred: true });
  assert.deepEqual(C.coolingMedium({ cooling_type: 'PPG loop, heat exchanger to seawater' }), { medium: 'liquid', inferred: true });
  // a negated circuit ('no internal coolant loop') is not a loop (Astra re-review 2026-10-25)
  assert.deepEqual(C.coolingMedium({ cooling_type: 'Heat rejected through the submerged housing; no internal coolant loop.', coolant: 'Sea water outside' }), { medium: 'submerged', inferred: true });
});

test('choosing a load settles the cooling it brings or cannot run with; a fitting choice is kept', () => {
  // an air propeller with a misclicked "underwater" -> air + propeller wash
  assert.deepEqual(C.coolingForLoad({ cooling_type: 'Air', cooling_medium: 'submerged', airflow: {} }, 'air_propeller'), { cooling_type: 'Air', cooling_medium: 'air', airflow: { mode: 'Propeller wash' } });
  assert.deepEqual(C.coolingForLoad({}, 'air_propeller'), { cooling_medium: 'air', airflow: { mode: 'Propeller wash' } });
  assert.equal(C.coolingForLoad({ cooling_medium: 'air', airflow: { mode: 'Propeller wash' } }, 'air_propeller'), null);
  // liquid behind an air propeller is kept; the wash over the housing is filled in
  assert.deepEqual(C.coolingForLoad({ cooling_medium: 'liquid', coolant: 'Water' }, 'air_propeller'), { cooling_medium: 'liquid', coolant: 'Water', airflow: { mode: 'Propeller wash' } });
  // a pod motor: water propeller, sea water around the pod -> underwater confirmed (not the 'coolant' -> liquid guess)
  assert.deepEqual(C.coolingForLoad({ cooling_type: 'Heat rejected through the submerged housing', coolant: 'Sea water outside' }, 'marine_propeller'), { cooling_type: 'Heat rejected through the submerged housing', coolant: 'Sea water outside', cooling_medium: 'submerged' });
  // an engine-driven generator on a glycol jacket -> liquid kept; the housing airflow takes the shaft's default (still air)
  assert.deepEqual(C.coolingForLoad({ cooling_medium: 'liquid', coolant: 'Glycol' }, 'engine_driven_generator'), { cooling_medium: 'liquid', coolant: 'Glycol', airflow: { mode: 'Still air' } });
  assert.equal(C.coolingForLoad({ cooling_medium: 'liquid', coolant: 'Glycol', airflow: { mode: 'Fan' } }, 'engine_driven_generator'), null);
  // propeller wash cannot exist without an air propeller: a wheel takes its default (still air); a typed own mode stays
  assert.deepEqual(C.coolingForLoad({ cooling_medium: 'air', airflow: { mode: 'Propeller wash', speed_mps: 8 } }, 'wheel'), { cooling_medium: 'air', airflow: { mode: 'Still air', speed_mps: 8 } });
  assert.equal(C.coolingForLoad({ cooling_medium: 'air', airflow: { mode: 'duct from the radiator fan' } }, 'shaft'), null);
  // a shaft with nothing chosen asks; a turbine keeps underwater (hydro)
  assert.equal(C.coolingForLoad({}, 'shaft'), null);
  assert.equal(C.coolingForLoad({ cooling_medium: 'submerged' }, 'turbine'), null);
  // ICU single braces (i18n/core.ts uses ICU); '{{x}}' would print literally (Astra review 2026-10-25)
  for (const lang of [en, zh]) for (const m of [lang.coolingReason.air_propeller.submerged, lang.coolingReason.wheel.submerged, lang.airflowReason.propellerWash, lang.airflowReason.ramAir]) assert.ok(m && !m.includes('{{'));
});

test('Astra review 2026-10-25: a hydro turbine stays submerged; a water-jet gets the propeller loads', () => {
  assert.equal(C.coolingForLoad({ cooling_medium: 'submerged', depth_m: 20 }, 'turbine'), null);
  const ids = ['axial_thrust_N', 'reverse_thrust_N', 'radial_load_N', 'bending_moment_Nm', 'gyroscopic_moment_Nm', 'tilt_rate'];
  assert.deepEqual(C.loadPropertyIds('water_jet', ids, {}), ['axial_thrust_N', 'reverse_thrust_N', 'gyroscopic_moment_Nm', 'tilt_rate']);
  assert.deepEqual(C.loadPropertyIds('engine_driven_generator', ids, {}), ['axial_thrust_N', 'radial_load_N', 'bending_moment_Nm']);
});
