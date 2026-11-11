// node --test: the new-customer path fixes of 2026-11-06 - a decimal comma in every number box, AC mains as a power supply, the status a customer
// cannot move, the one progress number, the 428 said in its own words, the locale mirror of every new key.  All values are made up.
import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { decimalNumber, parseDecimal } from '../../../lib/decimalText.ts';
import { editBatteryField, batteryFieldNumber } from '../../../lib/configuratorBattery.ts';
import { specBatteryFieldEdit } from '../specBattery.ts';
import { suggestionValue } from '../specChoices.ts';
import { supplyEmpty, supplySummary, supplyType, withAcMains, withSupplyType } from '../components/specWizard/supply.ts';
import { buildWizardSteps, stillOpen } from '../components/specWizard/steps.ts';
import { allowedProjectStatuses } from '../motorStatus.ts';
import { createErrorText } from '../createProject.ts';
import * as W from '../wizardState.ts';

const read = (rel) => readFileSync(new URL(rel, import.meta.url), 'utf8');
const schema = JSON.parse(read('../../../../../src/motor_ai_sim/development/schema.json'));
const en = (ns) => JSON.parse(read(`../../../locales/en/${ns}.json`));
const zh = (ns) => JSON.parse(read(`../../../locales/zh-CN/${ns}.json`));
const get = (obj, path) => path.split('.').reduce((o, k) => o?.[k], obj);
const tr = (key, args = {}) => { const v = get(en('development'), key); return typeof v === 'string' ? v.replace(/\{(\w+)\}/g, (_, k) => String(args[k] ?? '')) : key; };
const fieldOf = (id) => schema.sections.flatMap((s) => s.fields ?? []).find((f) => f.id === id);
const draft = (fields = {}) => ({ schema_version: 2, fields, mission_profile: [], production: {} });

test('a decimal comma is a decimal point in every number path of the form (0,35 is never 35)', () => {
  assert.equal(parseDecimal('0,35'), 0.35);
  assert.equal(parseDecimal('143,2'), 143.2);
  assert.equal(parseDecimal('-5'), -5);
  assert.equal(parseDecimal(''), 'empty');
  for (const half of ['-', '.', ',', '1e', 'abc', '1,2,3']) assert.equal(parseDecimal(half), 'partial', half);
  assert.ok(Number.isNaN(decimalNumber('x')));
  assert.equal(decimalNumber(' 0,5 '), 0.5);
  // the battery block (cells x cell voltage) and the menu fields use the same helper
  assert.equal(batteryFieldNumber(editBatteryField({ type: 'NMC', cells: 10, nom: 3.7, max: 4.2, min: 3 }, 'nom', '3,65'), 'nom'), 3.65);
  const allowed = new Set(['series_cells', 'cell_voltage_nominal_V', 'cell_voltage_min_V', 'cell_voltage_max_V']);
  assert.equal(specBatteryFieldEdit({}, 'nom', '3,2', allowed).cell_voltage_nominal_V, 3.2);
  assert.equal(suggestionValue([1, 2], '1,5', true), 1.5);
});

test('AC mains is a supply of its own: line-line voltage and frequency, no battery voltage; old answers still read as before', () => {
  assert.equal(supplyType({ supply_type: 'ac_mains' }), 'ac_mains');
  assert.equal(supplyType({ supply_type: 'dc_source' }), 'dc_source');
  assert.equal(supplyType({ voltage_nominal_V: 48 }), 'battery');
  assert.equal(supplyType({ supply_type: 'mains' }), 'battery');
  const ac = withAcMains(withSupplyType({ chemistry: 'NMC' }, 'battery'), { ac_line_voltage_V: 400 });
  assert.deepEqual(ac, { chemistry: 'NMC', supply_type: 'ac_mains', ac_line_voltage_V: 400 });       // nothing stored for the other choices is removed
  assert.equal(withAcMains(ac, { ac_frequency_Hz: Number.NaN }).ac_frequency_Hz, '');                  // a cleared box stores nothing
  const t = (key, args = {}) => tr(key, args);
  assert.equal(supplySummary({ supply_type: 'ac_mains', ac_line_voltage_V: 400, ac_frequency_Hz: 50 }, t), 'AC mains 400 V line-line, 50 Hz');
  assert.equal(supplySummary({ supply_type: 'ac_mains' }, t), '');
  assert.equal(supplyEmpty({ supply_type: '', ac_line_voltage_V: '' }), true);
  // the schema asks both values for AC mains and no battery voltage; a DC source and a battery stay as they were
  const f = fieldOf('2_1');
  const required = (value) => f.properties.filter((p) => W.propertyRequired(p, value)).map((p) => p.id).sort();
  assert.deepEqual(required({ supply_type: 'ac_mains' }), ['ac_frequency_Hz', 'ac_line_voltage_V']);
  assert.deepEqual(required({ supply_type: 'dc_source' }), ['dc_source_V']);
  assert.deepEqual(required({}), ['voltage_nominal_V']);
  assert.equal(W.choiceHolds({ property: 'supply_type', equals_any: ['dc_source', 'ac_mains'] }, { supply_type: 'ac_mains' }), true);
  assert.equal(W.choiceHolds({ property: 'supply_type', equals: 'dc_source' }, { supply_type: 'ac_mains' }), false);
});

test('the stage of a project is staff\'s: a customer is offered no other stage (owner 2026-11-06)', () => {
  assert.deepEqual(allowedProjectStatuses(false, 'spec'), ['spec']);
  assert.ok(allowedProjectStatuses(true, 'spec').includes('quote'));
});

test('the data agreement said at New project: a refused 428 reads as the page\'s own line', () => {
  const t = (key) => key;
  const e = Object.assign(new Error('Accept the AeroStator Platform Agreement (version 1.0) to continue.'), { status: 428, body: { detail: { code: 'agreement_required', at: 'simulation' } } });
  assert.equal(createErrorText(e, t), 'agreementNeeded');
  assert.equal(createErrorText(Object.assign(new Error('boom'), { status: 500 }), t), 'boom');
});

test('Review: the counts line and the "Still open" list come from the same required fields', () => {
  const steps = buildWizardSteps(schema, 'industrial', { staff: false }).filter((s) => s.kind !== 'review');
  const d = draft({ '1_1': { status: 'not_known_yet' }, load_inertia_kg_m2: { status: 'not_known_yet' } });   // one required, one optional, both "answer later"
  const all = stillOpen(steps, d);
  const required = all.filter((o) => { const f = steps.flatMap((s) => s.fields).find((x) => x.id === o.fieldId); return W.fieldRequired(f, d) && !f.handled_by; });
  assert.ok(all.some((o) => o.fieldId === 'load_inertia_kg_m2' && o.state === 'unknown'), 'the optional field is in the raw list');
  assert.ok(!required.some((o) => o.fieldId === 'load_inertia_kg_m2'));
  assert.equal(required.filter((o) => o.state === 'unknown').length, 1);
});

test('every new key has its zh-CN mirror', () => {
  const keys = { development: ['projectStatusTipCustomer', 'statusCustomerQuote', 'closeProject', 'agreementNeeded', 'saveRejected.title', 'saveRejected.range', 'saveRejected.number', 'saveRejected.other',
    'wizard.review.submittedLine', 'wizard.review.submittedChanged', 'wizard.review.noChanges', 'wizard.review.noChangesTip', 'wizard.supply.ac_mains', 'wizard.supply.reviewAcVolts', 'wizard.supply.reviewAcHz'],
    motors: ['startProject.text', 'startProject.button'], common: ['digest.tipCustomer', 'legal.agreement.title', 'legal.agreement.privateDone'] };
  for (const [ns, list] of Object.entries(keys)) for (const key of list) {
    assert.ok(get(en(ns), key), `EN ${ns}.${key}`); assert.ok(get(zh(ns), key), `zh-CN ${ns}.${key}`);
  }
  // no customer text names the commercial stage, and nobody is told to simulate when the account cannot
  assert.doesNotMatch(get(en('development'), 'projectStatusTipCustomer') + get(en('development'), 'statusCustomerQuote'), /quote/i);
  assert.doesNotMatch(get(en('common'), 'legal.agreement.privateDone'), /simulate/i);
  assert.doesNotMatch(get(en('common'), 'legal.agreement.title'), /simulate/i);
});

const page = () => read('../components/DevelopmentPage.tsx');

test('the project page resets only when the account changed: a replayed effect never throws the customer out to the list', () => {
  // StrictMode replays every effect of a subtree coming back from a Suspense fallback; the reset must see that nothing changed
  assert.match(page(), /const identity = user\?\.email \?\? '';\s+if \(resetForIdentity\.current === identity\) return;\s+resetForIdentity\.current = identity;\s+if \(saveTimer\.current\)/);
  // and the first open of Tests waits inside its dialog, not over the whole page
  assert.match(read('../ProjectMotorTests.tsx'), /<Suspense fallback=[^>]*\/>}><TestsPanel /);
});

test('the name a save gave the project goes to the page and the list; only staff get the rename control', () => {
  assert.match(page(), /const name = result\.project_title;[\s\S]{0,200}setSelected\([\s\S]{0,200}setProjects\(/);
  assert.match(page(), /\{canManageGlobally && <Tooltip title=\{tr\('renameProjectTip'\)\}>/);
  assert.match(page(), /developmentApi\.update\(id, \{ title: name \}\)/);
});

test('keys asked for by the screens exist in both languages', () => {
  const keys = { common: ['tabs.designer'], production: ['tests.group.load_point'], development: ['renameProject', 'renameProjectTip'] };
  for (const [ns, list] of Object.entries(keys)) for (const key of list) {
    assert.ok(get(en(ns), key), `EN ${ns}.${key}`); assert.ok(get(zh(ns), key), `zh-CN ${ns}.${key}`);
  }
});

test('on a phone the chat bubble keeps off the Back / Next bar, and a label tooltip never takes a click meant for what lies under it', () => {
  assert.match(read('../../../components/support/SupportWidget.tsx'), /body:has\(\[data-wizard-bar\]\) &/);
  assert.match(read('../components/specWizard/SpecWizard.tsx'), /<Stack data-wizard-bar /);
  // ONE assistant (2026-11-11): the separate specification panel is gone; the chat bubble is the assistant inside the Specification too
  assert.ok(!existsSync(new URL('../components/SpecAssistantPanel.tsx', import.meta.url)));
  assert.match(read('../components/DevelopmentPage.tsx'), /setAssistantSpec\(\{ projectId: selected\.id/);
  assert.match(read('../components/specWizard/useSpecFields.tsx'), /const labelRow = [^\n]*<Tooltip disableInteractive /);
});
