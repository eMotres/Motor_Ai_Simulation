// node --test — the pure adoption rule of lib/panelSettings.ts, copied verbatim
// (the repo's convention for node tests: `node --test` cannot load the TS
// modules, so the pure function under test is re-stated here and kept in sync).
import test from 'node:test';
import assert from 'node:assert/strict';

function adoptSettings(current, server, keys) {
  const out = {};
  if (!server) return out;
  for (const k of keys) {
    if (!(k in server)) continue;
    const v = server[k];
    if (v === undefined) continue;
    const cur = current[k];
    if (cur !== undefined && cur !== null && typeof v !== typeof cur) continue;
    out[k] = v;
  }
  return out;
}

const KEYS = ['coolMode', 'flowLpm', 'eqTemp', 'contacts'];

test('server values win for every persisted key it has', () => {
  const cur = { coolMode: 'air', flowLpm: '8', eqTemp: true, contacts: { a: 1 } };
  const srv = { coolMode: 'liquid', flowLpm: '20', eqTemp: false, contacts: { a: 2 } };
  assert.deepEqual(adoptSettings(cur, srv, KEYS), srv);
});

test('keys the server never saw keep the browser value', () => {
  const cur = { coolMode: 'air', flowLpm: '8', eqTemp: true, contacts: {} };
  assert.deepEqual(adoptSettings(cur, { flowLpm: '12' }, KEYS), { flowLpm: '12' });
});

test('a value of the wrong shape is refused rather than adopted', () => {
  const cur = { coolMode: 'air', flowLpm: '8', eqTemp: true, contacts: {} };
  // a number where the store keeps a string, a string where it keeps a boolean
  assert.deepEqual(adoptSettings(cur, { flowLpm: 12, eqTemp: 'yes' }, KEYS), {});
});

test('no server answer adopts nothing', () => {
  assert.deepEqual(adoptSettings({ coolMode: 'air' }, null, KEYS), {});
  assert.deepEqual(adoptSettings({ coolMode: 'air' }, undefined, KEYS), {});
});

test('keys outside the persisted list are ignored even if the server sends them', () => {
  const cur = { coolMode: 'air' };
  assert.deepEqual(adoptSettings(cur, { coolMode: 'none', hydrated: true, field: {} }, ['coolMode']), { coolMode: 'none' });
});
