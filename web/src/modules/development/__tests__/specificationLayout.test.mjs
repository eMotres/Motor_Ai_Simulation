import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { buildSpecificationCards } from '../specificationLayout.ts';
import { sectionCompleteness, fieldAnswerComplete } from '../wizardState.ts';

test('specification cards preserve required fields once and keep production storage namespace', () => {
  const schema = JSON.parse(readFileSync(new URL('../../../../../src/motor_ai_sim/development/schema.json', import.meta.url), 'utf8'));
  const cards = buildSpecificationCards(schema);
  const ids = cards.flatMap((card) => card.fields.map((field) => field.id));
  assert.equal(new Set(ids).size, ids.length);
  const originalRequired = schema.sections.flatMap((section) => section.fields ?? []).filter((field) => field.required || field.required_flag === 'E');
  const groupedRequired = cards.flatMap((card) => card.fields).filter((field) => field.required || field.required_flag === 'E');
  assert.equal(groupedRequired.length, originalRequired.length);
  const production = cards.find((card) => card.id === 'delivery');
  assert.ok(production.fields.some((field) => field.storage_namespace === 'production'));
  assert.equal(sectionCompleteness(production, { production: {}, fields: {} }).required, production.fields.filter((f) => f.required || f.required_flag === 'E').length);
});

test('load required fields can be unknown and numeric limits remain schema driven', () => {
  const required = { id: 'load_inertia_kg_m2', type: 'number', min: 0, required: true };
  assert.equal(fieldAnswerComplete(required, { status: 'not_known_yet' }), true);
  assert.equal(fieldAnswerComplete(required, { status: 'filled', value: -1 }), false);
  assert.equal(fieldAnswerComplete({ id: 'ambient', type: 'number' }, { status: 'filled', value: -20 }), true);
  assert.equal(fieldAnswerComplete({ id: 'range', allow_range: true }, { status: 'filled', value: { minimum: 9, maximum: 2 } }), false);
});
