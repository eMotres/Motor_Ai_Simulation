// node --test — the pure rule of lib/sweepResumeNotice.ts, restated here
// verbatim (the repo's convention for node tests: `node --test` cannot load
// the TS modules, so the pure function under test is re-stated and kept in
// sync — see runNotice.test.mjs for the same pattern).
//
// The case this exists for: owner 2026-09-19, «нужно, чтобы автоматом это
// было видно после сбоя» — a sweep interrupted by an API restart resumes on
// its own (sweep_journal.py / sweep_resume.py) and the frontend must say so
// without the user doing anything, on whatever tab they are looking at.
import test from 'node:test';
import assert from 'node:assert/strict';

function formatLocalTime(at) {
  const d = new Date(at);
  if (Number.isNaN(d.getTime())) return String(at);
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function sweepResumeNoticeText(info) {
  const when = formatLocalTime(info.at);
  const done = Math.max(0, Math.trunc(Number(info.done_before) || 0));
  const total = Math.max(0, Math.trunc(Number(info.total) || 0));
  return `sweep resumed after a restart at ${when}: ${done} of ${total} points were already done`;
}

test('the sentence names when, and how many of how many', () => {
  const text = sweepResumeNoticeText({ at: '2026-09-19T14:23:00Z', done_before: 81, total: 128 });
  assert.ok(text.startsWith('sweep resumed after a restart at '));
  assert.ok(text.endsWith('81 of 128 points were already done'));
});

test('the time is the ISO instant read on the local clock, not the raw ISO string', () => {
  const text = sweepResumeNoticeText({ at: '2026-09-19T14:23:00Z', done_before: 1, total: 2 });
  assert.ok(!text.includes('2026-09-19T14:23:00Z'), 'a UTC timestamp is not what a viewer reads');
  assert.ok(/\d{1,2}:\d{2}/.test(text), 'a local HH:MM appears somewhere in the line');
});

test('an unparsable timestamp falls back to showing SOMETHING rather than throwing', () => {
  assert.doesNotThrow(() => sweepResumeNoticeText({ at: 'not-a-date', done_before: 0, total: 3 }));
  const text = sweepResumeNoticeText({ at: 'not-a-date', done_before: 0, total: 3 });
  assert.ok(text.includes('not-a-date'));
});

test('a fresh sweep (0 done before) still reads correctly', () => {
  const text = sweepResumeNoticeText({ at: '2026-09-19T09:00:00Z', done_before: 0, total: 4 });
  assert.ok(text.endsWith('0 of 4 points were already done'));
});

test('negative / non-numeric counts never produce a garbled or negative line', () => {
  const text = sweepResumeNoticeText({ at: '2026-09-19T09:00:00Z', done_before: -5, total: NaN });
  assert.ok(text.endsWith('0 of 0 points were already done'));
});

test('formatLocalTime alone: HH:MM, no seconds, no date', () => {
  const t = formatLocalTime('2026-09-19T14:23:00Z');
  assert.ok(/^\d{1,2}:\d{2}(?:\s?[AP]M)?$/.test(t), `unexpected shape: ${t}`);
});
