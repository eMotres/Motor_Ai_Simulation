// node --test: the owner-queue card logic (order, Skip, filters, keyboard map, ages, history).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { queueOrder, orderCards, skipCard, applyFilters, NO_PROJECT, keyAction, ageParts, clampIndex, historyOrder, answerBody, badgeTotal,
  projectChoices, sessionChoices, isApproval } from '../model.ts';

const it = (id, kind, created_at, extra = {}) => ({ id, kind, created_at, project_id: null, asked_by: 's', ...extra });

test('queue order: blocking kinds first, then messages, oldest first; pure', () => {
  const items = [it('m1', 'message', '2026-11-01'), it('d2', 'decision', '2026-11-03'), it('q1', 'question', '2026-11-02'),
    it('c1', 'catalog_write', '2026-11-04'), it('m0', 'message', '2026-10-30'), it('o1', 'outside_message', '2026-11-02')];
  assert.deepEqual(queueOrder(items).map((x) => x.id), ['q1', 'o1', 'd2', 'c1', 'm0', 'm1']);
  assert.equal(items[0].id, 'm1');
});

test('Skip moves the card to the end of the round, repeated skips queue behind each other', () => {
  const items = [it('a', 'decision', '1'), it('b', 'decision', '2'), it('c', 'decision', '3')];
  let sk = skipCard([], 'a');
  assert.deepEqual(orderCards(items, sk).map((x) => x.id), ['b', 'c', 'a']);
  sk = skipCard(sk, 'b');
  assert.deepEqual(orderCards(items, sk).map((x) => x.id), ['c', 'a', 'b']);
  sk = skipCard(sk, 'a');
  assert.deepEqual(orderCards(items, sk).map((x) => x.id), ['c', 'b', 'a']);
  assert.deepEqual(orderCards(items, ['gone']).map((x) => x.id), ['a', 'b', 'c']);
});

test('filters: by project, "no project" and by session', () => {
  const items = [it('a', 'decision', '1', { project_id: 'p1' }), it('b', 'decision', '2', { project_id: 'p2', asked_by: 'x' }), it('c', 'message', '3')];
  assert.deepEqual(applyFilters(items, { project: 'p1', session: '' }).map((x) => x.id), ['a']);
  assert.deepEqual(applyFilters(items, { project: NO_PROJECT, session: '' }).map((x) => x.id), ['c']);
  assert.deepEqual(applyFilters(items, { project: '', session: 'x' }).map((x) => x.id), ['b']);
  assert.deepEqual(applyFilters(items, { project: '', session: '' }).length, 3);
  assert.deepEqual(projectChoices(items), ['p1', 'p2']);
  assert.deepEqual(sessionChoices(items), ['s', 'x']);
});

test('keyboard map: arrows move, 1-5 pick, never while typing or with a modifier', () => {
  const ctx = { typing: false, optionCount: 3 };
  assert.deepEqual(keyAction('ArrowLeft', ctx), { type: 'prev' });
  assert.deepEqual(keyAction('ArrowRight', ctx), { type: 'next' });
  assert.deepEqual(keyAction('2', ctx), { type: 'option', index: 1 });
  assert.equal(keyAction('4', ctx), null);
  assert.equal(keyAction('6', ctx), null);
  assert.equal(keyAction('0', ctx), null);
  assert.equal(keyAction('a', ctx), null);
  for (const k of ['ArrowLeft', 'ArrowRight', '1']) assert.equal(keyAction(k, { ...ctx, typing: true }), null);
  assert.equal(keyAction('1', { ...ctx, modifier: true }), null);
  assert.equal(keyAction('1', { typing: false, optionCount: 0 }), null);
});

test('age, clamp, history order, answer body, badge, approval kinds', () => {
  const now = Date.parse('2026-11-25T12:00:00Z');
  assert.deepEqual(ageParts('2026-11-25T11:30:00Z', now), { unit: 'ageMinutes', n: 30 });
  assert.deepEqual(ageParts('2026-11-25T07:00:00Z', now), { unit: 'ageHours', n: 5 });
  assert.deepEqual(ageParts('2026-11-22T12:00:00Z', now), { unit: 'ageDays', n: 3 });
  assert.equal(clampIndex(5, 3), 2); assert.equal(clampIndex(-1, 3), 0); assert.equal(clampIndex(2, 0), 0);
  const h = historyOrder([{ id: 'a', state: 'answered', created_at: '1', answer: { at: '2026-11-02' } }, { id: 'b', state: 'open', created_at: '5', answer: null },
    { id: 'c', state: 'withdrawn', created_at: '2026-11-03', answer: null }]);
  assert.deepEqual(h.map((x) => x.id), ['c', 'a']);
  assert.deepEqual(answerBody('Approve', '  '), { option: 'Approve' });
  assert.deepEqual(answerBody(null, ' hi '), { text: 'hi' });
  assert.equal(badgeTotal({ open_blocking: 4, open_messages: 9, oldest_open_at: null }), 13);
  assert.equal(badgeTotal(null), 0);
  assert.ok(isApproval('outside_message') && isApproval('catalog_write') && !isApproval('decision'));
});
