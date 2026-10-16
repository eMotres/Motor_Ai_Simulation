// node --test — the status-dispatch rule of motorStore.updateGeometryViaApi,
// restated here verbatim (the repo's convention for node tests: `node --test`
// cannot load the TS modules, so the pure rule under test is re-stated and kept
// in sync with stores/motorStore.ts).
//
// The property that matters: only a status that means "the backend is NOT
// REACHABLE" may queue the edit and mark the client disconnected, because
// App.tsx retries every 5 s while disconnected and fetchGeometryFromApi replays
// the queue.  A 500 is an answer, not an outage — queueing it produced the
// endless `PUT /api/geometry → 500` seen on production 2026-09-16, whose every
// replay rewrote the geometry object and made the Geometry tab blink.
import test from 'node:test';
import assert from 'node:assert/strict';

/** @returns {{queue: boolean, connected: boolean, surfaced: boolean}} */
function saveOutcome(status /* number, or 'network' for a thrown fetch */) {
  if (status === 422 || status === 423) {
    return { queue: false, connected: true, surfaced: true };   // refused, named
  }
  if (status === 500) {
    return { queue: false, connected: true, surfaced: true };   // broken route, named once
  }
  if (status === 'network' || status === 502 || status === 503 || status === 504) {
    return { queue: true, connected: false, surfaced: true };   // outage → replay later
  }
  if (status >= 200 && status < 300) {
    return { queue: false, connected: true, surfaced: false };  // saved
  }
  return { queue: true, connected: false, surfaced: true };     // unknown → treat as outage
}

test('a 500 is surfaced once and never queued — no replay loop', () => {
  const o = saveOutcome(500);
  assert.equal(o.queue, false, 'a 500 must not enter the offline queue');
  assert.equal(o.connected, true, 'the backend answered — it is not disconnected');
  assert.equal(o.surfaced, true, 'the user must see one error line');
});

test('a real outage still queues and reconnects', () => {
  for (const s of ['network', 502, 503, 504]) {
    const o = saveOutcome(s);
    assert.equal(o.queue, true, `status ${s} must queue the edit`);
    assert.equal(o.connected, false, `status ${s} must mark the client disconnected`);
  }
});

test('a refusal (422/423) clears the queue and keeps the connection', () => {
  for (const s of [422, 423]) {
    const o = saveOutcome(s);
    assert.equal(o.queue, false);
    assert.equal(o.connected, true);
    assert.equal(o.surfaced, true);
  }
});

test('a successful save queues nothing and shows no error', () => {
  const o = saveOutcome(200);
  assert.deepEqual(o, { queue: false, connected: true, surfaced: false });
});

test('only the outage statuses can ever drive the 5 s reconnect loop', () => {
  const replays = [200, 422, 423, 500, 502, 503, 504, 'network']
    .filter(s => saveOutcome(s).queue && !saveOutcome(s).connected);
  assert.deepEqual(replays, [502, 503, 504, 'network']);
});
