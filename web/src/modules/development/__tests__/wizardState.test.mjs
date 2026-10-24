// node --test: required fields, not-known-yet, eight-step completeness and version diffs.
import test from 'node:test';
import assert from 'node:assert/strict';

import { readFileSync } from 'node:fs';
import * as W from '../wizardState.ts';
import * as R from '../resultPresentation.ts';
import { buildSpecificationCards } from '../specificationLayout.ts';
import { canApplyMigrationPreview, migrationS1Candidate } from '../migrationPresentation.ts';
const t = test;

const sections = [
  { id: '1', fields: [{ id: 'application', required: true }, { id: 'optional', required: false }] },
  { id: 'mission_profile', fields: [{ id: 'mission_profile', required: true }] },
  { id: 'production', fields: [{ id: 'quantities', required: true }] },
];

t('draft answers distinguish missing values from explicitly not known yet', () => {
  let draft = { fields: {} };
  assert.equal(W.answerComplete(W.fieldAnswer(draft, 'application')), false);
  draft = W.setFieldAnswer(draft, 'application', { status: 'not_known_yet' });
  assert.equal(W.answerComplete(W.fieldAnswer(draft, 'application')), true);
  draft = W.setFieldAnswer(draft, 'application', { value: 'eVTOL', status: 'filled' });
  assert.equal(W.answerComplete(W.fieldAnswer(draft, 'application')), true);
});

t('completeness includes the mission profile and the production namespace', () => {
  const draft = { fields: { application: { value: 'eVTOL', status: 'filled' } }, mission_profile_status: 'not_known_yet', production: { quantities: { value: { prototypes: 2 }, status: 'filled' } } };
  const complete = W.wizardCompleteness(sections, draft);
  assert.equal(complete.filled, 3); assert.equal(complete.required, 3);
  assert.equal(complete.sections.production.filled, 1);
  assert.deepEqual(W.requiredFieldIds(sections, draft), []);
});

t('nested object answers remain incomplete until every value has been entered', () => {
  assert.equal(W.answerComplete({ value: { torque_Nm: 30, speed_rpm: '' } }), false);
  assert.equal(W.answerComplete({ value: { torque_Nm: 30, speed_rpm: 900 } }), true);
});

t('required object properties determine completeness while optional subfields do not', () => {
  const field = { id: '1_4', required: true, properties: [{ id: 'torque', required: true }, { id: 'note', required: false }] };
  assert.equal(W.fieldAnswerComplete(field, { value: { torque: 120, note: '' }, status: 'filled' }), true);
  assert.equal(W.fieldAnswerComplete(field, { value: { torque: '', note: 'optional' }, status: 'filled' }), false);
});

t('version diff reports changed nested leaves only', () => {
  assert.deepEqual(W.shallowDiff({ fields: { speed: { value: 800 } } }, { fields: { speed: { value: 900 } } }), [
    { path: 'fields.speed.value', before: 800, after: 900 },
  ]);
});


t('serialized autosaves advance revision and never write a stale project token', async () => {
  const writes = [];
  const coordinator = W.createDraftSaveCoordinator(async (project, draft, revision) => { writes.push({ project, draft, revision }); await Promise.resolve(); return { revision: revision + 1 }; });
  coordinator.setRevision('alpha', 4);
  let currentProject = 'alpha';
  const stillAlpha = () => currentProject === 'alpha';
  const first = coordinator.enqueue('alpha', { n: 1 }, stillAlpha);
  const second = coordinator.enqueue('alpha', { n: 2 }, stillAlpha);
  await Promise.all([first, second]);
  assert.deepEqual(writes.map((w) => w.revision), [4, 5]);
  assert.equal(coordinator.revision('alpha'), 6);
  currentProject = 'beta';
  await coordinator.enqueue('alpha', { n: 3 }, stillAlpha);
  assert.equal(writes.length, 2);
});

t('failed autosave prevents the submit callback from running', async () => {
  const coordinator = W.createDraftSaveCoordinator(async () => { throw new Error('conflict'); });
  coordinator.setRevision('alpha', 7);
  let submitted = false;
  await assert.rejects(coordinator.submit('alpha', {}, () => true, async () => { submitted = true; return { revision: 8 }; }), /conflict/);
  assert.equal(submitted, false);
  assert.equal(coordinator.revision('alpha'), 7);
  await coordinator.flush();
});

t('successful autosave and submit advance revisions for the next draft', async () => {
  const seen = [];
  const coordinator = W.createDraftSaveCoordinator(async (_project, _draft, revision) => { seen.push(revision); return { revision: revision + 1 }; });
  coordinator.setRevision('alpha', 3);
  const result = await coordinator.submit('alpha', { x: 1 }, () => true, async (revision) => { assert.equal(revision, 4); return { revision: 5 }; });
  assert.equal(result.revision, 5);
  assert.equal(coordinator.revision('alpha'), 5);
  await coordinator.enqueue('alpha', { x: 2 }, () => true);
  assert.deepEqual(seen, [3, 5]);
});

t('a draft queued during submit waits and saves with the revision returned by submit', async () => {
  const saves = [];
  const coordinator = W.createDraftSaveCoordinator(async (_project, draft, revision) => { saves.push({ draft, revision }); return { revision: revision + 1 }; });
  coordinator.setRevision('alpha', 1);
  let finishSubmit;
  const submit = coordinator.submit('alpha', { n: 'submitted' }, () => true, async (revision) => {
    assert.equal(revision, 2);
    await new Promise((resolve) => { finishSubmit = resolve; });
    return { revision: 3 };
  });
  await new Promise((resolve) => setImmediate(resolve));
  const later = coordinator.enqueue('alpha', { n: 'edit during submit' }, () => true);
  finishSubmit();
  await Promise.all([submit, later]);
  assert.deepEqual(saves, [{ draft: { n: 'submitted' }, revision: 1 }, { draft: { n: 'edit during submit' }, revision: 3 }]);
  assert.equal(coordinator.revision('alpha'), 4);
});

t('actual shared schema drives section completeness and mission row validation', () => {
  const schema = JSON.parse(readFileSync('../src/motor_ai_sim/development/schema.json', 'utf8'));
  const mission = { fields: {}, mission_profile_status: 'filled', mission_profile: [{ phase: 'cruise', torque_Nm: 50, speed_rpm: 800, duration_s: 300, battery_voltage_V: 700 }], production: {} };
  const counts = W.wizardCompleteness(schema.sections, mission);
  assert.equal(counts.sections['mission_profile'].filled, 1);
  assert.equal(counts.sections['production'].required, (schema.sections.find((s) => s.id === 'production').fields ?? []).filter((f) => f.required === true || f.required_flag === 'E').length);
  assert.equal(W.missionProfileComplete({ ...mission, mission_profile: [{ phase: 'cruise', torque_Nm: 50 }] }), false);
  const field = schema.sections.find((s) => s.id === '1').fields.find((f) => f.id === '1_4');
  assert.equal(W.fieldAnswerComplete(field, { status: 'filled', value: { torque_Nm: 50, speed_rpm: 900 } }), true);
});

t('stored result kinds and temperature categories render through localized labels', () => {
  const labels = {
    storedResult: 'Stored result', temperatures: 'Temperatures',
    'resultKind.thermal': 'Thermal', 'temperature.coil': 'Coil',
    'temperature.stator': 'Stator', 'temperature.rotor': 'Rotor',
    'temperature.housing': 'Housing', 'temperature.maximum': 'Maximum',
  };
  const translate = (key) => labels[key] ?? key;
  assert.equal(R.resultKindLabel('thermal', translate), 'Thermal');
  assert.equal(R.resultKindLabel('unknown', translate), 'Stored result');
  for (const [key, expected] of [['coil_temp_c', 'Coil'], ['stator_temp_c', 'Stator'], ['rotor_c', 'Rotor'], ['housing_temp_c', 'Housing'], ['t_max_c', 'Maximum']]) {
    assert.equal(R.temperatureLabel(R.temperatureKindForKey(key), translate), expected);
  }
});

t('schema card layout shows every field once and keeps production storage namespace', () => {
  const schema = JSON.parse(readFileSync('../src/motor_ai_sim/development/schema.json', 'utf8'));
  const cards = buildSpecificationCards(schema);
  assert.equal(cards.length, 9);
  const laidOut = cards.flatMap((card) => card.fields);
  const ids = laidOut.map((field) => field.id);
  assert.equal(new Set(ids).size, ids.length);
  assert.deepEqual([...ids].sort(), schema.sections.flatMap((s) => s.fields ?? []).map((f) => f.id).sort());
  assert.ok(laidOut.filter((f) => f.storage_namespace === 'production').every((f) => cards.find((c) => c.fields.includes(f))?.id === 'delivery'));
});

t('local validation rejects nonfinite values, bad ranges and enum values while honoring conditional requirements', () => {
  assert.equal(W.fieldValueValid({ id: 'n', type: 'number', min: 0 }, Number.NaN), false);
  assert.equal(W.fieldValueValid({ id: 'range', allow_range: true }, { minimum: '', maximum: 4 }), false);
  assert.equal(W.fieldValueValid({ id: 'range', allow_range: true }, { minimum: 5, maximum: 4 }), false);
  assert.equal(W.fieldValueValid({ id: 'mode', options: ['a', 'b'] }, 'c'), false);
  const loadSchema = { sections: [{ id: 'load', fields: [
    { id: 'load_source', required: true, options: ['catalog', 'manual', 'unknown'] },
    { id: 'load_characteristic', required_when: { field: 'load_source', in: ['catalog', 'manual'] } },
  ] }] };
  const counts = W.wizardCompleteness(loadSchema.sections, { fields: { load_source: { status: 'filled', value: 'manual' } } });
  assert.equal(counts.required, 2);
  assert.equal(counts.missing, 1);
});

t('mission values accept signed torque and speed but reject negative duration and voltage', () => {
  const draft = { mission_profile_status: 'filled', mission_profile: [{ phase: 'regen', torque_Nm: -12, speed_rpm: -900, duration_s: 10, battery_voltage_V: 48 }] };
  assert.equal(W.missionProfileComplete(draft), true);
  assert.equal(W.missionProfileComplete({ ...draft, mission_profile: [{ ...draft.mission_profile[0], torque_Nm: 0, speed_rpm: 0 }] }), true);
  assert.equal(W.missionProfileComplete({ ...draft, mission_profile: [{ ...draft.mission_profile[0], duration_s: -1 }] }), false);
  assert.equal(W.missionProfileComplete({ ...draft, mission_profile: [{ ...draft.mission_profile[0], battery_voltage_V: -1 }] }), false);
  assert.equal(W.missionProfileComplete({ ...draft, mission_profile: [{ ...draft.mission_profile[0], duration_s: '' }] }), false);
});

t('numeric schema values honor schema bounds and reject booleans, blanks and reversed ranges', () => {
  assert.equal(W.schemaValueComplete({ type: 'number' }, true), false);
  assert.equal(W.schemaValueComplete({ type: 'number' }, -1), true);
  assert.equal(W.schemaValueComplete({ type: 'number', min: 0 }, -1), false);
  assert.equal(W.schemaValueComplete({ type: 'number' }, -20), true);
  assert.equal(W.schemaValueComplete({ type: 'number' }, ''), false);
  assert.equal(W.schemaValueComplete({ allow_range: true }, { minimum: 5, maximum: 2 }), false);
});

t('mission profile requires finite nonnegative numeric values', () => {
  assert.equal(W.missionProfileComplete({ mission_profile: [{ phase: 'cruise', torque_Nm: true, speed_rpm: 2, duration_s: 3, battery_voltage_V: 4 }] }), false);
  assert.equal(W.missionProfileComplete({ mission_profile: [{ phase: 'cruise', torque_Nm: 1, speed_rpm: 2, duration_s: 3, battery_voltage_V: 4 }] }), true);
  assert.equal(W.missionProfileComplete({ mission_profile: [{ phase: 'regen', torque_Nm: -1, speed_rpm: -2, duration_s: 3, battery_voltage_V: 4 }] }), true);
});

t('relocated production section reads the production answer namespace', () => {
  const section = { id: 'delivery', production: true, fields: [{ id: 'units', required: true }] };
  const draft = { fields: {}, production: { units: { value: 12, status: 'filled' } } };
  assert.deepEqual(W.sectionCompleteness(section, draft), { filled: 1, required: 1 });
});

t('migration preview candidate is derived only from a known point and apply requires current revision plus acknowledgement', () => {
  const preview = { source_revision: 12, source_sha256: 'abc', requires_acknowledgement: true, issues: [], draft: { fields: { '1_4': { status: 'filled', value: { torque_Nm: 40, speed_rpm: 1200 } } } } };
  assert.deepEqual(migrationS1Candidate(preview), { torque_Nm: 40, speed_rpm: 1200, shaft_power_W: 40 * 2 * Math.PI * 1200 / 60 });
  assert.equal(canApplyMigrationPreview(preview, 12, false), false);
  assert.equal(canApplyMigrationPreview(preview, 12, true), true);
  assert.equal(canApplyMigrationPreview(preview, 13, true), false);
  const unknown = { ...preview, draft: { fields: { '1_4': { status: 'not_known_yet', value: { torque_Nm: 40, speed_rpm: 1200 } } } } };
  assert.equal(migrationS1Candidate(unknown), null);
});

t('editing an S1 point recalculates its derived power and removes stale power for blank inputs', () => {
  const saved = { torque_Nm: 30, speed_rpm: 1000, shaft_power_W: 2000 };
  const changed = W.withS1DerivedPower({ ...saved, torque_Nm: 60 });
  assert.equal(changed.shaft_power_W, 60 * 2 * Math.PI * 1000 / 60);
  assert.equal(W.withS1DerivedPower({ ...changed, speed_rpm: '' }).shaft_power_W, undefined);
});

