// node --test: the specification wizard's pure logic (owner 2026-10-27) - the step map, per-step progress, derived values and
// chart data of the operating points, the project profile (aviation / industrial / generator) and the review summary.
// All values are made up.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { buildWizardSteps, PROFILE_FIELDS, STEP_DEFS, STEP_CARDS, NOT_ASKED, stepIndexForField, stepCommentRefs, stepProgress, stillOpen, fieldState, overallProgress, answerPresent, isOptionalField } from '../components/specWizard/steps.ts';
import { buildChartModel, chartPoints, DURATION_PRESETS, durationMode, formatNumber, formatPower, missionChartPoints, niceTicks, operatingRows, pointRoles, shaftPowerOf } from '../components/specWizard/operatingPoints.ts';
import { hiddenAirflowModes, isUntouchedDefaults, profileDefaultRows, PROFILE_DEFAULTS, wizardNaturalAirflow, wizardProfile } from '../components/specWizard/profile.ts';
import { formatValue, reviewItems } from '../components/specWizard/reviewSummary.ts';
import { CONTINUOUS, withKeyPoints, blankKeyPoint } from '../keyPoints.ts';
import { buildSpecificationCards, RETIRED_FIELDS } from '../specificationLayout.ts';
import * as W from '../wizardState.ts';

const read = (rel) => readFileSync(new URL(rel, import.meta.url), 'utf8');
const schema = JSON.parse(read('../../../../../src/motor_ai_sim/development/schema.json'));
const en = JSON.parse(read('../../../locales/en/development.json'));
const zh = JSON.parse(read('../../../locales/zh-CN/development.json'));
const allFields = schema.sections.flatMap((s) => s.fields ?? []);
const field = (id) => allFields.find((f) => f.id === id);
const steps = buildWizardSteps(schema);
const step = (id) => steps.find((s) => s.id === id);
const filled = (value) => ({ status: 'filled', value });
const draft = (fields = {}, extra = {}) => ({ schema_version: 2, fields, mission_profile: [], production: {}, ...extra });
const get = (obj, path) => path.split('.').reduce((o, k) => o?.[k], obj);
const names = (key) => en.wizard.op.default[key];            // the English default names
const tr = (key, args = {}) => { const v = get(en, key); return typeof v === 'string' ? v.replace(/\{(\w+)\}/g, (_, k) => String(args[k] ?? '')) : key; };

// ── step map ────────────────────────────────────────────────────────────────────────────────────────────────────────
test('seven steps in the owner order, then the review', () => {
  assert.deepEqual(steps.map((s) => s.id), ['application', 'operating', 'supply', 'cooling', 'mechanical', 'safety', 'project', 'review']);
  assert.equal(steps[7].kind, 'review'); assert.deepEqual(steps[7].fields, []);
  for (const s of steps) { assert.ok(get(en, s.title) && get(zh, s.title), s.title); assert.ok(get(en, s.summary) && get(zh, s.summary), s.summary); }
});

test('a field sits in at most one step; every required field is asked exactly once; retired and not-asked fields are not', () => {
  const ids = steps.flatMap((s) => s.fields.map((f) => f.id));
  assert.equal(new Set(ids).size, ids.length);
  const required = allFields.filter((f) => f.required || f.required_flag === 'E').map((f) => f.id);
  for (const id of required) assert.equal(ids.filter((x) => x === id).length, 1, id);
  for (const id of [...RETIRED_FIELDS, ...NOT_ASKED]) assert.ok(!ids.includes(id), id);
  // a required field is never hidden in the optional group
  for (const id of required) assert.ok(!isOptionalField(steps, id), id);
  // every id the map names exists in the schema
  for (const def of STEP_DEFS) for (const id of [...def.core, ...def.optional]) assert.ok(field(id), id);
});

test('the cards of a step group exactly its core fields, in order (a profile field has its own card)', () => {
  for (const def of STEP_DEFS) {
    const ids = STEP_CARDS[def.id].flatMap((c) => c.ids);
    const extras = Object.values(PROFILE_FIELDS).flatMap((p) => p[def.id] ?? []);
    assert.deepEqual(ids.filter((id) => !extras.includes(id)), def.core, def.id);
    assert.deepEqual(extras.filter((id) => !ids.includes(id)), [], def.id);
  }
  for (const def of STEP_DEFS) for (const card of STEP_CARDS[def.id]) if (card.ids.length > 1 && !card.untitled) assert.ok(en.wizard.card[card.id] && zh.wizard.card[card.id], card.id);
});

test('field to step: the schema sections map onto the seven steps', () => {
  const where = (id) => steps[stepIndexForField(steps, id)]?.id;
  assert.deepEqual(['1_1', 'load_type', '1_2'].map(where), ['application', 'application', 'application']);
  assert.deepEqual(['key_points', '1_6', '1_8', 'mission_profile', '1_7'].map(where), Array(5).fill('operating'));
  assert.deepEqual(['2_1', '2_4', '2_7'].map(where), Array(3).fill('supply'));
  // the phase current limits and the phase count are not asked from the customer (owner 2026-10-27); staff still see them, collapsed
  assert.deepEqual(['2_6', '2_9'].map(where), [undefined, undefined]);
  const staffSteps = buildWizardSteps(schema, undefined, { staff: true });
  assert.deepEqual(['2_6', '2_9'].map((id) => staffSteps[stepIndexForField(staffSteps, id)]?.id), ['supply', 'supply']);
  assert.ok(isOptionalField(staffSteps, '2_6') && isOptionalField(staffSteps, '2_9'));
  assert.deepEqual(['3_1', '3_2', '3_3', '3_6', '3_4', '3_5'].map(where), Array(6).fill('cooling'));
  assert.deepEqual(['4_1', '4_2', '4_3', '4_4', '4_8', '4_6', '4_5', '4_7', '4_9'].map(where), Array(9).fill('mechanical'));
  assert.deepEqual(['5_1', '5_2', '5_3', '5_4', '1_9', '1_10', 'tests', 'certification'].map(where), Array(8).fill('safety'));
  assert.deepEqual(['6_1', '6_2', 'additional_requirements', '6_4', 'packaging_delivery', 'controller_supplier', 'magnets_supplier', 'housing_supplier'].map(where), Array(8).fill('project'));
  assert.equal(where('2_3'), undefined); assert.equal(where('target_price'), undefined);
  // production fields keep their storage namespace
  assert.equal(step('safety').fields.find((f) => f.id === 'tests').storage_namespace, 'production');
  assert.equal(step('project').fields.find((f) => f.id === '6_1').storage_namespace, 'fields');
});

test('completeness over the steps equals the completeness over the former cards (nothing silently dropped)', () => {
  const cards = buildSpecificationCards(schema);
  const drafts = [
    draft(), draft({ '1_1': filled('Drone propulsion'), '1_6': filled({ maximum_rpm: 9000 }), '3_2': filled({ temperature_max_C: 45 }) }),
    draft({ '3_1': filled({ cooling_medium: 'liquid', inlet_temperature_C: 50 }), '4_1': filled({ outer_diameter_mm: 200, length_mm: 50 }) }),
  ];
  for (const d of drafts) {
    const a = W.wizardCompleteness(cards, d), b = W.wizardCompleteness(steps, d);
    assert.deepEqual([b.total_required, b.answered, b.unknown, b.missing, b.invalid], [a.total_required, a.answered, a.unknown, a.missing, a.invalid]);
    assert.deepEqual(W.requiredFieldIds(steps, d).sort(), W.requiredFieldIds(cards, d).sort());
  }
});

test('section comments written against the earlier card ids still find their step', () => {
  const refs = Object.fromEntries(steps.map((s) => [s.id, stepCommentRefs(s)]));
  for (const [legacy, now] of [['application', 'application'], ['key', 'application'], ['continuous', 'operating'], ['mission', 'operating'], ['electrical', 'supply'], ['cooling', 'cooling'],
    ['mechanical', 'mechanical'], ['construction', 'mechanical'], ['load', 'mechanical'], ['delivery', 'project'], ['requirements', 'project'], ['review', 'review']]) assert.ok(refs[now].includes(`section:${legacy}`), legacy);
  const all = Object.values(refs).flat(); assert.equal(new Set(all).size, all.length);   // each ref belongs to one step
});

// ── progress ────────────────────────────────────────────────────────────────────────────────────────────────────────
test('step states: empty, partial, done; a step without required fields is optional until something is entered', () => {
  assert.equal(stepProgress(step('application'), draft()).state, 'empty');
  const half = draft({ '1_1': filled('Robot joint') });
  assert.deepEqual(['partial', 1, 2], [stepProgress(step('application'), half).state, stepProgress(step('application'), half).answered, stepProgress(step('application'), half).required]);
  const done = draft({ '1_1': filled('Robot joint'), '1_2': filled('Motor, one direction') });
  assert.equal(stepProgress(step('application'), done).state, 'done');
  assert.equal(stepProgress(step('safety'), draft()).state, 'optional');
  assert.equal(stepProgress(step('safety'), draft({ '5_1': filled('IEC 60034') })).state, 'done');
  // "answer later" is not done
  const later = draft({ '1_1': filled('Robot joint'), '1_2': { status: 'not_known_yet' } });
  assert.equal(stepProgress(step('application'), later).state, 'partial');
  // prototypes default to 1: the project step starts partly answered
  assert.equal(stepProgress(step('project'), draft({}, {})).answered, 1);
});

test('still open lists missing, invalid and answer-later fields, optional ones only when marked', () => {
  const d = draft({ '1_1': filled('Robot joint'), '1_2': { status: 'not_known_yet' }, '5_1': { status: 'not_known_yet' } });
  const open = stillOpen(steps, d);
  const find = (id) => open.find((o) => o.fieldId === id);
  assert.equal(find('1_2').state, 'unknown'); assert.equal(find('5_1').state, 'unknown'); assert.equal(find('1_1'), undefined);
  assert.equal(find('2_4').state, 'missing');
  assert.equal(find('5_2'), undefined);                                   // an empty optional field is not open
  assert.equal(find('3_6'), undefined);                                   // the depth applies to underwater cooling only
  assert.equal(fieldState(field('1_1'), d), 'answered');
});

test('overall progress is answered of required, as a whole percentage', () => {
  assert.deepEqual(overallProgress({ answered: 7, total_required: 9 }), { answered: 7, total: 9, pct: 78 });
  assert.deepEqual(overallProgress({ answered: 3, required: 0 }), { answered: 0, total: 0, pct: 0 });
  assert.deepEqual(overallProgress(undefined), { answered: 0, total: 0, pct: 0 });
  assert.equal(answerPresent({ status: 'filled', value: { a: '', b: [] } }), false);
  assert.equal(answerPresent({ status: 'filled', value: { a: 0 } }), true);
});

// ── derived values and the chart ────────────────────────────────────────────────────────────────────────────────────
test('shaft power is T x omega; the display scales W, kW, MW', () => {
  assert.ok(Math.abs(shaftPowerOf(1000, 850) - 1000 * 2 * Math.PI * 850 / 60) < 1e-6);
  assert.equal(formatPower(shaftPowerOf(1000, 850)), '89 kW');
  assert.equal(formatPower(850), '850 W'); assert.equal(formatPower(1.25e6), '1.25 MW'); assert.equal(formatPower(1500), '1.5 kW');
  assert.equal(shaftPowerOf(null, 850), null); assert.equal(shaftPowerOf(100, ''), null);
  assert.equal(shaftPowerOf(-100, 600), shaftPowerOf(100, 600));          // a generator point shows the magnitude
  assert.equal(formatNumber(0.5123), '0.512'); assert.equal(formatNumber(1234.6), '1235'); assert.equal(formatNumber(NaN), '');
});

test('duration chips: continuous, 10 / 30 / 60 s presets, other (a free number or an opened empty input)', () => {
  assert.deepEqual([...DURATION_PRESETS], [10, 30, 60]);
  assert.equal(durationMode(CONTINUOUS), 'continuous'); assert.equal(durationMode(30), 'preset'); assert.equal(durationMode(45), 'other');
  assert.equal(durationMode(null), 'unset'); assert.equal(durationMode(null, true), 'other'); assert.equal(durationMode(undefined), 'unset');
});

test('chart points: only rows with a torque and a speed; roles follow the system rule; max speed and mission points', () => {
  const rows = [
    { ...blankKeyPoint('Cont'), row_id: 'a', torque_Nm: 1000, speed_rpm: 850, duration_s: CONTINUOUS },
    { ...blankKeyPoint('Peak'), row_id: 'b', torque_Nm: 1500, speed_rpm: 1000, duration_s: 60 },
    { ...blankKeyPoint('Half'), row_id: 'c', torque_Nm: 20 },
  ];
  const pts = chartPoints(rows);
  assert.deepEqual(pts.map((p) => [p.id, p.role]), [['a', 'continuous'], ['b', 'peak']]);
  assert.deepEqual([...pointRoles(rows)], [['a', 'continuous'], ['b', 'peak'], ['c', null]]);
  const model = buildChartModel(pts, 1200, missionChartPoints(draft({}, { mission_profile: [{ speed_rpm: 500, torque_Nm: 300 }, { speed_rpm: 'x' }] })));
  assert.equal(model.mission.length, 1);
  assert.ok(model.xMax >= 1200 && model.yMax >= 1500 && model.yMin === 0);
  assert.ok(model.maxSpeed.x > model.points[1].x);                           // 1200 rpm is right of the 1000 rpm point
  assert.ok(model.points[1].y < model.points[0].y);                          // more torque is higher on the screen
  for (const p of model.points) assert.ok(p.x >= model.plot.left && p.x <= model.plot.right && p.y >= model.plot.top && p.y <= model.plot.bottom);
  const empty = buildChartModel([], null);
  assert.equal(empty.maxSpeed, null); assert.ok(empty.xTicks.length >= 2 && empty.yTicks.length >= 2);
  const neg = buildChartModel([{ id: 'g', name: 'g', torque: -200, speed: 300, duration: CONTINUOUS, role: 'continuous' }], null);
  assert.ok(neg.yMin < 0);
  assert.deepEqual(niceTicks(0, 1080), [0, 250, 500, 750, 1000, 1250]);          // the last tick covers the largest value
  assert.deepEqual(niceTicks(-220, 1700).at(-1) >= 1700, true);
  assert.deepEqual(niceTicks(0, 0), [0, 1]);
});

// ── project profile ─────────────────────────────────────────────────────────────────────────────────────────────────
test('profile: generator by mode, load or application; aviation by application or propeller; otherwise industrial', () => {
  assert.equal(wizardProfile(draft()), 'industrial');
  assert.equal(wizardProfile(draft({ '1_1': filled('eVTOL / aircraft propulsion') })), 'aviation');
  assert.equal(wizardProfile(draft({ '1_1': filled('Drone propulsion') })), 'aviation');
  assert.equal(wizardProfile(draft({ load_type: filled('air_propeller') })), 'aviation');
  assert.equal(wizardProfile(draft({ '1_1': filled('Robot joint') })), 'industrial');
  assert.equal(wizardProfile(draft({ '1_1': filled('Marine propulsion') })), 'industrial');
  assert.equal(wizardProfile(draft({ '1_1': filled('Wind / water turbine generator') })), 'generator');
  assert.equal(wizardProfile(draft({ '1_1': filled('Industrial drive'), '1_2': filled('Generator, rotation set by the engine') })), 'generator');
  assert.equal(wizardProfile(draft({ load_type: filled('turbine') })), 'generator');
  assert.equal(wizardProfile(draft({ load_type: filled('engine_driven_generator') })), 'generator');
  // the generator beats a propeller word; an unknown answer says nothing
  assert.equal(wizardProfile(draft({ '1_1': filled('Wind generator with propeller blades') })), 'generator');
  assert.equal(wizardProfile(draft({ '1_1': { status: 'not_known_yet', value: 'Wind generator' } })), 'industrial');
});

test('default points by profile: aviation takeoff / cruise / landing / peak; industrial continuous + peak; generator cut-in / rated / peak', () => {
  const rowsFor = (p) => profileDefaultRows(p, names);
  assert.deepEqual(rowsFor('aviation').map((r) => r.name), ['Takeoff', 'Cruise', 'Landing', 'Peak']);
  assert.deepEqual(rowsFor('industrial').map((r) => [r.name, r.duration_s]), [['Continuous (S1)', CONTINUOUS], ['Peak (S2)', null]]);
  assert.deepEqual(rowsFor('generator').map((r) => [r.name, r.duration_s]), [['Cut-in', null], ['Rated (continuous)', CONTINUOUS], ['Peak', null]]);
  for (const p of Object.keys(PROFILE_DEFAULTS)) for (const d of PROFILE_DEFAULTS[p]) { assert.ok(en.wizard.op.default[d.key] && zh.wizard.op.default[d.key], d.key); }
});

test('defaults are offered only for an empty or untouched list; a typed name or number is never replaced', () => {
  const o = (d, profile) => operatingRows(d, { legacy: { continuous: 'Continuous (S1)', peak: 'Peak (S2)' }, profile, nameOf: names });
  // nothing stored: the profile's defaults
  assert.deepEqual(o(draft(), 'generator').map((r) => r.name), ['Cut-in', 'Rated (continuous)', 'Peak']);
  // stored but untouched defaults (the user opened a chip, nothing typed) follow a changed profile
  const untouched = withKeyPoints(draft(), profileDefaultRows('aviation', names));
  assert.deepEqual(o(untouched, 'generator').map((r) => r.name), ['Cut-in', 'Rated (continuous)', 'Peak']);
  assert.deepEqual(o(untouched, 'aviation').map((r) => r.name), ['Takeoff', 'Cruise', 'Landing', 'Peak']);
  // a number typed: the whole list is the user's
  const typed = profileDefaultRows('aviation', names); typed[1] = { ...typed[1], torque_Nm: 40 };
  assert.deepEqual(o(withKeyPoints(draft(), typed), 'generator').map((r) => r.name), ['Takeoff', 'Cruise', 'Landing', 'Peak']);
  // a name typed, a row removed, a row added, a duration chosen: all are the user's own
  const renamed = profileDefaultRows('industrial', names); renamed[0] = { ...renamed[0], name: 'Hover' };
  assert.equal(o(withKeyPoints(draft(), renamed), 'generator')[0].name, 'Hover');
  assert.equal(o(withKeyPoints(draft(), profileDefaultRows('aviation', names).slice(1)), 'generator').length, 3);
  assert.equal(o(withKeyPoints(draft(), [...profileDefaultRows('industrial', names), blankKeyPoint('Extra')]), 'generator').length, 3);
  const timed = profileDefaultRows('industrial', names); timed[1] = { ...timed[1], duration_s: 30 };
  assert.equal(isUntouchedDefaults(timed, names), false);
  // deleting every row leaves an empty list (the user's choice)
  assert.deepEqual(o(withKeyPoints(draft(), []), 'generator'), []);
  // an old project that stored only S1 / peak reads as those two points
  const legacy = draft({ '1_4': filled({ torque_Nm: 100, speed_rpm: 500 }), '1_5': filled({ torque_Nm: 150, speed_rpm: 400, duration_s: 30 }) });
  assert.deepEqual(o(legacy, 'generator').map((r) => r.torque_Nm), [100, 150]);
  // defaults stored in English still count as untouched when the page runs in Chinese (and the reverse for the page's own language)
  const zhName = (k) => zh.wizard.op.default[k];
  assert.equal(isUntouchedDefaults(profileDefaultRows('aviation', names), zhName), true);
  assert.equal(isUntouchedDefaults(profileDefaultRows('aviation', zhName), zhName), true);
});

test('airflow presets: a turbine or a water propeller hides propeller wash and ram air unless already stored', () => {
  assert.deepEqual(hiddenAirflowModes('turbine'), ['Propeller wash', 'Ram air']);
  assert.deepEqual(hiddenAirflowModes('marine_propeller'), ['Propeller wash', 'Ram air']);
  assert.deepEqual(hiddenAirflowModes('water_jet'), ['Propeller wash', 'Ram air']);      // a finer type sits under its button
  assert.deepEqual(hiddenAirflowModes('turbine', 'Ram air'), ['Propeller wash']);
  assert.deepEqual(hiddenAirflowModes('turbine', 'ram air'), ['Propeller wash']);
  assert.deepEqual(hiddenAirflowModes('air_propeller'), []); assert.deepEqual(hiddenAirflowModes('wheel'), []); assert.deepEqual(hiddenAirflowModes(undefined), []);
  assert.equal(wizardNaturalAirflow('turbine', 'Ram air'), 'Still air');                 // the shared matrix would bring ram air
  assert.equal(wizardNaturalAirflow('air_propeller', 'Propeller wash'), 'Propeller wash');
  assert.equal(wizardNaturalAirflow('turbine', null), null);
});

// ── review summary ──────────────────────────────────────────────────────────────────────────────────────────────────
test('review items: one line per usable point with torque, speed, duration and power; max speed; answer-later fields', () => {
  const rows = [{ ...blankKeyPoint('Rated'), row_id: 'r', torque_Nm: 1000, speed_rpm: 850, duration_s: CONTINUOUS }, { ...blankKeyPoint('Peak'), row_id: 'p', torque_Nm: 1500, speed_rpm: 1000, duration_s: 60 }, blankKeyPoint('Empty')];
  const d = withKeyPoints(draft({ '1_6': filled({ maximum_rpm: 1200 }), '1_8': { status: 'not_known_yet' } }), rows);
  const items = reviewItems(step('operating'), d, { lang: 'en', t: tr, profile: 'industrial' });
  const byId = Object.fromEntries(items.map((i) => [i.id, i]));
  assert.equal(byId['key_points:r'].value, '1000 N·m');
  assert.match(byId['key_points:r'].note, /850 rpm/); assert.match(byId['key_points:r'].note, /89 kW/); assert.match(byId['key_points:r'].note, /Continuous \(S1\)/);
  assert.match(byId['key_points:p'].note, /60 s/); assert.match(byId['key_points:p'].note, /Peak \(S2\)/);
  assert.equal(items.filter((i) => i.id.startsWith('key_points:')).length, 2);        // the empty row is skipped
  assert.equal(byId['1_6'].value, '1200 rpm');
  assert.equal(byId['1_8'].unknown, true);
});

test('review values: units, menu labels, objects, booleans, ranges as their maximum', () => {
  const t = (k) => ({ yes: 'Yes', no: 'No' })[k] ?? k;
  assert.equal(formatValue(field('4_1'), { outer_diameter_mm: 550, length_mm: 120 }, 'en', t), 'Maximum outer diameter 550 mm · Maximum length 120 mm');
  assert.equal(formatValue(field('3_2'), { temperature_max_C: 45 }, 'en', t), '45 °C');
  assert.equal(formatValue(field('1_2'), 'Generator', 'en', t), 'Generator');
  assert.equal(formatValue(field('1_2'), 'Generator', 'zh-CN', t), '发电机');
  assert.equal(formatValue({ type: 'boolean' }, true, 'en', t), 'Yes');
  assert.equal(formatValue(field('1_6'), { maximum_rpm: { minimum: 800, maximum: 1000 } }, 'en', t), '1000 rpm');
  assert.equal(formatValue(field('4_8'), { balance_grade: 'G2.5', runout_mm: 0.05 }, 'en', t), 'G2.5 · Runout 0.05 mm');
  assert.equal(formatValue(field('1_2'), '', 'en', t), '');
  assert.equal(formatValue(field('6_1'), { prototypes: 1 }, 'en', t), '1');                  // no "1 units"
  assert.equal(formatValue(field('3_1'), { cooling_medium: 'air', airflow: { mode: 'Propeller wash' } }, 'en', t), 'Air · Propeller wash');
});

// ── wording ─────────────────────────────────────────────────────────────────────────────────────────────────────────
test('professional wording, both languages: no consumer phrasing, every key the wizard uses exists', () => {
  const text = JSON.stringify(en.wizard);
  for (const bad of ['All the time', 'all the time', 'How hard', 'Short peak', 'Top speed', 'I\'ll answer']) assert.ok(!text.includes(bad), bad);
  assert.equal(en.wizard.op.role.continuous, 'Continuous (S1)'); assert.equal(en.wizard.op.role.peak, 'Peak (S2)');
  assert.equal(en.wizard.answerLater, 'Answer later');
  const dir = new URL('../components/specWizard/', import.meta.url);
  const used = new Set();
  for (const f of readdirSync(dir).filter((n) => /\.tsx?$/.test(n))) {
    const src = readFileSync(new URL(f, dir), 'utf8');
    for (const m of src.matchAll(/\b(?:tr|t)\(\s*'([^'$]+)'/g)) used.add(m[1]);
  }
  const missing = [...used].filter((k) => /^(wizard|keyPoints|migration|field)\./.test(k) || ['sectionReview', 'previous', 'next', 'saving', 'saveFailedShort', 'specReadOnly', 'specReadOnlyTip', 'validationError', 'requiredMissing', 'submitBlocked', 'retrySave', 'reloadDraft', 'saveFailed', 'openComment', 'yes', 'no'].includes(k))
    .filter((k) => !get(en, k) || !get(zh, k));
  assert.deepEqual(missing, []);
  // the dynamic keys
  for (const k of ['done', 'partial', 'empty', 'optional']) assert.ok(en.wizard.state[k] && zh.wizard.state[k], k);
  for (const k of ['unknown', 'missing', 'invalid']) assert.ok(en.wizard.review.state[k] && zh.wizard.review.state[k], k);
  for (const k of ['continuous', 'peak']) assert.ok(en.wizard.op.role[k] && zh.wizard.op.role[k], k);
});
