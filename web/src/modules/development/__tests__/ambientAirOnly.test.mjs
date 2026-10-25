// node --test: the maximum ambient temperature (3_2) is asked and required for air cooling only (owner 2026-10-26);
// the load switch is one line.  The case table is shared with tests/test_development_ambient_air_only.py so the web
// and the backend agree.  All values are made up.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import * as C from '../specChoices.ts';
import * as W from '../wizardState.ts';
import { buildSpecificationCards } from '../specificationLayout.ts';

const read = (rel) => readFileSync(new URL(rel, import.meta.url), 'utf8');
const schema = JSON.parse(read('../../../../../src/motor_ai_sim/development/schema.json'));
const cases = JSON.parse(read('./ambient_cases.json'));
const en = JSON.parse(read('../../../locales/en/development.json'));
const zh = JSON.parse(read('../../../locales/zh-CN/development.json'));
const field = (id) => schema.sections.flatMap((s) => s.fields ?? []).find((f) => f.id === id);
const draftFor = (cooling, status) => ({ fields: cooling === null ? {} : { '3_1': { status, value: cooling } } });

test('schema: 3_2 applies to air only and keeps every stored property', () => {
  assert.deepEqual(field('3_2').applies_when_cooling_medium, ['air']);
  assert.deepEqual(field('3_2').properties.map((p) => p.id).sort(), ['altitude_m', 'temperature_max_C', 'temperature_min_C']);
  assert.deepEqual(field('3_2').properties.filter((p) => p.required).map((p) => p.id), ['temperature_max_C']);
});

test('the shared case table: ambient applies to air or an unchosen medium, is required exactly then', () => {
  for (const c of cases.applies) {
    const draft = draftFor(c.cooling, c.status);
    assert.equal(W.fieldApplies(field('3_2'), draft), c.applies, JSON.stringify(c));
    assert.equal(W.fieldRequired(field('3_2'), draft), c.applies, JSON.stringify(c));
  }
});

test('other fields do not depend on the cooling', () => {
  const liquid = draftFor({ cooling_medium: 'liquid' }, 'filled');
  assert.equal(W.fieldRequired(field('3_1'), liquid), true);
  assert.equal(W.fieldRequired(field('3_3'), liquid), true);
  assert.equal(W.fieldRequired(field('3_5'), liquid), false);
});

test('a hidden ambient is not counted, not listed as missing and keeps its stored value', () => {
  const section = { id: '3', fields: [field('3_1'), field('3_2')] };
  const air = draftFor({ cooling_medium: 'air' }, 'filled');
  const liquid = { fields: { '3_1': { status: 'filled', value: { cooling_medium: 'liquid', inlet_temperature_C: 55 } }, '3_2': { status: 'filled', value: { temperature_max_C: 45, altitude_m: 800 } } } };
  assert.equal(W.sectionAnswerCounts(section, air).required, 2);
  assert.equal(W.sectionAnswerCounts(section, liquid).required, 1);
  assert.deepEqual(W.requiredFieldIds([section], air), ['3_2']);
  assert.deepEqual(W.requiredFieldIds([section], liquid), []);
  assert.deepEqual(liquid.fields['3_2'].value, { temperature_max_C: 45, altitude_m: 800 });
  // the whole-page counts agree
  assert.equal(W.wizardCompleteness([section], liquid).total_required, 1);
  assert.equal(W.wizardCompleteness([section], air).total_required, 2);
});

test('a filled maximum ambient is complete on its own (no minimum, no altitude)', () => {
  assert.equal(W.fieldAnswerComplete(field('3_2'), { status: 'filled', value: { temperature_max_C: 45 } }), true);
  assert.equal(W.fieldAnswerComplete(field('3_2'), { status: 'filled', value: { altitude_m: 800 } }), false);
});

test('the thermal basis follows the cooling medium (shared case table)', () => {
  for (const c of cases.thermal_basis) {
    const basis = C.thermalBasis(c.cooling, c.ambient);
    const source = basis.source === 'temperature_max_C' ? `3_2.${basis.source}` : `3_1.${basis.source}`;
    assert.deepEqual([basis.medium, source, basis.temperature_C], [c.medium, c.source, c.temperature_C], JSON.stringify(c));
  }
});

test('3_2 stays one field in the key card; nothing else about the layout changed', () => {
  const key = buildSpecificationCards(schema).find((card) => card.id === 'key');
  assert.deepEqual(key.fields.map((f) => f.id), ['1_1', 'key_points', '2_1', '3_1', '3_2', 'load_type']);
});

test('the load switch is one line with short names and full names in the tooltip', () => {
  const source = read('../components/DevelopmentPage.tsx');
  const group = source.slice(source.indexOf('const renderLoadType'), source.indexOf('if (loading)'));
  assert.match(group, /flexWrap: 'nowrap'/);
  assert.match(group, /whiteSpace: 'nowrap'/);
  assert.match(group, /overflowX: 'auto'/);
  assert.doesNotMatch(group, /flexWrap: 'wrap'/);
  assert.match(group, /load\.short\./);
  assert.match(group, /title=\{\[tr\(`load\.types\./);
  for (const type of C.LOAD_SWITCH) {
    assert.ok(en.load.short[type], `en load.short.${type}`);
    assert.ok(zh.load.short[type], `zh load.short.${type}`);
    assert.ok(en.load.short[type].length <= en.load.types[type].length);
  }
  assert.deepEqual(Object.keys(zh.load.short), Object.keys(en.load.short));
});

test('the ambient line renders only the maximum, hidden for liquid and underwater', () => {
  const source = read('../components/DevelopmentPage.tsx');
  const body = source.slice(source.indexOf('const renderAmbient'), source.indexOf('/** 4_6 for the chosen load'));
  assert.match(body, /fieldApplies\(f, draftRef\.current\)\) return null/);
  assert.match(body, /p\.id === 'temperature_max_C'/);
  assert.doesNotMatch(body, /altitude|temperature_min/);
  assert.match(source, /renderAmbient\(byId\('3_2'\)!\)/);
  assert.ok(en.wizard.ambientMaxNote && zh.wizard.ambientMaxNote);
});

const MODES = ['Propeller wash', 'Ram air', 'Still air', 'Fan'];

test('the web tables equal the shared load x medium x airflow matrix', () => {
  const m = cases.load_matrix;
  for (const group of Object.keys(m)) {
    assert.deepEqual(C.coolingChoices(group), m[group].media, group);
    assert.deepEqual(C.AIRFLOW_BY_LOAD[group].map((x) => x.toLowerCase()), m[group].airflow, group);
    assert.equal(C.DEFAULT_AIRFLOW[group].toLowerCase(), m[group].default_airflow, group);
    assert.ok(m[group].airflow.includes(m[group].default_airflow), `${group}: a default is allowed`);
  }
  assert.ok(m.air_propeller.airflow.includes('ram air') && m.air_propeller.default_airflow !== 'ram air');
});

test('every load x medium x airflow mode: excluded ones are loud problems, the rest pass', () => {
  const m = cases.load_matrix;
  const stored = [...Object.keys(m).map((g) => [g, g]), ['marine_propeller', 'water_jet'], ['shaft', 'pump'], ['shaft', 'engine_driven_generator'], ['shaft', 'robot_joint']];
  for (const [group, load] of stored) for (const medium of ['air', 'liquid', 'submerged']) for (const mode of [null, ...MODES]) {
    const cooling = { cooling_medium: medium, ...(mode ? { airflow: { mode } } : {}) };
    const expected = [];
    if (!m[group].media.includes(medium)) expected.push('3_1.cooling_medium');
    const asked = medium === 'air' || (medium === 'liquid' && group !== 'marine_propeller');
    if (mode && asked && !m[group].airflow.includes(mode.toLowerCase())) expected.push('3_1.airflow.mode');
    const got = C.coolingLoadProblems(cooling, load);
    assert.deepEqual(got.map((p) => p.field), expected, `${load} ${medium} ${mode}`);
    for (const p of got) assert.ok(p.reason.split('.').reduce((o, k) => o?.[k], en), `reason ${p.reason}`), assert.ok(p.reason.split('.').reduce((o, k) => o?.[k], zh), `zh reason ${p.reason}`);
    // underwater never asks the airflow; a water propeller's liquid jacket has none either
    assert.equal(C.coolingPropertyIds(medium, load).includes('airflow'), asked, `${load} ${medium}`);
  }
  // a typed own airflow is not judged
  assert.deepEqual(C.coolingLoadProblems({ cooling_medium: 'air', airflow: { mode: 'duct from the radiator fan' } }, 'shaft'), []);
});

test('the default airflow is pre-selected for a chosen load or an air switch and never overwrites a chosen mode', () => {
  const m = cases.load_matrix;
  for (const group of Object.keys(m)) {
    const def = C.DEFAULT_AIRFLOW[group];
    assert.equal(C.naturalAirflow('air', group), def);
    // a load chosen while the cooling is already air: blank mode takes the default; a chosen allowed mode stays
    const blank = C.coolingForLoad({ cooling_medium: 'air' }, group, 'unknown');
    assert.equal(blank?.airflow?.mode ?? null, def === undefined ? null : def, group);
    for (const chosen of C.AIRFLOW_BY_LOAD[group]) assert.equal(C.coolingForLoad({ cooling_medium: 'air', airflow: { mode: chosen } }, group, 'unknown'), null, `${group} keeps ${chosen}`);
  }
  // only the previous load's own default that the new load cannot make is replaced
  assert.equal(C.coolingForLoad({ cooling_medium: 'air', airflow: { mode: 'Propeller wash' } }, 'wheel', 'air_propeller').airflow.mode, 'Still air');
  assert.equal(C.coolingForLoad({ cooling_medium: 'air', airflow: { mode: 'Ram air' } }, 'marine_propeller', 'air_propeller'), null);   // the user's own pick stays (and shows the error)
  assert.equal(C.coolingForLoad({ cooling_medium: 'air', airflow: { mode: 'Ram air' } }, 'marine_propeller', 'turbine').airflow.mode, 'Still air');
  // a liquid jacket with air over the housing takes the default too; a water propeller's jacket and underwater have none
  assert.equal(C.coolingForLoad({ cooling_medium: 'liquid', inlet_temperature_C: 50 }, 'turbine', 'unknown').airflow.mode, 'Ram air');
  assert.equal(C.coolingForLoad({ cooling_medium: 'liquid' }, 'marine_propeller', 'unknown'), null);
  assert.equal(C.coolingForLoad({ cooling_medium: 'submerged' }, 'turbine', 'unknown'), null);
  // excluded options are offered disabled with their reason
  assert.deepEqual(Object.keys(C.airflowExcluded('wheel')), ['Propeller wash']);
  assert.deepEqual(Object.keys(C.airflowExcluded('marine_propeller')).sort(), ['Propeller wash', 'Ram air']);
  assert.deepEqual(C.airflowExcluded('air_propeller'), {});
});

test('the max immersion depth is asked only underwater, is optional, and the cooling block no longer lists it', () => {
  for (const c of cases.depth_applies) assert.equal(W.fieldApplies(field('3_6'), draftFor(c.cooling, c.status)), c.applies, JSON.stringify(c));
  assert.equal(W.fieldRequired(field('3_6'), draftFor({ cooling_medium: 'submerged' }, 'filled')), false);
  assert.equal(field('3_6').short_label.en, 'Max immersion depth (sealing)');
  assert.equal(field('3_1').properties.find((p) => p.id === 'depth_m').moved_to, '3_6');
  for (const medium of ['air', 'liquid', 'submerged']) for (const load of ['air_propeller', 'marine_propeller', 'shaft']) assert.ok(!C.coolingPropertyIds(medium, load).includes('depth_m'), `${medium} ${load}`);
  // a stored legacy depth still tells the medium apart
  assert.deepEqual(C.coolingMedium({ depth_m: 30 }), { medium: 'submerged', inferred: true });
  // it sits in the Construction card (owner 2026-10-26), right after the housing
  const construction = buildSpecificationCards(schema).find((card) => card.id === 'construction');
  assert.ok(construction.fields.map((f) => f.id).includes('3_6'));
  assert.ok(construction.fields.map((f) => f.id).indexOf('3_6') === construction.fields.map((f) => f.id).indexOf('3_3') + 1);
});

test('no "Guaranteed" in the specification text, EN or zh-CN', () => {
  assert.doesNotMatch(JSON.stringify(schema), /uaranteed|保证/);
  assert.doesNotMatch(JSON.stringify(en), /uaranteed/);
  assert.doesNotMatch(JSON.stringify(zh), /保证/);
});

test('liquid needs the coolant inlet temperature, underwater the water temperature, air neither (shared case table)', () => {
  for (const c of cases.cooling_required) {
    const required = field('3_1').properties.filter((p) => W.propertyRequired(p, c.cooling)).map((p) => p.id);
    assert.deepEqual(required, c.required, JSON.stringify(c.cooling));
  }
  for (const c of cases.cooling_complete) {
    assert.equal(W.fieldAnswerComplete(field('3_1'), { status: 'filled', value: c.cooling }), c.complete, JSON.stringify(c.cooling));
  }
  // an unknown cooling needs nothing; the counts follow
  assert.equal(W.fieldAnswerComplete(field('3_1'), { status: 'not_known_yet', value: { cooling_medium: 'liquid' } }), true);
  const section = { id: '3', fields: [field('3_1'), field('3_2')] };
  const air = { fields: { '3_1': { status: 'filled', value: { cooling_type: 'forced air', airflow: { mode: 'Fan' } } }, '3_2': { status: 'filled', value: { temperature_max_C: 45 } } } };
  assert.equal(W.sectionAnswerCounts(section, air).answered, 2);
  const liquid = { fields: { '3_1': { status: 'filled', value: { cooling_medium: 'liquid', coolant: 'Water' } } } };
  assert.equal(W.sectionAnswerCounts(section, liquid).answered, 0);
  assert.deepEqual(W.requiredFieldIds([section], liquid), ['3_1']);
  liquid.fields['3_1'].value.inlet_temperature_C = 55;
  assert.equal(W.sectionAnswerCounts(section, liquid).answered, 1);
});
