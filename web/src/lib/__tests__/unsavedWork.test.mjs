// node --test — what a load of another machine would lose (owner 2026-10-29): the pure half of
// lib/loadGuard.  Imports lib/unsavedWork.ts itself (Node >= 22.18 / 24; else skipped).
import test from 'node:test';
import assert from 'node:assert/strict';

let U = null;
try { U = await import('../unsavedWork.ts'); } catch { /* old Node */ }
const t = U ? test : test.skip;

// A tiny localStorage for the one key the module keeps.
const mem = new Map();
globalThis.localStorage = {
  getItem: (k) => (mem.has(k) ? mem.get(k) : null),
  setItem: (k, v) => { mem.set(k, String(v)); },
  removeItem: (k) => { mem.delete(k); },
};

t('nothing at stake asks nothing', () => {
  assert.equal(U.stakeIsEmpty({ jobs: [], unsavedRun: false, edits: null }), true);
  assert.equal(U.stakeIsEmpty({ jobs: [], unsavedRun: false, edits: { any: false } }), true);
});

t('a running job, an unsaved run or geometry edits each put something at stake', () => {
  const job = [{ run_id: 'r1', kind: 'optimizer.scan', state: 'running' }];
  assert.equal(U.stakeIsEmpty({ jobs: job, unsavedRun: false, edits: null }), false);
  assert.equal(U.stakeIsEmpty({ jobs: [], unsavedRun: true, edits: null }), false);
  assert.equal(U.stakeIsEmpty({ jobs: [], unsavedRun: false, edits: { any: true } }), false);
});

t('only running or queued jobs count; finished ones do not', () => {
  const rows = [
    { run_id: 'a', kind: 'optimizer.scan', state: 'running' },
    { run_id: 'b', kind: 'transient', state: 'queued' },
    { run_id: 'c', kind: 'transient', state: 'done' },
    { run_id: 'd', kind: 'coupled', state: 'interrupted' },
    { run_id: 'e', kind: 'optimizer.descent', state: 'cancelled' },
  ];
  assert.deepEqual(U.busyJobs(rows).map((j) => j.run_id), ['a', 'b']);
  assert.deepEqual(U.busyJobs(null), []);
});

t('the unsaved 14-turn geometry of the incident is a drift', () => {
  const saved = { stack_mm: 100, turns: 11, wire_height_mm: 0.5 };
  const live = { motor_length: 100, num_wires_per_slot: 14, wire_height: 0.5 };
  const d = U.buildDrift(saved, live);
  assert.equal(d.any, true);
  assert.deepEqual(d.turns, [11, 14]);
  assert.equal(d.stack, undefined);
  assert.equal(U.buildDrift(saved, { ...live, num_wires_per_slot: 11 }).any, false);
  // unknown on either side is not a drift (a machine that stores no build)
  assert.equal(U.buildDrift({}, live).any, false);
  assert.equal(U.buildDrift(saved, null).any, false);
});

t('a finished run stays unsaved until it is saved or the machine is loaded', () => {
  mem.clear();
  assert.equal(U.hasUnsavedRun(), false);
  U.noteFreshRun(1000);
  assert.equal(U.hasUnsavedRun(), true);
  U.markRunsSaved();
  assert.equal(U.hasUnsavedRun(), false);
});

t('blocked storage never breaks the question', () => {
  const keep = globalThis.localStorage;
  globalThis.localStorage = { getItem() { throw new Error('blocked'); }, setItem() { throw new Error('blocked'); }, removeItem() { throw new Error('blocked'); } };
  assert.doesNotThrow(() => { U.noteFreshRun(); U.markRunsSaved(); });
  assert.equal(U.hasUnsavedRun(), false);
  globalThis.localStorage = keep;
});

t('the tooltip names each thing at stake once', () => {
  const stake = {
    jobs: [{ run_id: 'a', kind: 'optimizer.scan', state: 'running' }, { run_id: 'b', kind: 'optimizer.scan', state: 'queued' }],
    unsavedRun: true,
    edits: { any: true, turns: [11, 14] },
  };
  const items = U.stakeItems(stake, 'rated', (w) => w);
  assert.deepEqual(items.map((i) => i.key), ['running', 'run', 'edits']);
  assert.equal(items[0].vars.what, 'sweep');
  assert.match(items[2].vars.what, /turns 11 → 14/);
});
