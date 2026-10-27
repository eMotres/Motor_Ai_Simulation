// node --test: the three P2 findings of the second independent review of the wizard (R2-01 .. R2-03).  Pure logic and
// source-level structure guards; all values are made up.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { buildWizardSteps } from '../components/specWizard/steps.ts';
import { hiddenCompanions, reviewItems } from '../components/specWizard/reviewSummary.ts';
import { visibleProperties } from '../specGrid.ts';
import * as W from '../wizardState.ts';

const read = (rel) => readFileSync(new URL(rel, import.meta.url), 'utf8');
const schema = JSON.parse(read('../../../../../src/motor_ai_sim/development/schema.json'));
const v1 = JSON.parse(read('../../../../../src/motor_ai_sim/development/schema_v1.json'));
const en = JSON.parse(read('../../../locales/en/development.json'));
const zh = JSON.parse(read('../../../locales/zh-CN/development.json'));
const field = (id) => schema.sections.flatMap((s) => s.fields ?? []).find((f) => f.id === id);
const step = (id, list = buildWizardSteps(schema)) => list.find((s) => s.id === id);
const filled = (value) => ({ status: 'filled', value });
const draft = (fields = {}) => ({ schema_version: 2, fields, mission_profile: [], production: {} });
const get = (obj, path) => path.split('.').reduce((o, k) => o?.[k], obj);
const tr = (key, args = {}) => { const v = get(en, key); return typeof v === 'string' ? v.replace(/\{(\w+)\}/g, (_, k) => String(args[k] ?? '')) : key; };
const wizardSource = (name) => readFileSync(new URL(`../components/specWizard/${name}`, import.meta.url), 'utf8');

test('R2-01: v1 legacy cooling answers stay in the review (description beside the implied medium conditions)', () => {
  const v1Step = step('cooling', buildWizardSteps(v1));
  const items = (value) => reviewItems(v1Step, { schema_version: 1, fields: { '3_1': filled(value) }, mission_profile: [], production: {} }, { lang: 'en', t: tr });
  for (const type of ['liquid cooling', 'air', 'submerged']) {
    const hit = items({ cooling_type: type }).find((i) => i.id === '3_1');
    assert.ok(hit, `v1 ${type} is shown`);
    assert.equal(hit.value, type);
  }
  const mixed = items({ cooling_type: 'liquid cooling', coolant: 'Glycol', inlet_temperature_C: 55 }).find((i) => i.id === '3_1').value;
  assert.match(mixed, /liquid cooling/); assert.match(mixed, /Glycol/); assert.match(mixed, /55/);
});

test('R2-01: a v2 answer holding only the old description stays; an explicit medium keeps hiding it and every inactive condition; nothing is rewritten', () => {
  const items = (value) => reviewItems(step('cooling'), draft({ '3_1': filled(value) }), { lang: 'en', t: tr });
  assert.equal(items({ cooling_type: 'air' }).find((i) => i.id === '3_1').value, 'air');
  assert.match(items({ cooling_type: 'forced air from the propeller', airflow: { mode: 'Propeller wash', speed_mps: 12 } }).find((i) => i.id === '3_1').value, /forced air from the propeller/);
  const explicit = items({ cooling_medium: 'air', cooling_type: 'liquid cooling', airflow: { mode: 'Still air' }, inlet_temperature_C: 55 }).find((i) => i.id === '3_1').value;
  assert.doesNotMatch(explicit, /liquid cooling|55/);
  assert.match(explicit, /Still air/);
  const d = draft({ '3_1': filled({ cooling_type: 'liquid cooling', coolant: 'Glycol' }) }), before = JSON.stringify(d);
  reviewItems(step('cooling'), d, { lang: 'en', t: tr });
  assert.equal(JSON.stringify(d), before);
});

test('R2-02: an inverted generator window is invalid on the page; partial, equal and unknown windows stay allowed; counts do not move', () => {
  const f = field('2_13');
  assert.deepEqual(f.ordered_pairs, [['ac_min_V', 'ac_max_V'], ['at_rpm_min', 'at_rpm_max']]);
  const inverted = { ac_min_V: 520, ac_max_V: 380, basis: 'line_line_rms', at_rpm_min: 1500, at_rpm_max: 600 };
  assert.deepEqual(W.invertedPairs(f, inverted), [['ac_min_V', 'ac_max_V'], ['at_rpm_min', 'at_rpm_max']]);
  assert.deepEqual(W.invertedPairs(f, { at_rpm_min: 1500, at_rpm_max: 600 }), [['at_rpm_min', 'at_rpm_max']]);
  assert.equal(W.fieldValueValid(f, inverted), false);
  assert.equal(W.fieldAnswerComplete(f, filled(inverted)), false);
  for (const ok of [{ ac_max_V: 400 }, { ac_min_V: 400 }, { ac_min_V: 380, ac_max_V: 380 }, { ac_min_V: 380, ac_max_V: 520, at_rpm_min: 600, at_rpm_max: 1500 }, { ac_min_V: '', ac_max_V: 400 }, { basis: 'phase_rms' }]) {
    assert.deepEqual(W.invertedPairs(f, ok), [], JSON.stringify(ok));
    assert.equal(W.fieldAnswerComplete(f, filled(ok)), true, JSON.stringify(ok));
  }
  const d = draft({ '2_13': filled(inverted) });
  assert.deepEqual(W.invertedWindowFieldIds(schema.sections, d), ['2_13']);
  assert.deepEqual(W.invertedWindowFieldIds(schema.sections, draft({ '2_13': { status: 'not_known_yet', value: inverted } })), []);
  assert.deepEqual(W.invertedWindowFieldIds(schema.sections, draft({ '2_13': filled({ ac_max_V: 400 }) })), []);
  assert.deepEqual(W.invertedWindowFieldIds(schema.sections, draft({ '1_1': filled('x') })), []);
  // optional and count-neutral
  const g = buildWizardSteps(schema, 'generator');
  const base = W.wizardCompleteness(g, draft()), withInverted = W.wizardCompleteness(g, d);
  assert.deepEqual(withInverted, base);
  // the page shows a red alert and Submit lists the field (source guards)
  assert.match(wizardSource('useSpecFields.tsx'), /invertedPairs\(f, answer\.value\)\.length > 0 && <Typography role="alert"/);
  assert.match(read('../components/DevelopmentPage.tsx'), /invertedWindowFieldIds\(sections, draftRef\.current\)/);
  for (const k of ['wizard.windowInverted', 'wizard.otherBound']) assert.ok(get(en, k) && get(zh, k), k);
  // v1 has no pairs
  assert.equal(v1.sections.flatMap((s) => s.fields).some((x) => x.ordered_pairs), false);
});

test('R2-03: the generator output voltage is ONE worst-case value per quantity - the lowest voltage at its speed; the other bounds sit in a tooltip', () => {
  const f = field('2_13');
  assert.deepEqual(f.properties.filter((p) => p.ui_hidden).map((p) => p.id), ['ac_max_V', 'at_rpm_max']);
  assert.deepEqual(visibleProperties(f.properties).map((p) => p.id), ['ac_min_V', 'basis', 'at_rpm_min']);
  for (const lang of ['en', 'zh-CN']) for (const p of visibleProperties(f.properties)) assert.doesNotMatch(String(p.label[lang]), /最小|最大|Minimum|Maximum|minimum|maximum/);
  const stored = { ac_min_V: 380, ac_max_V: 520, basis: 'line_line_rms', at_rpm_min: 600, at_rpm_max: 1500 };
  const d = draft({ '2_13': filled(stored) }), before = JSON.stringify(d);
  const g = step('supply', buildWizardSteps(schema, 'generator'));
  const of = (dr, lang = 'en') => reviewItems(g, dr, { lang, t: tr, profile: 'generator' }).find((i) => i.id === '2_13');
  const item = of(d);
  assert.equal(item.value, 'Lowest AC voltage 380 V · Line-to-line, RMS · At speed 600 rpm');
  assert.doesNotMatch(item.value, /520|1500|–/);
  assert.equal(item.title, 'Also stored: Maximum AC voltage 520 V\nAlso stored: At speed, maximum 1500 rpm');
  assert.equal(JSON.stringify(d), before);                                   // storage is untouched
  const zhItem = of(d, 'zh-CN');
  assert.match(zhItem.value, /最低交流电压 380 V/);
  assert.doesNotMatch(zhItem.value, /520|1500/);
  assert.deepEqual(hiddenCompanions(f, stored, 'en', tr).map((x) => [x.id, x.low]), [['ac_max_V', 'ac_min_V'], ['at_rpm_max', 'at_rpm_min']]);
  // no worst-case value yet: the stored upper bound is still readable, labelled as itself
  const only = of(draft({ '2_13': filled({ ac_max_V: 520 }) }));
  assert.equal(only.value, 'Maximum AC voltage 520 V');
  assert.equal(only.title, undefined);
  // a lone worst-case value has no tooltip; a field without pairs never has one
  const lone = of(draft({ '2_13': filled({ ac_min_V: 380 }) }));
  assert.equal(lone.value, 'Lowest AC voltage 380 V');
  assert.equal(lone.title, undefined);
  assert.equal(reviewItems(step('supply'), draft({ '2_1': filled({ voltage_min_V: 400, voltage_nominal_V: 480, voltage_max_V: 520 }) }), { lang: 'en', t: tr }).find((i) => i.id === '2_1').title, undefined);
  // the page passes the companions to the property label's tooltip; the Review wraps the value in a tooltip
  assert.match(wizardSource('useSpecFields.tsx'), /hiddenCompanions\(f, objectValue, lang, tr\)/);
  assert.match(wizardSource('ReviewStep.tsx'), /item\.title/);
});
