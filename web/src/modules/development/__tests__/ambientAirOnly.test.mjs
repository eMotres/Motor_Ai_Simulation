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
