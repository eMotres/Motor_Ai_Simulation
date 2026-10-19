// node --test - identical catalogue GETs in flight share ONE request (owner 2026-10-19: a Configure load fired the
// references / family tree / context 2-4 times).  Imports lib/sharedGet.ts (Node >= 22.18 / 24; else skipped).
import test from 'node:test';
import assert from 'node:assert/strict';

let A = null;
try { A = await import('../sharedGet.ts'); } catch { /* old Node */ }
const t = A ? test : test.skip;
const API = 'http://localhost:8001';

function fakeFetch() {
  const calls = [];
  const f = (input, init) => {
    calls.push([String(input), init]);
    return new Promise((res) => setTimeout(() => res(new Response(JSON.stringify({ n: calls.length }), { status: 200 })), 5));
  };
  return { f, calls };
}

t('concurrent identical catalogue reads share one request; each caller reads its own answer', async () => {
  const { f, calls } = fakeFetch();
  const url = `${API}/api/catalog/references`;
  const [a, b, c] = await Promise.all([A.sharedGet(f, API, url), A.sharedGet(f, API, url), A.sharedGet(f, API, url, { cache: 'no-store' })]);
  assert.equal(calls.length, 1);
  assert.deepEqual([await a.json(), await b.json(), await c.json()], [{ n: 1 }, { n: 1 }, { n: 1 }]);
  await A.sharedGet(f, API, url);                                   // the flight is over: a later read is a new request
  assert.equal(calls.length, 2);
});

t('writes, aborted-able requests, other users and other endpoints are never shared', async () => {
  const { f, calls } = fakeFetch();
  const ctl = new AbortController();
  await Promise.all([
    A.sharedGet(f, API, `${API}/api/family/tree`, { headers: { Authorization: 'Bearer a' } }),
    A.sharedGet(f, API, `${API}/api/family/tree`, { headers: { Authorization: 'Bearer b' } }),   // another account
    A.sharedGet(f, API, `${API}/api/family/tree`, { signal: ctl.signal }),
    A.sharedGet(f, API, `${API}/api/me/last_motor`, { method: 'PUT', body: '{}' }),
    A.sharedGet(f, API, `${API}/api/catalog/cat_x/configure_load?config=L12`),
  ]);
  assert.equal(calls.length, 5);
  assert.ok(A.SHARED_GET_RE.test(`${API}/api/catalog/cat_x/configure_context`));
  assert.ok(!A.SHARED_GET_RE.test(`${API}/api/propellers/x/s1?die=a`));
});
