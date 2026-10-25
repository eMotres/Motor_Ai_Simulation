// node --test: the owner's 2026-10-26 specification changes - submit button and its error, maximum speed, prototypes default,
// the ripple field, the Construction card, retired fields, the controller block (source-level + pure tests).
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { submitFailureKind, submitFailureKey, canSubmitSpec, SUBMIT_FAILURE_KEYS } from '../submitFailure.ts';
import { maximumSpeedRpm, proofSpeedRpm, storedOverspeedRpm } from '../speedLimits.ts';
import { buildSpecificationCards, CONSTRUCTION_FIELDS } from '../specificationLayout.ts';
import * as W from '../wizardState.ts';

const read = (rel) => readFileSync(new URL(rel, import.meta.url), 'utf8');
const page = read('../components/DevelopmentPage.tsx');
const schema = JSON.parse(read('../../../../../src/motor_ai_sim/development/schema.json'));
const en = JSON.parse(read('../../../locales/en/development.json'));
const zh = JSON.parse(read('../../../locales/zh-CN/development.json'));
const field = (id) => schema.sections.flatMap((s) => s.fields ?? []).find((f) => f.id === id);
const filled = (value) => ({ status: 'filled', value });
const draft = (fields = {}) => ({ schema_version: 2, fields, mission_profile: [], production: {} });
const get = (obj, path) => path.split('.').reduce((o, k) => o?.[k], obj);

test('the pre-submit save failure says WHY in one line', () => {
  assert.equal(submitFailureKind({ status: 409 }), 'conflict');
  assert.equal(submitFailureKind({ status: 403 }), 'forbidden');
  for (const other of [{ status: 500 }, { status: 0 }, new Error('x'), null, undefined, 'x']) assert.equal(submitFailureKind(other), 'other');
  assert.equal(submitFailureKey({ status: 409 }), 'wizard.saveConflict');
  assert.equal(submitFailureKey({ status: 403 }), 'wizard.submitForbidden');
  assert.equal(submitFailureKey(new Error('boom')), 'saveBeforeSubmit');          // the existing generic text
  for (const key of Object.values(SUBMIT_FAILURE_KEYS)) {
    assert.ok(get(en, key), `EN ${key}`); assert.ok(get(zh, key), `zh-CN ${key}`);
  }
});

test('only people who can edit the specification see the submit button; its tooltip says what it does', () => {
  assert.equal(canSubmitSpec(true), true);
  assert.equal(canSubmitSpec(false), false);
  assert.match(page, /canSubmitSpec\(canEditSpec\) && <Tooltip title=\{<>\{tr\('wizard\.submitTip'\)\}<br \/>\{tr\('wizard\.reviewHint'\)\}<\/>\}>/);
  // the grey "Unknown answers stay visible..." help line is gone from the card (it lives in the button tooltip)
  assert.doesNotMatch(page, /<Box><HelpTip title=\{tr\('wizard\.reviewHint'\)\} \/><\/Box>/);
  // a 409 offers the Reload action
  assert.match(page, /submitConflict \? <Button color="inherit" size="small" onClick=\{\(\) => void reloadDraft\(\)\}>/);
  assert.match(page, /tr\(submitFailureKey\(e\)\)/);
  assert.match(en.wizard.submitTip, /numbered version/); assert.match(en.wizard.submitTip, /engineering review/);
});

test('Maximum speed is the ONE speed limit; the proof speed is that speed, a larger stored overspeed wins', () => {
  assert.equal(field('1_6').label.en, 'Maximum speed');
  const visible = field('1_6').properties.filter((p) => !p.ui_hidden).map((p) => p.id);
  assert.deepEqual(visible, ['maximum_rpm']);
  for (const id of ['overspeed_rpm', 'proof_or_runaway', 'runaway_rpm']) assert.equal(field('1_6').properties.find((p) => p.id === id).ui_hidden, true);
  assert.equal(field('1_6').required, true);
  assert.equal(field('1_6').properties.find((p) => p.id === 'maximum_rpm').required, true);
  assert.equal(W.fieldAnswerComplete(field('1_6'), filled({ maximum_rpm: 9000 })), true);   // the hidden ones are never required
  assert.equal(maximumSpeedRpm(draft({ '1_6': filled({ maximum_rpm: 9000 }) })), 9000);
  assert.equal(proofSpeedRpm(draft({ '1_6': filled({ maximum_rpm: 9000 }) })), 9000);          // no 1.2 factor
  assert.equal(proofSpeedRpm(draft({ '1_6': filled({ maximum_rpm: { minimum: 8000, maximum: 9500 } }) })), 9500);
  const legacy = draft({ '1_6': filled({ maximum_rpm: 9000, overspeed_rpm: 10800, runaway_rpm: 14000 }) });
  assert.equal(storedOverspeedRpm(legacy), 10800); assert.equal(proofSpeedRpm(legacy), 10800);   // the stored value wins (shown read-only)
  assert.equal(storedOverspeedRpm(draft({ '1_6': filled({ maximum_rpm: 9000, overspeed_rpm: 8000 }) })), null);
  assert.equal(proofSpeedRpm(draft({ '1_6': { status: 'not_known_yet', value: { maximum_rpm: 9000 } } })), null);
  assert.equal(proofSpeedRpm(draft()), null);
  assert.match(page, /wizard\.storedOverspeed/);   // the stored value is shown read-only in the field's tooltip
  // the value is not an operating point: nothing in the page derives a voltage or KV check from it
  assert.doesNotMatch(page, /maximumSpeedRpm|proofSpeedRpm/);
});

test('Prototypes is the ONE quantity: default 1, counted as filled, pre-series and series per year are not asked', () => {
  const f = field('6_1');
  assert.equal(f.label.en, 'Prototypes');
  assert.deepEqual(f.properties.filter((p) => !p.ui_hidden).map((p) => p.id), ['prototypes']);
  assert.equal(f.properties.find((p) => p.id === 'prototypes').default, 1);
  assert.equal(f.properties.find((p) => p.id === 'prototypes').required, true);
  for (const id of ['pre_series', 'series_per_year']) assert.equal(f.properties.find((p) => p.id === id).ui_hidden, true);
  // new project: the default is there; an empty value is filled; a typed value and "not known yet" are left alone
  assert.equal(get(W.withSpecDefaults(draft()), 'fields.6_1.value.prototypes'), 1);
  assert.equal(W.withSpecDefaults(draft()).fields['6_1'].status, 'filled');
  const empty = draft({ '6_1': filled({ prototypes: '', pre_series: 20 }) });
  const out = W.withSpecDefaults(empty);
  assert.equal(out.fields['6_1'].value.prototypes, 1); assert.equal(out.fields['6_1'].value.pre_series, 20);   // the extras stay
  assert.equal(W.withSpecDefaults(draft({ '6_1': filled({ prototypes: 3 }) })).fields['6_1'].value.prototypes, 3);
  assert.equal(W.withSpecDefaults(draft({ '6_1': { status: 'not_known_yet', value: null } })).fields['6_1'].status, 'not_known_yet');
  assert.equal(W.withSpecDefaults({ schema_version: 1, fields: {} }).fields['6_1'], undefined);                    // version 2 only
  assert.equal(W.withSpecDefaults(out), out);                                                                      // idempotent
  // counted as filled in the completeness (no typing needed)
  const cards = buildSpecificationCards(schema);
  const requirements = cards.find((c) => c.id === 'requirements');
  const q = W.sectionAnswerCounts({ id: 'q', fields: requirements.fields.filter((x) => x.id === '6_1') }, draft());
  assert.deepEqual([q.required, q.answered, q.missing], [1, 1, 0]);
  assert.ok(W.requiredFieldIds(cards, draft()).indexOf('6_1') < 0);
  assert.match(page, /return withSpecDefaults\(\{ \.\.\.DEFAULT_DRAFT/);
});

test('Max torque ripple at continuous operation is the ONE ripple field; its tooltip carries the rule', () => {
  const f = field('1_8');
  assert.equal(f.label.en, 'Max torque ripple at continuous operation');
  assert.equal(f.short_label.en, 'Max torque ripple at continuous operation');
  assert.deepEqual(f.properties.filter((p) => !p.ui_hidden).map((p) => p.id), ['torque_ripple_pct_pkpk']);
  for (const id of ['cogging_pct_pkpk', 'cogging_torque_Nm', 'avoid_frequencies_Hz']) assert.equal(f.properties.find((p) => p.id === id).ui_hidden, true);
  assert.equal(f.required, false);
  for (const loc of ['en', 'zh-CN']) assert.ok(f.help[loc]);
  assert.match(f.help.en, /continuous key point/i); assert.match(f.help.en, /costs torque and efficiency/i);
  assert.equal(f.properties.find((p) => p.id === 'torque_ripple_pct_pkpk').unit, '% pk-pk');
  // a stored cogging value is read as before
  assert.equal(W.fieldAnswerComplete(f, filled({ torque_ripple_pct_pkpk: 5, cogging_pct_pkpk: 2 })), true);
});

test('the Construction card holds every geometric and mass requirement; the retired fields are not asked', () => {
  const cards = buildSpecificationCards(schema);
  const construction = cards.find((c) => c.id === 'construction');
  assert.deepEqual(construction.fields.map((f) => f.id), CONSTRUCTION_FIELDS);
  for (const id of ['4_1', '4_2', '3_3', '3_6', '4_3', '4_4', '4_6']) assert.ok(construction.fields.some((f) => f.id === id), id);
  assert.deepEqual(cards.map((c) => c.id), ['key', 'requirements', 'construction', 'mission', 'review']);
  // between "requirements" and "mission"; titled in both languages
  assert.equal(en.wizard.construction, 'Construction'); assert.equal(zh.wizard.construction, '结构');
  // required stays the same set (nothing was silently dropped), the depth keeps its underwater-only gate
  assert.equal(field('3_6').only_when_cooling_medium[0], 'submerged');
  for (const id of ['4_1', '4_2', '3_3', '4_6']) assert.equal(field(id).required, true);
  // the card is drawn on the same grid, each group on its own row, the loads included
  assert.match(page, /section\.id === 'construction' && <Box sx=\{grid\}>/);
  assert.match(page, /renderLoads\(byId\('4_6'\)!/);
  // battery current and power devices are not in any card
  for (const id of ['2_3', '2_5']) assert.ok(!cards.some((c) => c.fields.some((f) => f.id === id)), id);
  // the stored values stay readable: the schema keeps the fields
  assert.ok(field('2_3')); assert.ok(field('2_5'));
});

test('EN and zh-CN carry every new key', () => {
  for (const key of ['controller.ownDesign', 'controller.fromCatalog', 'controller.linked', 'controller.statedDevices', 'controller.devicesUnknown', 'controller.devicesUnknownTip', 'controller.batteryTooHigh',
    'controller.basis.stated', 'controller.basis.derived', 'controller.basis.figure', 'controller.basis.inferred', 'wizard.construction', 'wizard.saveConflict', 'wizard.submitForbidden', 'wizard.submitTip', 'wizard.storedOverspeed']) {
    assert.ok(get(en, key), `EN ${key}`); assert.ok(get(zh, key), `zh-CN ${key}`);
  }
  assert.equal(get(en, 'wizard.secondTier'), undefined); assert.equal(get(zh, 'wizard.secondTier'), undefined);   // the line is gone
  assert.match(en.controller.batteryTooHigh, /\{max\}.*\{limit\}.*\{controller\}|\{max\}.*\{controller\}.*\{limit\}/);
});

test('the controller block: catalogue picker, supplier as a company, one amber warning, no second tier', () => {
  // the picker and the supplier get their menus from the catalogue; the picker's change goes through the controller rule
  assert.match(page, /const pickController = \(text: string\) => updateDraft\(\(d\) => applyControllerChoice\(/);
  assert.match(page, /f\.id === '2_4' && prop\.id === 'manufacturer_model' \? \(value\) => pickController/);
  assert.match(page, /catalogBrands\(catalog\)/);
  assert.match(page, /data-testid="controller-devices-warning"/);
  assert.match(page, /<Alert severity="warning"[^>]*data-testid="controller-devices-warning"/);
  assert.match(page, /controller\.devicesUnknownTip/);
  assert.match(page, /data-testid="battery-controller-error"/);
  assert.doesNotMatch(page, /wizard\.secondTier/);
  // supplier: free text company, not required, the old choice kept as a hidden property
  const props = Object.fromEntries(field('2_4').properties.map((p) => [p.id, p]));
  assert.equal(props.supplier.suggestions, undefined);
  assert.equal(props.supplier.required, false);
  assert.equal(props.supplier.label.en, 'Supplier (company)');
  assert.equal(props.supplier_legacy.ui_hidden, true); assert.equal(props.catalog_id.ui_hidden, true);
});
