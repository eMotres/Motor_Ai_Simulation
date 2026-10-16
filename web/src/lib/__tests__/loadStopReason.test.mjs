// node --test — a duty load stops on a refused geometry write (owner 2026-10-05).
// Imports lib/geometryApplyOutcome.ts itself (Node >= 22.18 / 24; else skipped).
import test from 'node:test';
import assert from 'node:assert/strict';

let G = null;
try { G = await import('../geometryApplyOutcome.ts'); } catch { /* old Node */ }
const t = G ? test : test.skip;

t('a written geometry lets the load continue', () => {
  assert.equal(G.loadStopReason('D', 'L12', { ok: true, refused: [] }), null);
  assert.equal(G.loadStopReason('D', 'L12', null), null);
});

t('a refused geometry stops the load and names the die, the configuration and the field', () => {
  const m = G.loadStopReason('CIANO14 40 new', 'L12',
    { ok: false, refused: [{ field: 'num_slots', reason: 'locked by the die' }] });
  assert.match(m, /CIANO14 40 new \/ L12 was refused/);
  assert.match(m, /num_slots: locked by the die/);
});

t('the real outcome of a 423 is a stop', () => {
  const o = G.geometryApplyOutcome(423, ['num_slots'], [{ field: 'num_slots', reason: 'locked' }]);
  assert.ok(G.loadStopReason('D', 'C', o));
  assert.equal(G.loadStopReason('D', 'C', G.geometryApplyOutcome(200, ['num_slots'], null)), null);
});
