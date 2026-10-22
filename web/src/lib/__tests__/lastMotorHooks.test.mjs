import test from 'node:test';
import assert from 'node:assert/strict';
import { registerHooks } from 'node:module';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';

class Storage {
  values = new Map();
  getItem(key) { return this.values.get(key) ?? null; }
  setItem(key, value) { this.values.set(key, String(value)); }
  removeItem(key) { this.values.delete(key); }
}
globalThis.localStorage = new Storage();
globalThis.__lastMotorHarness = { user: { email: 'a@example.com' }, applied: [] };
globalThis.window = { dispatchEvent() {} };
globalThis.CustomEvent = class CustomEvent { constructor(type, init) { this.type = type; this.detail = init?.detail; } };

const stubs = {
  'react': 'export const useSyncExternalStore = (_s, get) => get();',
  './localAuth': `export const getStoredToken=()=>"token"; export const getStoredUser=()=>globalThis.__lastMotorHarness.user;`,
  './dutySettings': 'export const clearActiveDuty=()=>{}; export const clearDutyMaterialsKeys=()=>{};',
  './dutyLocalApply': `export const applyDutyLocal=async (...args)=>{globalThis.__lastMotorHarness.applied.push(args);return {};};`,
  '../stores/motorStore': 'export const useMotorStore={setState(){}};',
};
const stubUrls = new Map(Object.entries(stubs).map(([k, v]) => [k, `data:text/javascript,${encodeURIComponent(v)}`]));
const hook = registerHooks({
  resolve(specifier, context, nextResolve) {
    if (context.parentURL?.endsWith('/lastMotor.ts') && stubUrls.has(specifier))
      return { url: stubUrls.get(specifier), shortCircuit: true };
    if (specifier === '../types/motor' && context.parentURL?.endsWith('/lastMotor.ts'))
      return { url: 'data:text/javascript,export%20%7B%7D', shortCircuit: true };
    if (specifier.startsWith('.') && context.parentURL?.startsWith('file:') && !/\.[a-z]+$/i.test(specifier)) {
      for (const ext of ['.ts', '.tsx', '.js']) {
        try { return nextResolve(`${specifier}${ext}`, context); } catch { /* try next */ }
      }
    }
    return nextResolve(specifier, context);
  },
  load(url, context, nextLoad) {
    if (/\/lastMotor\.ts$/.test(new URL(url).pathname)) {
      const source = readFileSync(fileURLToPath(url), 'utf8')
        .replaceAll('import.meta.env.VITE_API_URL', JSON.stringify('http://test-api'));
      return { format: 'module', shortCircuit: true, source: ts.transpileModule(source, {
        compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
      }).outputText };
    }
    return nextLoad(url, context);
  },
});
const M = await import('../lastMotor.ts');

test('actual lastMotor module persists B and restores B from the server selection', async () => {
  const before = globalThis.fetch;
  const originalGlobals = { storage: globalThis.localStorage, window: globalThis.window,
    customEvent: globalThis.CustomEvent, harness: globalThis.__lastMotorHarness };
  let serverSelection = { ref_id: 'cat-a', die: 'A', config: 'L8', duty: 'peak' };
  const expectedB = { ref_id: 'cat-b', die: 'B', config: 'L9', duty: 'continuous' };
  const payload = { die: 'B', config: 'L9', geometry: { slots: 9 }, duty: { name: 'continuous' }, materials: {} };
  const calls = [];
  const puts = [];
  const gets = [];
  globalThis.fetch = async (url, init = {}) => {
    calls.push([String(url), init.method ?? 'GET', init.body]);
    if (String(url).endsWith('/api/catalog/references'))
      return new Response(JSON.stringify({ motors: [{ id: 'cat-b', card: { die: 'B', config: 'L9' } }] }), { status: 200 });
    if (String(url).endsWith('/api/me/last_motor') && init.method !== 'PUT') {
      gets.push(serverSelection);
      return new Response(JSON.stringify({ selection: serverSelection, unavailable: false }), { status: 200 });
    }
    if (String(url).includes('/api/family/payload/')) return new Response(JSON.stringify(payload), { status: 200 });
    if (String(url).endsWith('/api/me/last_motor') && init.method === 'PUT') {
      const body = JSON.parse(init.body);
      puts.push(body);
      serverSelection = body.selection;
      return new Response(JSON.stringify({ selection: serverSelection }), { status: 200 });
    }
    throw new Error(`unexpected request ${url}`);
  };
  try {
    await M.rememberLoadedMotor('a@example.com', 'B', 'L9', 'continuous');
    assert.deepEqual(puts[0], { selection: expectedB });
    assert.deepEqual(serverSelection, expectedB);
    assert.deepEqual(M.readLocalLastMotor('a@example.com'), expectedB);
    M.writeLocalLastMotor('a@example.com', { ref_id: 'cat-a', die: 'A', config: 'L8', duty: 'peak' });
    globalThis.__lastMotorHarness.applied.length = 0;
    const restored = await M.restoreLastMotor('a@example.com');
    assert.equal(restored.status, 'loaded');
    assert.deepEqual(gets, [expectedB]);
    assert.deepEqual(restored.selection, expectedB);
    assert.equal(globalThis.__lastMotorHarness.applied[0][0], 'B');
    assert.equal(globalThis.__lastMotorHarness.applied[0][1], 'L9');
    assert.equal(globalThis.__lastMotorHarness.applied[0][2], 'continuous');
    assert.ok(calls.some(([url, method]) => url.endsWith('/api/me/last_motor') && method === 'PUT'));
    assert.ok(calls.some(([url]) => url.includes('/api/family/payload/B/L9?duty=continuous')));
  } finally {
    globalThis.fetch = before;
    globalThis.localStorage = originalGlobals.storage;
    globalThis.window = originalGlobals.window;
    globalThis.CustomEvent = originalGlobals.customEvent;
    globalThis.__lastMotorHarness = originalGlobals.harness;
    hook.deregister();
  }
});
