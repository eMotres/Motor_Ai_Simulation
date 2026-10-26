// node --test: the fixes of the independent review of the wizard (F01-F13).  Pure logic and source-level structure guards;
// all values are made up.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { buildWizardSteps, PROFILE_FIELDS, STEP_CARDS, stepCommentRefs, stepIndexForField, stepProgress } from '../components/specWizard/steps.ts';
import { buildChartModel, chartMaxSpeed, chartPoints, maxSpeedPlacement, missionLocks, niceTicks, operatingRows, PLOT_LIMIT, shaftPowerOf } from '../components/specWizard/operatingPoints.ts';
import { DEFAULT_NAMES, defaultRowId, isUntouchedDefaults, profileDefaultRows, wizardProfile } from '../components/specWizard/profile.ts';
import { deliveryConflictLines, reviewItems } from '../components/specWizard/reviewSummary.ts';
import { CONTINUOUS, blankKeyPoint, withKeyPoints } from '../keyPoints.ts';
import { buildSpecificationCards } from '../specificationLayout.ts';
import { LEGACY_CARD_STEPS, parseDevelopmentView, restorableView } from '../viewState.ts';
import * as W from '../wizardState.ts';

const read = (rel) => readFileSync(new URL(rel, import.meta.url), 'utf8');
const schema = JSON.parse(read('../../../../../src/motor_ai_sim/development/schema.json'));
const v1 = JSON.parse(read('../../../../../src/motor_ai_sim/development/schema_v1.json'));
const en = JSON.parse(read('../../../locales/en/development.json'));
const zh = JSON.parse(read('../../../locales/zh-CN/development.json'));
const field = (id) => schema.sections.flatMap((s) => s.fields ?? []).find((f) => f.id === id);
const steps = buildWizardSteps(schema);
const step = (id, list = steps) => list.find((s) => s.id === id);
const filled = (value) => ({ status: 'filled', value });
const draft = (fields = {}, extra = {}) => ({ schema_version: 2, fields, mission_profile: [], production: {}, ...extra });
const get = (obj, path) => path.split('.').reduce((o, k) => o?.[k], obj);
const names = (key) => en.wizard.op.default[key];
const tr = (key, args = {}) => { const v = get(en, key); return typeof v === 'string' ? v.replace(/\{(\w+)\}/g, (_, k) => String(args[k] ?? '')) : key; };
const wizardDir = new URL('../components/specWizard/', import.meta.url);
const wizardSource = (name) => readFileSync(new URL(name, wizardDir), 'utf8');

test('F01: the generator adds one new optional field 2_13; no existing field is relabelled or converted', () => {
  const g = buildWizardSteps(schema, 'generator'), m = buildWizardSteps(schema, 'aviation');
  const gs = step('supply', g), ms = step('supply', m);
  assert.equal(gs.title, 'wizard.steps.supply.titleGenerator'); assert.equal(ms.title, 'wizard.steps.supply.title');
  assert.deepEqual(gs.fields.map((f) => f.id).filter((id) => id !== '2_13'), ms.fields.map((f) => f.id));
  assert.ok(gs.fields.some((f) => f.id === '2_13') && !ms.fields.some((f) => f.id === '2_13'));
  assert.deepEqual(PROFILE_FIELDS.generator, { supply: ['2_13'] });
  assert.equal(field('2_13').required, false);
  assert.deepEqual(field('2_13').properties.map((p) => p.id), ['ac_min_V', 'ac_max_V', 'basis', 'at_rpm_min', 'at_rpm_max']);
  assert.deepEqual(field('2_13').properties.find((p) => p.id === 'basis').options.map((o) => o.value), ['line_line_rms', 'line_line_peak', 'phase_rms']);
  assert.equal(W.wizardCompleteness(g, draft()).total_required, W.wizardCompleteness(m, draft()).total_required);   // never required
  assert.equal(buildWizardSteps(v1).flatMap((s) => s.fields).some((f) => f.id === '2_13'), false);                  // v1 untouched
  // DC wording in every profile; the generator derived value is the shaft input magnitude, not an electrical output
  assert.match(en.wizard.op.voltage, /DC/); assert.equal(en.wizard.op.voltageGenerator, undefined); assert.equal(en.wizard.gen, undefined); assert.equal(zh.wizard.gen, undefined);
  assert.match(en.wizard.op.derivedGenerator, /\|T·ω\|/); assert.match(en.wizard.op.derivedGenerator, /[Ss]haft/);
  for (const k of ['wizard.steps.supply.titleGenerator', 'wizard.steps.supply.summaryGenerator', 'wizard.op.derivedGenerator', 'wizard.op.torqueGenerator', 'wizard.conflict.line']) assert.ok(get(en, k) && get(zh, k), k);
  for (const profile of ['generator', 'aviation']) {
    const d = draft({ '2_1': filled({ voltage_min_V: 400, voltage_nominal_V: 480, voltage_max_V: 520 }), '2_13': filled({ ac_min_V: 300, ac_max_V: 480, basis: 'line_line_rms', at_rpm_min: 600 }) });
    const items = reviewItems(step('supply', buildWizardSteps(schema, profile)), d, { lang: 'en', t: tr, profile });
    assert.equal(items.find((i) => i.id === '2_1').label, 'Battery and voltage');
    assert.equal(items.some((i) => i.id === '2_13'), profile === 'generator');
  }
  const gen = reviewItems(step('supply', g), draft({ '2_13': filled({ ac_min_V: 300, ac_max_V: 480, basis: 'line_line_rms', at_rpm_min: 600 }) }), { lang: 'en', t: tr, profile: 'generator' });
  assert.match(gen.find((i) => i.id === '2_13').value, /Minimum AC voltage 300 V/); assert.match(gen.find((i) => i.id === '2_13').value, /Line-to-line, RMS/);
  const ids = STEP_CARDS.supply.flatMap((c) => c.ids); assert.ok(ids.includes('2_13'));
});

test('F02: the maximum speed is always reachable - a card beside the key points, else its own schema-driven field (v1 has none)', () => {
  const ids = (sc) => step('operating', buildWizardSteps(sc)).fields.map((f) => f.id);
  assert.equal(maxSpeedPlacement(ids(schema)), 'card');
  assert.ok(ids(v1).includes('1_6') && !ids(v1).includes('key_points'));
  assert.equal(maxSpeedPlacement(ids(v1)), 'field');
  assert.equal(maxSpeedPlacement(['1_8']), null);
  assert.ok(stepIndexForField(buildWizardSteps(v1), '1_6') >= 0);
  const v1draft = { schema_version: 1, fields: {}, mission_profile: [], production: {} };
  assert.ok(stepProgress(step('operating', buildWizardSteps(v1)), v1draft).required >= 1);
  const src = wizardSource('OperatingStep.tsx');
  assert.match(src, /placement === 'field' && <StepCard cardColumn=\{cardColumnMin\(\[maxField\]\)\}>\{fields\.renderField\(maxField\)\}/);
  assert.match(src, /placement === 'card' && renderMaxSpeed\(\)/);
});

test('F03: marking a mission unknown locks the table, never the control that takes the mark back', () => {
  const on = { canEdit: true, busy: false, migrationBusy: false };
  assert.deepEqual(missionLocks({ ...on, unknown: false }), { inputs: false, toggle: false });
  assert.deepEqual(missionLocks({ ...on, unknown: true }), { inputs: true, toggle: false });
  assert.deepEqual(missionLocks({ ...on, unknown: true, busy: true }), { inputs: true, toggle: true });
  assert.deepEqual(missionLocks({ ...on, unknown: false, migrationBusy: true }), { inputs: true, toggle: true });
  assert.deepEqual(missionLocks({ ...on, canEdit: false, unknown: true }), { inputs: true, toggle: true });
  const src = wizardSource('SpecInputs.tsx');
  assert.match(src, /<Checkbox size="small" disabled=\{disabled\} checked=/);
  assert.match(src, /disabled=\{disabled \|\| unknown\}/);
  assert.match(wizardSource('useSpecFields.tsx'), /<MissionProfile draft=\{d\} disabled=\{locks\.toggle\}/);
});

test('F04: profile precedence - mode, then load, then bounded application text in English and Chinese', () => {
  const app = (text, extra = {}) => draft({ '1_1': filled(text), ...extra });
  for (const text of ['Window actuator', 'Winding machine', 'Hydrostatic drive', 'Marine propeller drive', 'Propeller test stand', 'Wheel hub motor']) assert.equal(wizardProfile(app(text)), 'industrial', text);
  assert.equal(wizardProfile(app('Drone propulsion')), 'aviation'); assert.equal(wizardProfile(app('Unmanned aerial vehicle')), 'aviation');
  assert.equal(wizardProfile(app('Marine drone thruster')), 'industrial');
  assert.equal(wizardProfile(app('无人机电机')), 'aviation'); assert.equal(wizardProfile(app('小型发电机')), 'generator');
  assert.equal(wizardProfile(app('风力发电机')), 'generator'); assert.equal(wizardProfile(app('船用推进电机')), 'industrial'); assert.equal(wizardProfile(app('电机')), 'industrial');
  assert.equal(wizardProfile(app('Drone propulsion', { '1_2': filled('Generator') })), 'generator');
  assert.equal(wizardProfile(app('Wind generator', { '1_2': filled('Motor, one direction') })), 'industrial');
  assert.equal(wizardProfile(app('Wind generator', { load_type: filled('marine_propeller') })), 'industrial');
  assert.equal(wizardProfile(draft({ '1_2': filled('Motor, both directions'), load_type: filled('turbine') })), 'industrial');
  assert.equal(wizardProfile(draft({ '1_2': filled('Motor, one direction'), load_type: filled('air_propeller') })), 'aviation');
  assert.equal(wizardProfile(app('Industrial drive', { load_type: filled('engine_driven_generator') })), 'generator');
  assert.equal(wizardProfile(app('Robot joint', { load_type: filled('air_propeller') })), 'aviation');
  assert.equal(wizardProfile(app('Wind / water turbine generator')), 'generator');
  assert.equal(wizardProfile(draft({ '1_1': { status: 'not_known_yet', value: 'Wind generator' } })), 'industrial');
});

test('F05: defaults are recognised by generated row id AND unchanged content; arbitrary ids are the user\'s own', () => {
  const o = (d, profile) => operatingRows(d, { legacy: { continuous: 'Continuous (S1)', peak: 'Peak (S2)' }, profile, nameOf: names });
  const custom = [{ ...blankKeyPoint('Continuous (S1)'), duration_s: CONTINUOUS }, { ...blankKeyPoint('Peak (S2)') }];
  assert.equal(isUntouchedDefaults(custom, names), false);
  assert.deepEqual(o(withKeyPoints(draft(), custom), 'generator').map((r) => [r.row_id, r.name]), custom.map((r) => [r.row_id, r.name]));
  const generated = profileDefaultRows('industrial', names);
  assert.ok(generated.every((r) => r.row_id.startsWith('wizard-default-')) && generated[0].row_id === defaultRowId('industrial', 's1'));
  assert.equal(isUntouchedDefaults(generated, names), true);
  assert.equal(isUntouchedDefaults([generated[0], { ...generated[1], row_id: 'abc' }], names), false);
  const legacy = ['Takeoff', 'Cruise', 'Landing', 'Peak'].map((name, i) => ({ ...blankKeyPoint(name), row_id: `default-${i}` }));
  assert.equal(isUntouchedDefaults(legacy, names), true);
  assert.deepEqual(o(withKeyPoints(draft(), legacy), 'generator').map((r) => r.name), ['Cut-in', 'Rated (continuous)', 'Peak']);
  const zhName = (k) => zh.wizard.op.default[k], enName = (k) => en.wizard.op.default[k];
  assert.equal(isUntouchedDefaults(profileDefaultRows('aviation', zhName), enName), true);
  assert.equal(isUntouchedDefaults(profileDefaultRows('aviation', enName), zhName), true);
  for (const [key, list] of Object.entries(DEFAULT_NAMES)) { assert.ok(list.includes(en.wizard.op.default[key]), key); assert.ok(list.includes(zh.wizard.op.default[key]), key); }
});

test('F06: a delivery-plan conflict is reported with both values; an agreeing plan is not', () => {
  const t = (k, a = {}) => k === 'wizard.conflict.line' ? `${a.label}: ${a.requirement} | ${a.plan}` : k;
  const conflicting = draft({ '6_1': filled({ prototypes: 1 }) }, { production: { quantities: filled({ prototypes: 10 }) } });
  assert.deepEqual(deliveryConflictLines(steps, conflicting, 'en', t), ['Prototypes: 1 | 10']);
  const dates = draft({ '6_2': filled({ simulation_date: '2026-11-01' }) }, { production: { timeline: filled({ simulation_date: '2026-12-01' }) } });
  assert.equal(deliveryConflictLines(steps, dates, 'en', t).length, 1);
  assert.deepEqual(deliveryConflictLines(steps, draft({ '6_1': filled({ prototypes: 3 }) }, { production: { quantities: filled({ prototypes: 3 }) } }), 'en', t), []);
  assert.deepEqual(deliveryConflictLines(steps, draft(), 'en', t), []);
  assert.match(get(en, 'wizard.conflict.line'), /\{label\}.*\{requirement\}.*\{plan\}/);
  assert.equal((wizardSource('StepBody.tsx').match(/<DeliveryConflict env=\{env\} \/>/g) ?? []).length, 1);
  assert.equal((wizardSource('ReviewStep.tsx').match(/<DeliveryConflict env=\{env\} \/>/g) ?? []).length, 1);
});

test('F07: the review shows only active requirements - air, liquid and underwater switches hide, never delete, inactive values', () => {
  const cooling = (value) => draft({ '3_2': filled({ temperature_max_C: 45 }), '3_6': filled(10), '3_1': filled(value) });
  const items = (d) => reviewItems(step('cooling'), d, { lang: 'en', t: tr });
  const ids = (d) => items(d).map((i) => i.id);
  const text = (d) => items(d).find((i) => i.id === '3_1').value;
  const air = cooling({ cooling_medium: 'air', airflow: { mode: 'Still air' }, inlet_temperature_C: 55, water_temperature_C: 12 });
  assert.ok(ids(air).includes('3_2') && !ids(air).includes('3_6'));
  assert.match(text(air), /Still air/); assert.ok(!/55|12/.test(text(air)));
  const liquid = cooling({ cooling_medium: 'liquid', coolant: 'Glycol', inlet_temperature_C: 55, airflow: { mode: 'Fan' } });
  assert.ok(!ids(liquid).includes('3_2') && !ids(liquid).includes('3_6'));
  assert.match(text(liquid), /55/); assert.match(text(liquid), /Glycol/);
  const sub = cooling({ cooling_medium: 'submerged', water_temperature_C: 12, inlet_temperature_C: 55 });
  assert.ok(ids(sub).includes('3_6') && !ids(sub).includes('3_2'));
  assert.match(text(sub), /12/); assert.ok(!/55/.test(text(sub)));
  assert.equal(sub.fields['3_2'].value.temperature_max_C, 45);
});

test('F08: the assistant is mounted once at a stable position (resizing keeps its unsent text)', () => {
  const src = wizardSource('SpecWizard.tsx');
  assert.equal((src.match(/p\.assistant\(/g) ?? []).length, 1);
  assert.doesNotMatch(src, /docked && p\.assistant|!docked && p\.assistant/);
});

test('F09: step summaries are tooltips, not grey captions; the unset-cooling line is gone', () => {
  for (const f of readdirSync(wizardDir).filter((n) => n.endsWith('.tsx'))) {
    const src = wizardSource(f);
    for (const m of src.matchAll(/(.{0,12})(?:step|current)\.summary\)/g)) assert.match(m[1], /title=\{tr\($/, `${f}: a step summary is shown only as a tooltip title`);
    assert.doesNotMatch(src, /wizard\.loadNone/);
  }
  assert.equal(en.wizard.loadNone, undefined); assert.equal(zh.wizard.loadNone, undefined);
  assert.match(wizardSource('StepNav.tsx'), /<Tooltip key=\{step\.id\} title=\{tr\(step\.summary\)\}/);
});

test('F10: keyboard focus follows the step heading unless a field reveal takes it', () => {
  const wizard = wizardSource('SpecWizard.tsx');
  assert.match(wizard, /if \(!pending\.current\) document\.getElementById\(`spec-card-/);
  assert.match(wizard, /querySelector<HTMLElement>\('h1'\)\?\.focus\(/); assert.match(wizard, /tabIndex=\{-1\}/);
  assert.match(wizardSource('ReviewStep.tsx'), /tabIndex=\{-1\}/);
});

test('F11: extreme, subnormal and invalid values never reach the SVG as NaN or Infinity', () => {
  const finite = (m) => [...m.xTicks.map((t) => t.x), ...m.yTicks.map((t) => t.y), ...m.points.flatMap((p) => [p.x, p.y]), ...m.mission.flatMap((p) => [p.x, p.y]), m.maxSpeed?.x ?? 0].every(Number.isFinite);
  const row = (torque, speed) => ({ ...blankKeyPoint('x'), row_id: 'x', torque_Nm: torque, speed_rpm: speed, duration_s: CONTINUOUS });
  for (const [t, n] of [[1.79e308, 1.79e308], [5e-324, 5e-324], [1e-320, 100], [1e12, 1e12], [100, -300], [-1.79e308, 50]]) {
    const m = buildChartModel(chartPoints([row(t, n)]), t === 1.79e308 ? 1.79e308 : null);
    assert.equal(finite(m), true, `${t}/${n}`); assert.ok(m.xTicks.length >= 2 && m.yTicks.length >= 2);
  }
  assert.equal(chartPoints([row(100, -300)]).length, 0);
  assert.equal(chartPoints([row(1e13, 100)]).length, 0); assert.equal(chartPoints([row(100, 1e12)]).length, 1);
  assert.equal(shaftPowerOf(1e308, 1e308), null); assert.equal(shaftPowerOf(1e300, 1e300), null);
  assert.equal(chartMaxSpeed(draft({ '1_6': filled({ maximum_rpm: 1.79e308 }) })), null);
  assert.equal(chartMaxSpeed(draft({ '1_6': filled({ maximum_rpm: 1200 }) })), 1200);
  assert.equal(chartMaxSpeed(draft({ '1_6': filled({ maximum_rpm: -5 }) })), null);
  assert.deepEqual(niceTicks(0, 5e-324), [0, 1]); assert.deepEqual(niceTicks(0, Infinity), [0, 1]); assert.deepEqual(niceTicks(NaN, 1), [0, 1]);
  assert.ok(PLOT_LIMIT > 1e9);
});

test('F12: every section-comment reference of the former cards still has a step', () => {
  const oldRefs = buildSpecificationCards(schema).flatMap((c) => [c.id, ...c.legacy_ids].map((id) => `section:${id}`));
  const newRefs = new Set(steps.flatMap((s) => stepCommentRefs(s)));
  assert.deepEqual(oldRefs.filter((r) => !newRefs.has(r)), []);
  assert.ok(stepCommentRefs(step('project')).includes('section:more'));
});

test('F13: the remembered step is a stable id; an old card index lands on the closest step; unknown falls back to the first', () => {
  const ids = steps.map((x) => x.id);
  assert.deepEqual(restorableView({ project: 'a', view: 'specification', step: 7, stepId: 'review' }, ids, true), { view: 'specification', step: 7 });
  assert.deepEqual(restorableView({ project: 'a', view: 'specification', step: 0, stepId: 'cooling' }, ids, true), { view: 'specification', step: 3 });
  const old = (n) => restorableView({ project: 'a', view: 'specification', step: n }, ids, true).step;
  assert.deepEqual([0, 1, 2, 3, 4].map(old), LEGACY_CARD_STEPS.map((id) => ids.indexOf(id)));
  assert.equal(old(4), 7);
  assert.equal(old(9), 0);
  assert.equal(restorableView({ project: 'a', view: 'specification', step: 2, stepId: 'gone' }, ids, true).step, 0);
  assert.equal(restorableView({ project: 'a', view: 'notes', step: 0 }, ids, false).view, 'specification');
  assert.deepEqual(parseDevelopmentView({ project: 'a', view: 'motors', step: 2, stepId: 'supply' }), { project: 'a', view: 'motors', step: 2, stepId: 'supply' });
  assert.deepEqual(parseDevelopmentView({ project: 'a', view: 'motors', step: 2, stepId: 'Not Valid!' }), { project: 'a', view: 'motors', step: 2 });
  assert.equal(parseDevelopmentView({ project: 'a', view: 'motors' }), null);
  // the optional groups are remembered through the same view memory, validated and guarded
  const wizard = wizardSource('SpecWizard.tsx');
  assert.match(wizard, /readView\(p\.memoryScope/); assert.match(wizard, /writeView\(p\.memoryScope, next\)/);
  assert.match(read('../components/DevelopmentPage.tsx'), /stepId: sections\[step\]\?\.id/);
});
