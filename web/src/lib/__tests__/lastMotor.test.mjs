import test from 'node:test';
import assert from 'node:assert/strict';

class Storage {
  values = new Map();
  getItem(k) { return this.values.has(k) ? this.values.get(k) : null; }
  setItem(k, v) { this.values.set(k, String(v)); }
  removeItem(k) { this.values.delete(k); }
}

globalThis.localStorage = new Storage();
globalThis.__lastMotorTestAuth = { user: null, token: 'account-token' };
globalThis.__lastMotorTestEffects = { store: [], apply: [] };
globalThis.window = { dispatchEvent() {} };
globalThis.CustomEvent = class CustomEvent { constructor(type, init) { this.type = type; this.detail = init?.detail; } };
const { build } = await import('esbuild');
const bundle = await build({
  entryPoints: [new URL('../lastMotor.ts', import.meta.url).pathname],
  bundle: true, platform: 'node', format: 'esm', write: false,
  define: { 'import.meta.env': JSON.stringify({ VITE_API_URL: 'http://test-api' }) },
  plugins: [{
    name: 'last-motor-test-dependencies',
    setup(buildApi) {
      const modules = new Map();
      const virtual = (filter, name, contents) => {
        modules.set(name, contents);
        buildApi.onResolve({ filter }, () => ({ path: name, namespace: 'last-motor-test' }));
      };
      virtual(/^\.\/localAuth$/, 'localAuth', `
        export const getStoredToken = () => globalThis.__lastMotorTestAuth.token;
        export const getStoredUser = () => globalThis.__lastMotorTestAuth.user;
      `);
      virtual(/^\.\/dutySettings$/, 'dutySettings', `
        export const clearActiveDuty = () => {};
        export const clearDutyMaterialsKeys = () => {};
      `);
      virtual(/^react$/, 'react', 'export const useSyncExternalStore = () => {};');
      virtual(/^\.\/dutyLocalApply$/, 'dutyLocalApply', `
        export const applyDutyLocal = async (...args) => { globalThis.__lastMotorTestEffects.apply.push(args); return {}; };
      `);
      virtual(/^\.\.\/stores\/motorStore$/, 'motorStore', `
        export const useMotorStore = { setState(value) { globalThis.__lastMotorTestEffects.store.push(value); } };
      `);
      buildApi.onLoad({ filter: /.*/, namespace: 'last-motor-test' }, ({ path }) => ({
        contents: modules.get(path), loader: 'js',
      }));
    },
  }],
});
const js = bundle.outputFiles[0].text;
const M = await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`);

const setIdentity = (email) => {
  globalThis.__lastMotorTestAuth.user = email ? { email, name: email, role: 'user' } : null;
  globalThis.__lastMotorTestAuth.token = 'account-token';
};

test('last motor local memory is namespaced and never migrates a global legacy context', () => {
  setIdentity('a@example.com');
  const a = { ref_id: 'cat_ciano', die: 'CIANO14 40 new', config: 'L12', duty: 'peak' };
  M.writeLocalLastMotor('A@example.com', a);
  assert.deepEqual(M.readLocalLastMotor('a@example.com'), a);
  assert.equal(M.readLocalLastMotor('b@example.com'), null);
  localStorage.setItem('family.localContext', JSON.stringify({ die: 'owner', config: '85mm', duty: 'peak' }));
  assert.equal(M.readLocalLastMotor('new@example.com'), null);
});

test('Configure converts raw catalog IDs to its cat: reference IDs explicitly', () => {
  assert.equal(M.configureRefIdForSelection({ ref_id: 'cat_ciano', die: 'd', config: 'L12', duty: 'peak' }), 'cat:cat_ciano');
  assert.equal(M.configureRefIdForSelection({ ref_id: null, die: 'personal', config: 'L12', duty: 'peak' }), null);
});

test('selection epochs reject stale same-account loads and account changes', () => {
  setIdentity('a@example.com');
  const first = M.beginLastMotorSelection('a@example.com');
  assert.equal(M.isCurrentLastMotorOperation('a@example.com', first), true);
  const second = M.beginLastMotorSelection('a@example.com');
  assert.equal(M.isCurrentLastMotorOperation('a@example.com', first), false);
  assert.equal(M.isCurrentLastMotorOperation('a@example.com', second), true);
  setIdentity('b@example.com');
  assert.equal(M.isCurrentLastMotorOperation('a@example.com', second), false);
});

test('last motor API rejects 401 instead of treating it as a shared preference', async () => {
  setIdentity('a@example.com');
  const before = globalThis.fetch;
  let sentHeaders;
  globalThis.fetch = async (_url, init) => {
    sentHeaders = init.headers;
    return new Response('{}', { status: 401 });
  };
  try {
    await assert.rejects(M.fetchLastMotor('a@example.com'), /HTTP 401/);
    assert.equal(sentHeaders.Authorization, 'Bearer account-token');
  } finally { globalThis.fetch = before; }
});

test('revoked server selection is not restored from a stale local copy', async () => {
  setIdentity('a@example.com');
  const stale = { ref_id: 'cat_ciano', die: 'CIANO14 40 new', config: 'L12', duty: 'peak' };
  M.writeLocalLastMotor('a@example.com', stale);
  const before = globalThis.fetch;
  const urls = [];
  globalThis.fetch = async (url) => {
    urls.push(String(url));
    return new Response(JSON.stringify({ selection: null, unavailable: true }), { status: 200 });
  };
  try {
    const state = await M.restoreLastMotor('a@example.com');
    assert.equal(state.status, 'unavailable');
    assert.deepEqual(state.selection, stale);
    assert.equal(urls.length, 1);
    assert.match(urls[0], /\/api\/me\/last_motor$/);
  } finally { globalThis.fetch = before; }
});

test('restores the selected L12 peak payload and applies its exact duty locally', async () => {
  setIdentity('a@example.com');
  globalThis.__lastMotorTestEffects = { store: [], apply: [] };
  const selection = { ref_id: 'cat_ciano', die: 'CIANO14 40 new', config: 'L12', duty: 'peak' };
  const payload = { die: selection.die, config: 'L12', geometry: { slots: 12, poles: 14 },
    duty: { name: 'peak', sim: { rpm: 14400 } }, materials: {} };
  const before = globalThis.fetch;
  globalThis.fetch = async (url) => String(url).endsWith('/api/me/last_motor')
    ? new Response(JSON.stringify({ selection, unavailable: false }), { status: 200 })
    : new Response(JSON.stringify(payload), { status: 200 });
  try {
    const state = await M.restoreLastMotor('a@example.com');
    assert.equal(state.status, 'loaded');
    assert.deepEqual(globalThis.__lastMotorTestEffects.store, [{ geometry: payload.geometry }]);
    assert.equal(globalThis.__lastMotorTestEffects.apply.length, 1);
    const [die, config, duty, appliedPayload, prev, canWrite, isCurrent] = globalThis.__lastMotorTestEffects.apply[0];
    assert.deepEqual([die, config, duty, appliedPayload, prev, canWrite],
      [selection.die, 'L12', 'peak', payload, null, false]);
    assert.equal(isCurrent(), true);
  } finally { globalThis.fetch = before; }
});

test('restores a valid no-reference motor selection without inventing a catalog model', async () => {
  setIdentity('a@example.com');
  globalThis.__lastMotorTestEffects = { store: [], apply: [] };
  const selection = { ref_id: null, die: 'personal motor', config: 'L12', duty: null };
  const payload = { die: selection.die, config: 'L12', geometry: { slots: 12, poles: 14 }, materials: {} };
  const before = globalThis.fetch;
  globalThis.fetch = async (url) => String(url).endsWith('/api/me/last_motor')
    ? new Response(JSON.stringify({ selection, unavailable: false }), { status: 200 })
    : new Response(JSON.stringify(payload), { status: 200 });
  try {
    const state = await M.restoreLastMotor('a@example.com');
    assert.equal(state.status, 'loaded');
    assert.deepEqual(state.selection, selection);
    assert.deepEqual(globalThis.__lastMotorTestEffects.store, [{ geometry: payload.geometry }]);
    assert.equal(globalThis.__lastMotorTestEffects.apply.length, 0);
  } finally { globalThis.fetch = before; }
});

test('a delayed family payload cannot apply geometry after a newer selection starts', async () => {
  setIdentity('a@example.com');
  globalThis.__lastMotorTestEffects = { store: [], apply: [] };
  const selection = { ref_id: 'cat_ciano', die: 'CIANO14 40 new', config: 'L12', duty: 'peak' };
  const before = globalThis.fetch;
  let enteredPayload;
  let releasePayload;
  const entered = new Promise((resolve) => { enteredPayload = resolve; });
  const waiting = new Promise((resolve) => { releasePayload = resolve; });
  globalThis.fetch = async (url) => {
    if (String(url).endsWith('/api/me/last_motor'))
      return new Response(JSON.stringify({ selection, unavailable: false }), { status: 200 });
    enteredPayload();
    await waiting;
    return new Response(JSON.stringify({ die: selection.die, config: 'L12',
      geometry: { slots: 12, poles: 14 }, duty: { name: 'peak' }, materials: {} }), { status: 200 });
  };
  try {
    const restoring = M.restoreLastMotor('a@example.com');
    await entered;
    M.beginLastMotorSelection('a@example.com');
    releasePayload();
    await assert.rejects(restoring, /account changed/);
    assert.deepEqual(globalThis.__lastMotorTestEffects.store, []);
    assert.deepEqual(globalThis.__lastMotorTestEffects.apply, []);
  } finally { globalThis.fetch = before; }
});
