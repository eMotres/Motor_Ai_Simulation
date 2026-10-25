import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { buildSpecificationCards, cardIndexForField, sectionCommentRefs, KEY_FIELDS, REQUIREMENT_FIELDS, SECOND_TIER_FIELDS, MORE_FIRST_FIELDS } from '../specificationLayout.ts';
import { sectionCompleteness, fieldAnswerComplete } from '../wizardState.ts';

const load = (name) => JSON.parse(readFileSync(new URL(`../../../../../src/motor_ai_sim/development/${name}`, import.meta.url), 'utf8'));
const schema = load('schema.json');

test('specification cards keep every required field exactly once; production and optional extras are not on the page', () => {
  const cards = buildSpecificationCards(schema);
  const ids = cards.flatMap((card) => card.fields.map((field) => `${field.storage_namespace}:${field.id}`));
  assert.equal(new Set(ids).size, ids.length);
  const originalRequired = schema.sections.flatMap((section) => section.fields ?? []).filter((field) => field.required || field.required_flag === 'E');
  const groupedRequired = cards.flatMap((card) => card.fields).filter((field) => field.required || field.required_flag === 'E');
  assert.equal(groupedRequired.length, originalRequired.length);
  assert.ok(cards.flatMap((card) => card.fields).every((field) => field.storage_namespace !== 'production'));
});

test('minimal layout (owner 2026-10-25): key data (with the load type), requirements, mission, review - no More details', () => {
  const cards = buildSpecificationCards(schema);
  const card = (id) => cards.find((c) => c.id === id);
  assert.deepEqual(cards.map((c) => c.id), ['key', 'requirements', 'mission', 'review']);
  assert.deepEqual(card('key').fields.map((f) => f.id), [...KEY_FIELDS, 'load_type']);
  assert.deepEqual(card('requirements').fields.map((f) => f.id), [...REQUIREMENT_FIELDS, ...SECOND_TIER_FIELDS]);
  assert.deepEqual(card('mission').fields.map((f) => f.id), ['mission_profile']);
  // the former extras are not on the page (their stored values are kept in the draft)
  for (const id of [...MORE_FIRST_FIELDS, 'load_characteristic']) assert.equal(cardIndexForField(cards, id), -1, id);
});

test('the required set is exactly the one the compact specification asks for', () => {
  const required = schema.sections.flatMap((s) => s.fields ?? []).filter((f) => f.required).map((f) => f.id).sort();
  assert.deepEqual(required, ['1_1', '1_2', '1_4', '1_6', '2_1', '2_4', '3_1', '3_2', '3_3', '4_1', '4_2', '4_6', '6_1', '6_2', 'mission_profile'].sort());
  // the axial load is required (owner 2026-10-25: «обязательно аксиальную нагрузку надо учесть»)
  const loads = schema.sections.flatMap((s) => s.fields ?? []).find((f) => f.id === '4_6');
  assert.equal(loads.properties.find((p) => p.id === 'axial_thrust_N').required, true);
  const loadCurve = schema.sections.flatMap((s) => s.fields ?? []).find((f) => f.id === 'load_characteristic');
  assert.deepEqual(loadCurve.required_when, { field: 'load_source', in: ['catalog', 'manual'] });
  const extra = schema.sections.flatMap((s) => s.fields ?? []).find((f) => f.id === 'additional_requirements');
  assert.equal(extra.type, 'string'); assert.equal(extra.multiline, true); assert.equal(extra.required, false);
  assert.ok(extra.label.en && extra.label['zh-CN']);
});

test('the v1 schema lays out its page fields once', () => {
  const v1 = load('schema_v1.json');
  const cards = buildSpecificationCards(v1);
  const ids = cards.flatMap((c) => c.fields.map((f) => `${f.storage_namespace}:${f.id}`));
  assert.equal(new Set(ids).size, ids.length);
  // v1 is legacy (every live project is migrated to v2): its fields that the minimal page does not ask stay stored, unshown
  assert.ok(ids.length > 0 && ids.length <= v1.sections.flatMap((s) => s.fields ?? []).length);
});

test('section comments written against the previous card ids stay on the card that holds those fields', () => {
  const cards = buildSpecificationCards(schema);
  const refs = Object.fromEntries(cards.map((c) => [c.id, sectionCommentRefs(c)]));
  for (const [legacy, now] of [['application', 'key'], ['continuous', 'key'], ['electrical', 'key'], ['cooling', 'key'], ['mechanical', 'requirements'], ['load', 'key'], ['delivery', 'requirements'], ['mission', 'mission'], ['review', 'review']]) {
    assert.ok(refs[now].includes(`section:${legacy}`), legacy);
  }
});

test('load required fields can be unknown and numeric limits remain schema driven', () => {
  const required = { id: 'load_inertia_kg_m2', type: 'number', min: 0, required: true };
  assert.equal(fieldAnswerComplete(required, { status: 'not_known_yet' }), true);
  assert.equal(fieldAnswerComplete(required, { status: 'filled', value: -1 }), false);
  assert.equal(fieldAnswerComplete({ id: 'ambient', type: 'number' }, { status: 'filled', value: -20 }), true);
  assert.equal(fieldAnswerComplete({ id: 'range', allow_range: true }, { status: 'filled', value: { minimum: 9, maximum: 2 } }), false);
});
