// node --test — what the help assistant is told about the screen
// (lib/supportContext.ts).  Imports the module itself (no runtime imports);
// needs a Node that strips TypeScript types (>= 22.18 / 23.6 / 24), else skipped.
//
// Owner 2026-10-05: every ticket and every chat carries the current tab, the
// motor / configuration, the Configure knobs and key tiles (refusals included),
// the build, the browser and the last ~10 failed API calls — NEVER tokens,
// cookies, passwords or other users' data, and compact.  What it pins:
//   * the recorder keeps only method / path (no query) / status / a clipped message,
//     at most ten, only for OUR api, and never the support routes themselves;
//   * a request's headers (the bearer token) and body never reach it;
//   * the Configure snapshot rounds numbers, drops blanks and names its warnings;
//   * the context only carries the Configure snapshot while the user is ON Configure.
import test from 'node:test';
import assert from 'node:assert/strict';

let C = null;
try { C = await import('../supportContext.ts'); } catch { /* old Node */ }
const t = C ? test : test.skip;
const API = 'http://localhost:8001';

const reset = () => { C.clearFailedCalls(); C.setConfigureSnapshot(null); };

t('apiPath keeps the path only and refuses other servers and non-api paths', () => {
  assert.equal(C.apiPath(`${API}/api/geometry?geo=${'x'.repeat(200)}&token=abc`, API), '/api/geometry');
  assert.equal(C.apiPath('/api/me#frag', API), '/api/me');
  assert.equal(C.apiPath('https://evil.example.com/api/me', API), null);
  assert.equal(C.apiPath(`${API}/assets/app.js`, API), null);
  assert.equal(C.apiPath('', API), null);
});

t('recordFailedCall: capped at ten, newest kept, support routes and strangers ignored', () => {
  reset();
  for (let i = 0; i < 25; i++) C.recordFailedCall({ method: 'post', url: `${API}/api/run/${i}?x=1`, status: 500, message: `boom ${i}` }, API, 1000);
  C.recordFailedCall({ url: `${API}/api/support/chat`, status: 500 }, API);
  C.recordFailedCall({ url: 'https://other.example.com/api/x', status: 500 }, API);
  const f = C.failedCalls(6000);
  assert.equal(f.length, 10);
  assert.equal(f[9].path, '/api/run/24');
  assert.equal(f[0].path, '/api/run/15');
  assert.equal(f[0].method, 'POST');
  assert.equal(f[0].agoS, 5);
  assert.deepEqual(Object.keys(f[0]).sort(), ['agoS', 'message', 'method', 'path', 'status']);
});

t('messages are clipped to one short line', () => {
  reset();
  C.recordFailedCall({ url: `${API}/api/x`, status: 422, message: `line one\n${'y'.repeat(500)}` }, API);
  const m = C.failedCalls()[0].message;
  assert.ok(m.length <= 160 && !m.includes('\n'));
});

t('errorMessageOf reads FastAPI details and never the whole body', () => {
  assert.equal(C.errorMessageOf({ detail: 'sign in to use tickets' }, 'x'), 'sign in to use tickets');
  assert.equal(C.errorMessageOf({ detail: [{ msg: 'field required' }] }, 'x'), 'field required');
  assert.equal(C.errorMessageOf({ error: 'nope' }, 'x'), 'nope');
  assert.equal(C.errorMessageOf({ secret: 1 }, 'HTTP 500'), 'HTTP 500');
  assert.equal(C.errorMessageOf(null, 'HTTP 500'), 'HTTP 500');
});

t('the fetch recorder sees status and error text only: never headers or the body', async () => {
  reset();
  const TOKEN = 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.signaturesignature';
  const calls = [];
  const fake = async (input, init) => {
    calls.push({ input, init });
    const url = String(input);
    if (url.includes('/api/boom')) return new Response(JSON.stringify({ detail: 'solver crashed' }), { status: 500, statusText: 'Server Error' });
    if (url.includes('/api/net')) throw new TypeError('Failed to fetch');
    return new Response('{}', { status: 200 });
  };
  globalThis.fetch = fake;
  delete globalThis.__supportRecorderInstalled;
  C.installFailedCallRecorder(API);
  C.installFailedCallRecorder(API);                       // twice: wraps once
  const ok = await fetch(`${API}/api/fine`, { headers: { Authorization: `Bearer ${TOKEN}` } });
  assert.equal(ok.status, 200);
  const bad = await fetch(`${API}/api/boom?geo=${TOKEN}`, { method: 'POST', headers: { Authorization: `Bearer ${TOKEN}` }, body: JSON.stringify({ password: 'hunter2' }) });
  assert.equal(bad.status, 500);
  assert.deepEqual(await bad.json(), { detail: 'solver crashed' }, 'the caller can still read the body');
  await assert.rejects(() => fetch(`${API}/api/net`), /Failed to fetch/);
  const f = C.failedCalls();
  assert.deepEqual(f.map((c) => [c.method, c.path, c.status, c.message]), [
    ['POST', '/api/boom', 500, 'solver crashed'],
    ['GET', '/api/net', 0, 'Failed to fetch'],
  ]);
  const blob = JSON.stringify(f);
  assert.ok(!blob.includes(TOKEN) && !blob.includes('hunter2') && !blob.includes('Bearer'));
  assert.equal(calls.length, 3, 'every request still went out exactly once');
});

const INPUT = {
  machine: { name: 'L12', die: 'cat:L12', configuration: 'L12' },
  preset: 'L12', presetModified: true,
  knobs: { stackLength_mm: 12.3456, turnsPerSlot: 7, wire_mm: 0.6, connection: '2S', current_A_rms: 40.123456, speed_rpm: 13000 },
  drive: { mode: 'pwm', transistor: 'GS66516T', pwm_kHz: 48 },
  propeller: { name: 'FPV 10x5', ambient_C: 25, load: 'propeller' },
  battery: { cells: 6, cellV: [3, 3.7, 4.2], pack_V: [18, 25.2], edited: false },
  tiles: { torque_Nm: 1.23456, efficiency_pct: 91.234, junctionTemp_C: null, totalLoss_W: NaN, power_kW: 0 },
  warnings: ['Refused: 12500 rpm is outside the computed range 1000–11000 rpm', 'x'.repeat(400)],
};

t('the Configure snapshot rounds, drops blanks and keeps the red lines', () => {
  const s = C.buildConfigureSnapshot(INPUT);
  assert.equal(s.machine.name, 'L12');
  assert.ok(!('fullCard' in s.machine), 'unknown stays out instead of guessing');
  assert.equal(s.configure.knobs.stackLength_mm, 12.3);
  assert.equal(s.configure.knobs.current_A_rms, 40.1);
  assert.deepEqual(s.configure.tiles, { torque_Nm: 1.23, efficiency_pct: 91.2, power_kW: 0 });
  assert.equal(s.configure.drive.mode, 'pwm');
  assert.equal(s.configure.drive.pwm_kHz, 48);
  assert.equal(s.configure.presetModified, true);
  assert.equal(s.configure.warnings[0].startsWith('Refused: 12500 rpm'), true);
  assert.ok(s.configure.warnings[1].length <= 200);
  assert.ok(!('editedByUser' in s.configure.battery));
  const withCard = C.buildConfigureSnapshot({ ...INPUT, machine: { ...INPUT.machine, fullCard: true } });
  assert.equal(withCard.machine.fullCard, true);
});

t('the context carries Configure values only while the user is on Configure', () => {
  reset();
  C.setConfigureSnapshot(C.buildConfigureSnapshot(INPUT));
  const base = { lang: 'en', appVersion: '1.2.3', appGitSha: '6a2ca6b', userAgent: 'Mozilla/5.0 Chrome/130' };
  const on = C.buildSupportContext({ ...base, tab: 'compare' });
  assert.equal(on.tabLabel, 'Configure');
  assert.equal(on.configure.knobs.speed_rpm, 13000);
  assert.equal(on.machine.name, 'L12');
  assert.deepEqual(on.app, { version: '1.2.3', gitSha: '6a2ca6b' });
  const off = C.buildSupportContext({ ...base, tab: 'motors' });
  assert.equal(off.tabLabel, 'Motors');
  assert.ok(!('configure' in off) && !('machine' in off));
  C.setConfigureSnapshot(null);
  assert.ok(!('configure' in C.buildSupportContext({ ...base, tab: 'compare' })));
});

t('the context includes the recent failed calls and stays compact', () => {
  reset();
  C.recordFailedCall({ url: `${API}/api/catalog/x/configure_context`, status: 502, message: 'bad gateway' }, API, 0);
  C.setConfigureSnapshot(C.buildConfigureSnapshot(INPUT));
  const ctx = C.buildSupportContext({ tab: 'compare', lang: 'zh-CN', appVersion: '1', appGitSha: 'abc', userAgent: 'u'.repeat(900), now: 3000 });
  assert.equal(ctx.failedCalls[0].path, '/api/catalog/x/configure_context');
  assert.equal(ctx.failedCalls[0].agoS, 3);
  assert.equal(ctx.browser.length, 200);
  assert.ok(JSON.stringify(ctx).length < 3000, 'compact');
  assert.ok(!/token|password|cookie|authorization/i.test(JSON.stringify(ctx)));
});
