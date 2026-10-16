/**
 * The materials library's RETRY POLICY, tested without a browser.
 *
 * The tab renders one grey caption when the library fetch fails, and before
 * 2026-09-16 nothing ever asked again: one failure emptied the Materials tab
 * for the whole session (production report: "materials are not displayed").
 * The fix is a bounded backoff — and a bounded backoff has exactly two ways to
 * be wrong:
 *
 *   • it retries a REFUSAL (401 / 403 / 404), which cannot change by being
 *     asked again — six pointless requests at a door the server just closed,
 *     which is the noise the anonymous-load work exists to remove;
 *   • it retries forever, or with no delay, turning one open tab into a
 *     poller against a backend that is already down.
 *
 * Both helpers are pure and are re-implemented verbatim here: the module is
 * TypeScript and reads `import.meta.env`, which `node --test` cannot load (the
 * repo's convention — see panelSettings.test.mjs).  Changing the policy in
 * `useMaterialsLibrary.ts` therefore has to change this file too.
 */
import test from 'node:test';
import assert from 'node:assert/strict';

/* ── verbatim copies of the shipped helpers ──────────────────────────────── */

const MAX_LIBRARY_RETRIES = 6;

function isRetriableStatus(status) {
  return status === 0 || status === 408 || status === 429 || status >= 500;
}

function retryDelayMs(attempt) {
  return Math.min(1000 * 2 ** attempt, 15000);
}

/* ── what is worth asking again ──────────────────────────────────────────── */

test('a server that never answered is worth asking again', () => {
  // status 0 = offline / DNS / the api container mid-restart: the fetch
  // rejects before any response exists.
  assert.equal(isRetriableStatus(0), true);
});

test('the server saying "not now" is worth asking again', () => {
  for (const s of [408, 429, 500, 502, 503, 504]) {
    assert.equal(isRetriableStatus(s), true, `${s} should be retried`);
  }
});

test('a REFUSAL is final — a closed door is not asked six more times', () => {
  // 401 not signed in (the gate, not a retry, handles that), 403 the tier does
  // not open the library, 404 no such route on this backend.
  for (const s of [400, 401, 403, 404, 409, 422]) {
    assert.equal(isRetriableStatus(s), false, `${s} must not be retried`);
  }
});

test('a 200 that failed to parse is not retried either', () => {
  assert.equal(isRetriableStatus(200), false);
});

/* ── how long it waits ───────────────────────────────────────────────────── */

test('the delay doubles and then stops doubling', () => {
  assert.deepEqual(
    [0, 1, 2, 3, 4, 5].map(retryDelayMs),
    [1000, 2000, 4000, 8000, 15000, 15000],
  );
});

test('the wait is never zero — no tight loop against a dead backend', () => {
  for (let a = 0; a < 20; a += 1) {
    assert.ok(retryDelayMs(a) >= 1000, `attempt ${a} waited ${retryDelayMs(a)} ms`);
    assert.ok(retryDelayMs(a) <= 15000, `attempt ${a} waited ${retryDelayMs(a)} ms`);
  }
});

/* ── the budget as the hook spends it ────────────────────────────────────── */

/** The hook's own loop, with the fetch replaced by a list of statuses: it
 *  retries while the failure is transient AND the budget is unspent, and a
 *  SUCCESS returns the budget to full (the next outage starts over). */
function run(statuses) {
  let attempts = 0;      // requests actually made
  let retries = 0;       // the hook's retriesRef
  const waits = [];
  for (const status of statuses) {
    attempts += 1;
    if (status === 200) { retries = 0; return { attempts, waits, loaded: true }; }
    if (!isRetriableStatus(status) || retries >= MAX_LIBRARY_RETRIES) {
      return { attempts, waits, loaded: false };
    }
    waits.push(retryDelayMs(retries));
    retries += 1;
  }
  return { attempts, waits, loaded: false };
}

test('one bad second costs one retry, and the tab fills', () => {
  const r = run([503, 200]);
  assert.deepEqual(r, { attempts: 2, waits: [1000], loaded: true });
});

test('a backend that stays down is asked seven times, then never again', () => {
  // the first request plus MAX_LIBRARY_RETRIES more, ~45 s of patience.
  const r = run(Array(20).fill(503));
  assert.equal(r.attempts, MAX_LIBRARY_RETRIES + 1);
  assert.equal(r.loaded, false);
  assert.equal(r.waits.reduce((a, b) => a + b, 0), 1000 + 2000 + 4000 + 8000 + 15000 + 15000);
});

test('a 401 costs exactly one request — the gate owns that case', () => {
  const r = run([401, 200]);
  assert.deepEqual(r, { attempts: 1, waits: [], loaded: false });
});
