import test from 'node:test';
import assert from 'node:assert/strict';

let counts = null;
try { counts = await import('../authEventCounts.ts'); } catch { /* Node without TS stripping */ }
const t = counts ? test : test.skip;

t('24 h auth reject tile excludes idle session closes', () => {
  const now = Date.parse('2026-10-20T12:00:00Z');
  const events = [
    { event: 'reject', reason: 'malformed', ts: '2026-10-20T11:00:00Z' },
    { event: 'reject', reason: 'idle_timeout', ts: '2026-10-20T11:30:00Z' },
    { event: 'revoke', reason: 'idle_timeout', ts: '2026-10-20T11:30:00Z' },
    { event: 'reject', reason: 'expired', ts: '2026-10-19T10:00:00Z' },
  ];
  assert.equal(counts.countAuthRejects24h(events, now), 1);
});
