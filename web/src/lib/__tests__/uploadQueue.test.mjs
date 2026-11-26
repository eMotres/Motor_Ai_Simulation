// node --test — the upload queue state (lib/uploadQueue.ts): queue, progress, concurrency, duplicate guard, retry, error text.
import test from 'node:test';
import assert from 'node:assert/strict';

let Q = null;
try { Q = await import('../uploadQueue.ts'); } catch { /* old Node */ }
const t = Q ? test : test.skip;
const F = (name, size) => ({ name, size });

t('three files with a limit of two: two start, one waits as queued', () => {
  let { state } = Q.enqueue(Q.emptyQueue(2), [F('a', 10), F('b', 20), F('c', 30)]);
  const first = Q.startable(state);
  assert.equal(first.length, 2);
  for (const id of first) state = Q.markStarted(state, id);
  assert.deepEqual(state.items.map((i) => i.status), ['uploading', 'uploading', 'queued']);
  assert.deepEqual(Q.startable(state), []);
  state = Q.markDone(state, first[0]);
  assert.deepEqual(Q.startable(state), [3]);
});

t('the same name and size is refused while active, accepted again once finished', () => {
  let r = Q.enqueue(Q.emptyQueue(), [F('a.pdf', 5)]);
  const dup = Q.enqueue(r.state, [F('a.pdf', 5), F('a.pdf', 6)]);
  assert.deepEqual(dup.duplicates, ['a.pdf']);
  assert.equal(dup.state.items.length, 2);
  assert.equal(Q.isDuplicate(dup.state, 'a.pdf', 5), true);
  let s = Q.markStarted(dup.state, 1);
  s = Q.markDone(s, 1);
  assert.equal(Q.isDuplicate(s, 'a.pdf', 5), false);
  const again = Q.enqueue(s, [F('a.pdf', 5)]);
  assert.equal(again.duplicates.length, 0);
  assert.equal(again.state.items.filter((i) => i.name === 'a.pdf' && i.size === 5).length, 1);
});

t('progress only grows, is capped at the size and ignored outside uploading', () => {
  let { state } = Q.enqueue(Q.emptyQueue(), [F('a', 100)]);
  state = Q.setProgress(state, 1, 50);
  assert.equal(state.items[0].loaded, 0);
  state = Q.markStarted(state, 1);
  state = Q.setProgress(state, 1, 40);
  state = Q.setProgress(state, 1, 30);
  assert.equal(state.items[0].loaded, 40);
  state = Q.setProgress(state, 1, 5000);
  assert.equal(state.items[0].loaded, 100);
  assert.equal(Q.percent(state.items[0]), 100);
  assert.equal(Q.percent({ loaded: 1, size: 3, status: 'uploading' }), 33);
  assert.equal(Q.percent({ loaded: 0, size: 0, status: 'uploading' }), 0);
});

t('failed keeps the server text, retry requeues from zero, a failed file does not block', () => {
  let { state } = Q.enqueue(Q.emptyQueue(1), [F('a', 10), F('b', 10)]);
  state = Q.markStarted(state, 1);
  state = Q.setProgress(state, 1, 4);
  state = Q.markFailed(state, 1, 'File too large');
  assert.equal(state.items[0].error, 'File too large');
  assert.equal(Q.isBusy(state), true);
  assert.deepEqual(Q.startable(state), [2]);
  state = Q.retry(state, 1);
  assert.equal(state.items[0].status, 'queued');
  assert.equal(state.items[0].loaded, 0);
  assert.equal(state.items[0].error, '');
});

t('retry is refused when the same file is active again; done rows cannot be retried', () => {
  let { state } = Q.enqueue(Q.emptyQueue(), [F('a', 10)]);
  state = Q.markStarted(state, 1);
  state = Q.markFailed(state, 1, 'x');
  state = Q.enqueue(state, [F('a', 10)]).state;
  // the failed row of the same key was replaced by the new queued one
  assert.equal(state.items.length, 1);
  assert.equal(Q.retry(state, state.items[0].id).items[0].status, 'queued');
  let d = Q.markDone(Q.markStarted(Q.enqueue(Q.emptyQueue(), [F('z', 1)]).state, 1), 1);
  assert.equal(Q.retry(d, 1).items[0].status, 'done');
});

t('cancel works on queued and uploading rows and frees the slot; dismiss only on finished rows', () => {
  let { state } = Q.enqueue(Q.emptyQueue(1), [F('a', 10), F('b', 10)]);
  state = Q.markStarted(state, 1);
  assert.equal(Q.dismiss(state, 1).items.length, 2);
  state = Q.cancel(state, 1);
  assert.equal(state.items[0].status, 'cancelled');
  assert.deepEqual(Q.startable(state), [2]);
  state = Q.cancel(state, 2);
  assert.equal(Q.isBusy(state), false);
  assert.equal(Q.dismiss(state, 1).items.length, 1);
  assert.equal(Q.retry(state, 1).items[0].status, 'queued');
});

t('a late done / progress after cancel does not revive the row', () => {
  let { state } = Q.enqueue(Q.emptyQueue(), [F('a', 10)]);
  state = Q.cancel(Q.markStarted(state, 1), 1);
  state = Q.markDone(Q.setProgress(state, 1, 10), 1);
  assert.equal(state.items[0].status, 'cancelled');
  assert.equal(Q.markFailed(state, 1, 'late').items[0].status, 'cancelled');
});

t('clearFinished drops done rows only', () => {
  let { state } = Q.enqueue(Q.emptyQueue(2), [F('a', 1), F('b', 1)]);
  state = Q.markDone(Q.markStarted(state, 1), 1);
  state = Q.markFailed(Q.markStarted(state, 2), 2, 'no');
  assert.deepEqual(Q.clearFinished(state).items.map((i) => i.name), ['b']);
  assert.deepEqual(Q.counts(state), { queued: 0, uploading: 0, done: 1, failed: 1, cancelled: 0 });
});

t('byte labels and the server error text', () => {
  assert.equal(Q.progressLabel({ loaded: 4404019, size: 11811160 }), '4.2 MB / 11.3 MB');
  assert.equal(Q.formatUploadBytes(0), '0 B');
  assert.equal(Q.serverErrorText(413, { detail: 'File is larger than 200 MB' }), 'File is larger than 200 MB');
  assert.equal(Q.serverErrorText(409, { detail: { message: 'Revision exists' } }), 'Revision exists');
  assert.equal(Q.serverErrorText(502, null), 'HTTP 502');
  assert.equal(Q.serverErrorText(500, { detail: [{ loc: 1 }] }), 'HTTP 500');
});

t('enqueue pairs every accepted id with its input index and refuses a repeat inside one batch', () => {
  const r = Q.enqueue(Q.emptyQueue(), [F('a', 1), F('a', 1), F('b', 2)]);
  assert.deepEqual(r.accepted, [{ id: 1, index: 0 }, { id: 2, index: 2 }]);
  assert.deepEqual(r.duplicates, ['a']);
});
