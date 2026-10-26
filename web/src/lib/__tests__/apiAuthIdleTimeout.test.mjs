import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import vm from 'node:vm';

const require = createRequire(import.meta.url);
const ts = require('typescript');
const source = readFileSync(new URL('../apiAuth.ts', import.meta.url), 'utf8');

function loadAuth(window, apiUrl) {
  window.location ??= { href: 'http://localhost:3000/' };
  const configured = apiUrl === undefined ? 'undefined' : JSON.stringify(apiUrl);
  const compiled = ts.transpileModule(
    source.replace('import.meta.env.VITE_API_URL', configured),
    { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } },
  ).outputText;
  const module = { exports: {} };
  const context = {
    module, exports: module.exports, window, Headers, Response, Request, URL, CustomEvent,
    setTimeout, clearTimeout,
    require(id) {
      if (id === './sharedGet') return { sharedGet: (orig, _api, input, init) => orig(input, init) };
      if (id === './accessUi') return { refusalReasonKey: () => null };
      if (id === './platformAgreement') return { handleAgreementRequired: async (res) => res };
      throw new Error(`Unexpected import ${id}`);
    },
  };
  vm.runInNewContext(compiled, context);
  return module.exports;
}

test('idle_timeout suppresses the old token on later background fetches and preserves a new login', async () => {
  const calls = [];
  let token = 'dead-session-token';
  const idleReplies = [true, false, false];
  const window = {
    __fetchAuthInstalled: false,
    fetch: async (input, init) => {
      const headers = new Headers(init?.headers ?? (input instanceof Request ? input.headers : undefined));
      calls.push(headers.get('Authorization'));
      return idleReplies[calls.length - 1]
        ? new Response(JSON.stringify({ reason: 'idle_timeout' }), { status: 401, headers: { 'content-type': 'application/json' } })
        : new Response('{}', { status: 200 });
    },
    dispatchEvent() {},
  };
  const auth = loadAuth(window);
  auth.setTokenGetter(async () => token);
  auth.installFetchAuth();
  await window.fetch('http://localhost:8001/api/notices');
  await window.fetch('http://localhost:8001/api/activity/heartbeat');
  assert.deepEqual(calls.slice(0, 2), ['Bearer dead-session-token', null]);
  token = null;
  await window.fetch('http://localhost:8001/api/admin/live', { headers: { Authorization: 'Bearer dead-session-token' } });
  assert.equal(calls[2], null);
  token = 'new-session-token';
  await window.fetch('http://localhost:8001/api/notices');
  assert.equal(calls[3], 'Bearer new-session-token');
});

test('header-only idle close is latched without a listener and dispatched once', async () => {
  const replies = [];
  const events = [];
  const window = {
    __fetchAuthInstalled: false,
    fetch: async (_input, init) => {
      replies.push(new Headers(init?.headers).get('Authorization'));
      return new Response('{}', { status: 200, headers: { 'x-auth-reason': 'idle_timeout' } });
    },
    dispatchEvent(event) { events.push(event.type); },
  };
  const auth = loadAuth(window);
  auth.setTokenGetter(async () => 'header-idle-token');
  auth.installFetchAuth();
  await window.fetch('http://localhost:8001/api/one');
  await window.fetch('http://localhost:8001/api/two');
  assert.deepEqual(replies, ['Bearer header-idle-token', null]);
  assert.equal(auth.isIdleClosedToken('header-idle-token'), true);
  assert.deepEqual(events, [auth.AUTH_IDLE_EVENT]);
});

test('a delayed old response cannot close a fresh sign-in', async () => {
  let releaseOld;
  let token = 'old-token';
  const events = [];
  const window = {
    __fetchAuthInstalled: false,
    fetch: async () => new Promise((resolve) => { releaseOld = resolve; }),
    dispatchEvent(event) { events.push(event.type); },
  };
  const auth = loadAuth(window);
  auth.setTokenGetter(async () => token);
  auth.installFetchAuth();
  const oldRequest = window.fetch('http://localhost:8001/api/slow');
  while (!releaseOld) await Promise.resolve();
  token = 'fresh-token';
  releaseOld(new Response('{}', { status: 200, headers: { 'x-auth-reason': 'idle_timeout' } }));
  await oldRequest;
  assert.equal(auth.isIdleClosedToken('old-token'), true);
  assert.deepEqual(events, []);
});

test('a close arriving while token lookup waits prevents the old bearer from being sent', async () => {
  let releaseLookup;
  let getterCalls = 0;
  const sent = [];
  const window = {
    __fetchAuthInstalled: false,
    fetch: async (_input, init) => {
      sent.push(new Headers(init?.headers).get('Authorization'));
      return sent.length === 1
        ? new Response('{}', { status: 200, headers: { 'x-auth-reason': 'idle_timeout' } })
        : new Response('{}', { status: 200 });
    },
    dispatchEvent() {},
  };
  const auth = loadAuth(window);
  auth.setTokenGetter(async () => {
    getterCalls += 1;
    if (getterCalls === 1) return new Promise((resolve) => { releaseLookup = resolve; });
    return 'racing-token';
  });
  auth.installFetchAuth();
  const waiting = window.fetch('http://localhost:8001/api/waiting');
  await Promise.resolve();
  await window.fetch('http://localhost:8001/api/close-now', { headers: { Authorization: 'Bearer racing-token' } });
  releaseLookup('racing-token');
  await waiting;
  assert.deepEqual(sent, ['Bearer racing-token', null]);
});

test('Request headers are stripped after idle close and lookalike origins never receive auth', async () => {
  const calls = [];
  const window = {
    __fetchAuthInstalled: false,
    fetch: async (input, init) => {
      calls.push({ url: String(input instanceof Request ? input.url : input), auth: new Headers(init?.headers ?? (input instanceof Request ? input.headers : undefined)).get('Authorization') });
      return calls.length === 1
        ? new Response('{}', { status: 200, headers: { 'x-auth-reason': 'idle_timeout' } })
        : new Response('{}', { status: 200 });
    },
    dispatchEvent() {},
  };
  const auth = loadAuth(window);
  auth.setTokenGetter(async () => 'request-token');
  auth.installFetchAuth();
  await window.fetch('http://localhost:8001/api/close');
  await window.fetch(new Request('http://localhost:8001/api/request', { headers: { Authorization: 'Bearer request-token' } }));
  await window.fetch('http://localhost:8001.evil.example/api/steal');
  assert.deepEqual(calls.map((call) => call.auth), ['Bearer request-token', null, null]);
});

test('empty production API base authenticates same-origin relative requests only', async () => {
  const calls = [];
  const window = {
    __fetchAuthInstalled: false,
    location: { href: 'https://emotres.com/configure' },
    fetch: async (input, init) => {
      calls.push(new Headers(init?.headers).get('Authorization'));
      return new Response('{}', { status: 200 });
    },
    dispatchEvent() {},
  };
  const auth = loadAuth(window, '');
  auth.setTokenGetter(async () => 'production-token');
  auth.installFetchAuth();
  await window.fetch('/api/me');
  await window.fetch('https://emotres.com/api/notices');
  await window.fetch('//emotres.com/api/activity/heartbeat');
  await window.fetch('https://third-party.example/api/me');
  await window.fetch('//third-party.example/api/me');
  assert.deepEqual(calls, ['Bearer production-token', 'Bearer production-token', 'Bearer production-token', null, null]);
});

test('relative page requests are not treated as a separately hosted API', async () => {
  const calls = [];
  const window = {
    __fetchAuthInstalled: false,
    location: { href: 'https://app.example.test/configure' },
    fetch: async (input, init) => {
      calls.push(new Headers(init?.headers).get('Authorization'));
      return new Response('{}', { status: 200 });
    },
    dispatchEvent() {},
  };
  const auth = loadAuth(window, 'https://api.example.test');
  auth.setTokenGetter(async () => 'separate-api-token');
  auth.installFetchAuth();
  await window.fetch('/api/me');
  await window.fetch('//api.example.test/api/me');
  await window.fetch('https://api.example.test/api/me');
  await window.fetch('https://api.example.test.evil.example/api/me');
  assert.deepEqual(calls, [null, 'Bearer separate-api-token', 'Bearer separate-api-token', null]);
});
