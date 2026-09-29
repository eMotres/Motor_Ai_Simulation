/**
 * node --test — the "Loaded from history — computed …" notice line.
 *
 * Owner, 2026-09-22: every panel's result header shows this ONE line when
 * the backend answered from persistent history instead of solving. Pure
 * formatting only (`web/src/lib/historyNotice.ts`) — no fetch, no store.
 */
import test from 'node:test';
import assert from 'node:assert/strict';

import { formatHistoryComputedAt, historyNoticeFor, historyRowLabel }
  from '../historyNotice.ts';

// The formatter renders in the VIEWER's own local time (right for a UI: the
// user reads "18:20" as their own clock, not a UTC offset they have to do
// math on) — so these tests build the expected string the same way, from a
// real Date, rather than hardcoding one timezone and breaking in every
// other one the test runner (or CI) happens to sit in.
const STAMP = '2026-09-21T18:20:00+00:00';
function expectedLocal(iso) {
  const d = new Date(iso);
  const dd = String(d.getDate()).padStart(2, '0');
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const hh = String(d.getHours()).padStart(2, '0');
  const mi = String(d.getMinutes()).padStart(2, '0');
  return `${dd}.${mm} ${hh}:${mi}`;
}

test('formats an ISO timestamp as DD.MM HH:MM (viewer local time)', () => {
  assert.equal(formatHistoryComputedAt(STAMP), expectedLocal(STAMP));
});

test('a missing or unparsable timestamp formats to null', () => {
  assert.equal(formatHistoryComputedAt(null), null);
  assert.equal(formatHistoryComputedAt(undefined), null);
  assert.equal(formatHistoryComputedAt(''), null);
  assert.equal(formatHistoryComputedAt('not a date'), null);
});

test('historyNoticeFor is null when the result was not served from history', () => {
  assert.equal(historyNoticeFor(null), null);
  assert.equal(historyNoticeFor(undefined), null);
  assert.equal(historyNoticeFor({ served_from_history: false,
                                  computed_at: '2026-09-21T18:20:00+00:00' }),
              null);
  assert.equal(historyNoticeFor({ computed_at: '2026-09-21T18:20:00+00:00' }),
              null);
});

test('historyNoticeFor is null when served_from_history is true but the '
     + 'timestamp cannot be read — never a claim it cannot back up', () => {
  assert.equal(historyNoticeFor({ served_from_history: true, computed_at: null }),
              null);
  assert.equal(
    historyNoticeFor({ served_from_history: true, computed_at: 'garbage' }),
    null);
});

test('historyNoticeFor renders the one-line notice', () => {
  const n = historyNoticeFor({ served_from_history: true, computed_at: STAMP });
  assert.deepEqual(n, { text: `Loaded from history — computed ${expectedLocal(STAMP)}` });
});

test('historyRowLabel joins the summary and the time', () => {
  assert.equal(
    historyRowLabel({ summary: '23,000 rpm, SF 25.13', computed_at: STAMP }),
    `23,000 rpm, SF 25.13 — ${expectedLocal(STAMP)}`);
});

test('historyRowLabel degrades gracefully with a missing half', () => {
  assert.equal(historyRowLabel({ summary: '23,000 rpm', computed_at: null }),
              '23,000 rpm');
  assert.equal(
    historyRowLabel({ summary: '', computed_at: STAMP }),
    expectedLocal(STAMP));
  assert.equal(historyRowLabel(null), '');
  assert.equal(historyRowLabel({}), '');
});
