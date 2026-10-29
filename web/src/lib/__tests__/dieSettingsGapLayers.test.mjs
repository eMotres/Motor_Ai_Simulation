/**
 * node --test — lib/dieSettings.ts: the configured air-gap layers open
 * (owner 2026-10-29).
 *
 * Until then a run whose Coulomb ring check failed was re-solved at more gap
 * layers and the passing level was written into `mesh.gapLayers` (with a
 * `mesh.gapLayersNote`), then remembered per die.  On the next duty load the
 * die memory overrode the duty's own `mesh.gapLayers` (1/side) and the Mesh
 * tab opened at 4/side.  A remembered block carrying the auto-adopt note no
 * longer restores its gap layers; an engineer's own setting still does.
 */
import test from 'node:test';
import assert from 'node:assert/strict';

const store = new Map();
globalThis.localStorage = {
  getItem: (k) => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => store.set(k, String(v)),
  removeItem: (k) => store.delete(k),
  key: (i) => [...store.keys()][i] ?? null,
  get length() { return store.size; },
};
const D = await import('../dieSettings.ts');

function remember(die, block) {
  store.clear();
  for (const [k, v] of Object.entries(block)) store.set(k, JSON.stringify(v));
  D.rememberDieSettings(die);
}

test('an auto-adopted gap level in the die memory does not override the duty', () => {
  // what the retired auto-adopt left behind: level + note
  remember('DIE A', { 'mesh.gapLayers': 4, 'mesh.gapLayersNote': 'gap layers 1→4 after ring mismatch',
                      'mesh.meshSize': 1.0 });
  const saved = JSON.parse(store.get('family.dieSettings'))['DIE A'].settings;
  assert.equal('mesh.gapLayersNote' in saved, false);   // the note is never remembered now…
  // …so put an old-style block back exactly as it sits in a browser today
  const s = JSON.parse(store.get('family.dieSettings'));
  s['DIE A'].settings['mesh.gapLayersNote'] = 'gap layers 1→4 after ring mismatch';
  store.clear();
  store.set('family.dieSettings', JSON.stringify(s));
  store.set('mesh.gapLayers', JSON.stringify(1));          // the duty's configured value
  store.set('mesh.gapLayersNote', JSON.stringify('stale'));
  D.restoreDieMeshSettings('DIE A');
  assert.equal(JSON.parse(store.get('mesh.gapLayers')), 1);
  assert.equal(JSON.parse(store.get('mesh.meshSize')), 1.0);  // the rest of the mesh memory stands
  assert.equal(store.has('mesh.gapLayersNote'), false);
});

test("an engineer's own gap setting (no auto note) is still restored", () => {
  remember('DIE B', { 'mesh.gapLayers': 2, 'mesh.meshSize': 0.8 });
  store.set('mesh.gapLayers', JSON.stringify(1));
  D.restoreDieMeshSettings('DIE B');
  assert.equal(JSON.parse(store.get('mesh.gapLayers')), 2);
});
