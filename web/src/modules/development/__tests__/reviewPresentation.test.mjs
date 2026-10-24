import test from 'node:test';
import assert from 'node:assert/strict';
import { matchesFieldReference, deliveryConflicts } from '../reviewPresentation.ts';

test('relocated comments retain old field aliases without conflating mission rows', () => {
  assert.equal(matchesFieldReference('4:4_6', 'fields.4_6'), true);
  assert.equal(matchesFieldReference('4_6', 'fields.4_6'), true);
  assert.equal(matchesFieldReference('production:quantities', 'production.quantities'), true);
  assert.equal(matchesFieldReference('mission_profile:a', 'mission_profile:b'), false);
});

test('delivery conflicts remain explicit and unknown retained values do not conflict', () => {
  const draft = {fields:{'6_1':{status:'filled',value:{prototypes:3}}},production:{quantities:{status:'filled',value:{prototypes:4}}}};
  assert.deepEqual(deliveryConflicts(draft), ['quantities']);
  assert.equal(draft.fields['6_1'].value.prototypes, 3);
  draft.production.quantities.status='not_known_yet';
  assert.deepEqual(deliveryConflicts(draft), []);
});

test('delivery ranges compare by content, not object identity', () => {
  const range = (minimum, maximum) => ({minimum, maximum});
  const draft = (a, b) => ({fields:{'6_1':{status:'filled',value:{prototypes:a}}},production:{quantities:{status:'filled',value:{prototypes:b}}}});
  assert.deepEqual(deliveryConflicts(draft(range(3, 5), range(3, 5))), []);
  assert.deepEqual(deliveryConflicts(draft(range(3, 5), range(3, 6))), ['quantities']);
  assert.deepEqual(deliveryConflicts(draft({status:'filled', value:range(3, 5)}, range(3, 5))), []);
  assert.deepEqual(deliveryConflicts(draft(4, range(3, 5))), ['quantities']);
});
