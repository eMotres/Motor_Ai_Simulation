// Specification assistant, fill mode and the kept conversation: the pure logic (synthetic data only).
import test from 'node:test';
import assert from 'node:assert/strict';
import { basisOf, filledState, fillEnabled, fromStored, historyForServer } from '../specAssistant.ts';

test('basis: the server value wins, else the reason tag decides', () => {
  assert.equal(basisOf({ reason: 'x', basis: 'stated' }), 'stated');
  assert.equal(basisOf({ reason: 'typical (assumed, to confirm)' }), 'assumed');
  assert.equal(basisOf({ reason: '典型值（假设，待确认）' }), 'assumed');
  assert.equal(basisOf({ reason: 'from power and voltage' }), 'derived');
});

test('a filled card offers Undo only to who can edit, while the fill stands', () => {
  assert.equal(filledState({ status: 'applied' }, true), 'undo');
  assert.equal(filledState({ status: 'applied' }, false), 'filled');
  assert.equal(filledState({ status: 'undone' }, true), 'undone');
  assert.equal(filledState({ status: 'open' }, true), 'gone');
});

test('fill mode is on unless switched off', () => {
  assert.equal(fillEnabled(null), true);
  assert.equal(fillEnabled('1'), true);
  assert.equal(fillEnabled('0'), false);
});

test('the stored conversation becomes chat messages with their applied cards, and only role + text go back to the server', () => {
  const card = { id: 'p-1', field_id: '4_8', value: {}, reason: 'r', status: 'applied' };
  const rows = [{ id: 'm-1', role: 'user', text: 'hi', applied: [], proposals: [] },
    { id: 'm-2', role: 'assistant', text: 'Filled.', applied: [card], proposals: [] }];
  const chat = fromStored(rows);
  assert.deepEqual(chat.map((m) => m.role), ['user', 'assistant']);
  assert.equal(chat[0].applied, undefined);
  assert.deepEqual(chat[1].applied, [card]);
  assert.deepEqual(historyForServer([...chat, { role: 'user', content: 'more' }]), [
    { role: 'user', content: 'hi' }, { role: 'assistant', content: 'Filled.' }, { role: 'user', content: 'more' }]);
});
