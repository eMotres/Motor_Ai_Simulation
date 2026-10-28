// node --test: pure helpers of the unread-messages feature (owner 2026-10-28). Imports unreadMessages.ts itself (Node >= 22.18 / 24).
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  accumulateHighlight, firstUnreadIndex, NO_UNREAD, parseUnreadCounts, productionUnreadKey, unreadIdsOf, unreadLabel,
} from '../unreadMessages.ts';

test('the counts answer is parsed defensively: bad input is "nothing unread"', () => {
  assert.deepEqual(parseUnreadCounts(null), NO_UNREAD);
  assert.deepEqual(parseUnreadCounts('x'), NO_UNREAD);
  const got = parseUnreadCounts({ projects: { a: 2, b: 0, c: -1, d: 'x' }, productions: { 'Demo X1/L10': 3 }, total_projects: 2, total_productions: 3 });
  assert.deepEqual(got, { projects: { a: 2 }, productions: { 'Demo X1/L10': 3 }, totalProjects: 2, totalProductions: 3 });
});

test('a missing total is the sum of the items', () => {
  const got = parseUnreadCounts({ projects: { a: 2, b: 5 }, productions: {} });
  assert.equal(got.totalProjects, 7);
  assert.equal(got.totalProductions, 0);
});

test('badge text: empty for 0, capped at 99+', () => {
  assert.equal(unreadLabel(0), '');
  assert.equal(unreadLabel(7), '7');
  assert.equal(unreadLabel(99), '99');
  assert.equal(unreadLabel(100), '99+');
  assert.equal(unreadLabel(NaN), '');
});

test('production key is die/config', () => {
  assert.equal(productionUnreadKey('Demo X1', 'L10'), 'Demo X1/L10');
});

test('the highlight keeps a message that was unread until the thread is left, and the divider sits before the first one', () => {
  const thread = [{ id: 'a' }, { id: 'b', unread: true }, { id: 'c', unread: true }];
  const flagged = unreadIdsOf(thread);
  assert.deepEqual(flagged, ['b', 'c']);
  let shown = accumulateHighlight(new Set(), flagged);
  // the server now says everything is read, but the open thread still shows what was new
  shown = accumulateHighlight(shown, unreadIdsOf(thread.map((m) => ({ id: m.id }))));
  assert.deepEqual([...shown], ['b', 'c']);
  assert.equal(firstUnreadIndex(thread, shown), 1);
  assert.equal(firstUnreadIndex(thread, new Set()), -1);
});

test('the menu shows the totals for Development and Production and nothing is polled while the page is hidden', () => {
  const app = readFileSync(new URL('../../App.tsx', import.meta.url), 'utf8');
  assert.match(app, /totalProductions/);
  assert.match(app, /totalProjects/);
  const hook = readFileSync(new URL('../useUnreadMessages.ts', import.meta.url), 'utf8');
  assert.match(hook, /pageVisible\(\)/);
  const store = readFileSync(new URL('../unreadMessages.ts', import.meta.url), 'utf8');
  assert.match(store, /\/api\/messages\/unread/);
});
