import { test } from 'node:test';
import assert from 'node:assert/strict';
import { answerOf, buildsOf, cellOf, COLUMNS, hasColumns, localized, parseNumber, payloadFields, withAnswer, withCell } from '../controllerSpec.ts';

const pwm = { id: 'pwm', label: { en: 'PWM' }, kind: 'number', unit: 'kHz' };
const bus = { id: 'bus_voltage', label: { en: 'Bus voltage', 'zh-CN': '母线电压' }, kind: 'range', unit: 'V', per_build: true };
const schema = { schema_version: 1, title: { en: 'Controller specification' }, statuses: [], sections: [{ id: 's', title: { en: 'S' }, fields: [pwm, bus] }] };

test('answers per build and not, read and written without touching the others', () => {
  let f = { builds: { value: ['6S', '12S', ' '] } };
  assert.deepEqual(buildsOf(f), ['6S', '12S']);
  assert.deepEqual(answerOf(f, pwm), { values: {} });
  f = withAnswer(f, pwm, { value: 48, status: 'measured', ref: 'bench' });
  f = withAnswer(f, bus, { value: { min: 18, nom: null, max: 25.2 }, status: 'requirement' }, '6S');
  f = withAnswer(f, bus, { value: { min: 36, nom: null, max: 50.4 }, status: 'model' }, '12S');
  assert.equal(answerOf(f, pwm).value, 48);
  assert.equal(answerOf(f, bus, '12S').status, 'model');
  assert.deepEqual(answerOf(f, bus, '8S'), { values: {} });
});

test('removing a build asks first: its answers are counted, and dropped only through withBuilds; the save sends everything', async () => {
  const { buildsWithAnswers, withBuilds } = await import('../controllerSpec.ts');
  let f = { builds: { value: ['6S', '12S'] } };
  f = withAnswer(f, bus, { values: { requirement: { value: { min: 1, nom: null, max: 2 }, ref: '' } } }, '6S');
  f = withAnswer(f, bus, { values: { requirement: { value: { min: 3, nom: null, max: 4 }, ref: '' } } }, '12S');
  assert.deepEqual(buildsWithAnswers(f, schema, ['12S']), [{ build: '6S', answers: 1 }]);
  assert.deepEqual(buildsWithAnswers(f, schema, ['6S', '12S', '24S']), []);
  assert.deepEqual(Object.keys(payloadFields(f, schema).bus_voltage.builds), ['6S', '12S']);     // nothing dropped silently
  const g = withBuilds(f, schema, ['12S']);
  assert.deepEqual(g.builds.value, ['12S']);
  assert.deepEqual(Object.keys(g.bus_voltage.builds), ['12S']);
});

test('a single earlier answer of a field that is per build now reads for every build and survives editing one build', () => {
  const f = { builds: { value: ['6S', '12S'] }, bus_voltage: { values: { model: { value: { min: 1, nom: null, max: 2 }, ref: '' } } } };
  assert.equal(answerOf(f, bus, '12S').values.model.value.max, 2);
  const g = withAnswer(f, bus, { values: {} }, '6S');
  assert.deepEqual(Object.keys(g.bus_voltage.builds), ['6S', '12S']);
  assert.equal(g.bus_voltage.builds['12S'].values.model.value.max, 2);
});

test('numbers, labels and the kinds with a status', () => {
  assert.equal(parseNumber(' 4,5 '), 4.5);
  assert.equal(parseNumber(''), null);
  assert.equal(parseNumber('fast'), undefined);
  assert.equal(localized(bus.label, 'zh-CN'), '母线电压');
  assert.equal(localized(pwm.label, 'zh-CN'), 'PWM');
  assert.ok(hasColumns('number') && hasColumns('range') && !hasColumns('text') && !hasColumns('thresholds'));
});

test('after a save the form holds the answer\'s fields and revision; an edit typed meanwhile stays unsaved on top', async () => {
  const { afterSave, isConflict } = await import('../controllerSpec.ts');
  const saved = { fields: { pwm: { value: 48 } }, revision: 2 };
  assert.deepEqual(afterSave({ pwm: { value: 48 } }, saved, false), { fields: saved.fields, revision: 2, dirty: false });
  const typed = { pwm: { value: 50 } };
  assert.deepEqual(afterSave(typed, saved, true), { fields: typed, revision: 2, dirty: true });
  assert.ok(isConflict(Object.assign(new Error('x'), { status: 409 })) && !isConflict(new Error('y')));
});

test('three columns per value: requirement, model, measured; an empty column is dropped', () => {
  assert.deepEqual([...COLUMNS], ['requirement', 'model', 'measured']);
  let a = { values: {} };
  a = withCell(a, 'requirement', { value: 48, ref: 'spec' });
  a = withCell(a, 'model', { value: 46.5, ref: '' });
  assert.deepEqual(a.values, { requirement: { value: 48, ref: 'spec' }, model: { value: 46.5, ref: '' } });
  assert.deepEqual(cellOf(a, 'measured'), { value: null, ref: '' });
  a = withCell(a, 'model', { value: null, ref: '' });
  assert.deepEqual(Object.keys(a.values), ['requirement']);
  const r = withCell({ values: {} }, 'model', { value: { min: null, nom: null, max: null }, ref: '' });
  assert.deepEqual(r.values, {});                                  // an all-empty range is no value
  assert.deepEqual(withCell({ values: {} }, 'measured', { value: null, ref: 'bench plan' }).values, { measured: { value: null, ref: 'bench plan' } });
});
