// node --test - the shared Active | Full construction scope (lib/constructionScope.ts) and the part-tree helpers (lib/partTree.ts).
import test from 'node:test';
import assert from 'node:assert/strict';

let S = null; let P = null;
try { S = await import('../constructionScope.ts'); P = await import('../partTree.ts'); } catch { /* old Node */ }
const t = S ? test : test.skip;

const mem = () => { const m = new Map(); return { getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, v), m }; };

t('two scopes, Active by default, anything else is Active', () => {
  assert.deepEqual(S.SCOPES.map((s) => s.id), ['active', 'full']);
  for (const bad of [undefined, null, '', 'FULL', 'garbage', 3]) assert.equal(S.parseScope(bad), 'active');
  assert.equal(S.parseScope('full'), 'full');
});

t('the choice is remembered per user and read back', () => {
  const st = mem();
  const a = S.createScopeStore(st);
  a.bindUser('Ann@Example.com');
  a.set('full');
  assert.equal(st.m.get(S.scopeKey('ann@example.com')), 'full');
  const b = S.createScopeStore(st);
  assert.equal(b.bindUser('ann@example.com'), 'full');
  assert.equal(b.bindUser('bob@example.com'), 'active');       // another user, nothing stored
  assert.equal(b.get(), 'active');
});

t('the old Materials mode is the starting value when the user has no choice yet', () => {
  const st = mem();
  st.setItem(S.LEGACY_SCOPE_KEY, 'full');
  const s = S.createScopeStore(st);
  assert.equal(s.get(), 'full');
  assert.equal(s.bindUser('new@user'), 'full');
  s.set('active');
  assert.equal(st.m.get(S.scopeKey('new@user')), 'active');      // an explicit choice wins from then on
  assert.equal(S.createScopeStore(st).bindUser('new@user'), 'active');
});

t('Geometry and Materials are ONE state: a change reaches every subscriber, a no-op change notifies nobody', () => {
  const s = S.createScopeStore(mem());
  let geometry = 0; let materials = 0;
  const u1 = s.subscribe(() => { geometry += 1; });
  s.subscribe(() => { materials += 1; });
  s.set('full');
  assert.deepEqual([geometry, materials, s.get()], [1, 1, 'full']);
  s.set('full');
  assert.deepEqual([geometry, materials], [1, 1]);
  u1();
  s.set('active');
  assert.deepEqual([geometry, materials], [1, 2]);
});

t('a storage that throws never breaks the switch', () => {
  const bad = { getItem() { throw new Error('blocked'); }, setItem() { throw new Error('blocked'); } };
  const s = S.createScopeStore(bad);
  assert.equal(s.get(), 'active');
  s.set('full');
  assert.equal(s.get(), 'full');
  assert.equal(S.createScopeStore(null).get(), 'active');
});

const tree = {
  groups: [{ id: 'stator', name: { en: 'Stator', zh: '定子' }, parts: ['stator_core'] },
    { id: 'housing', name: { en: 'Housing & holders', zh: '外壳与支架' }, parts: ['holder_front', 'rings'] }],
  parts: [
    { id: 'stator_core', group: 'stator', name: 'Stator core', names: { en: 'Stator core', zh: '定子铁芯' }, em: true, glb: ['Core'], order: 0, mass_kg: 0.03, counted: true },
    { id: 'holder_front', group: 'housing', name: 'Stator holder, front', names: { en: 'Stator holder, front', zh: '定子支架(前)' }, em: false, glb: ['Holder_front'], order: 1, mass_kg: 0.004, counted: true },
    { id: 'rings', group: 'housing', name: 'Glass-fibre rings', names: { en: 'Glass-fibre rings', zh: '玻璃纤维环' }, em: false, glb: ['Ring_front', 'Ring_back'], order: 2, mass_kg: 0.001, counted: true },
  ],
};

t('part tree helpers: names by language, order, group mass, viewer highlight', () => {
  assert.equal(P.nameOf(tree.parts[0].names, 'zh-CN'), '定子铁芯');
  assert.equal(P.nameOf(tree.parts[0].names, 'en'), 'Stator core');
  assert.equal(P.nameOf(tree.parts[0].names, 'de'), 'Stator core');
  assert.equal(P.nameOf({ en: '', zh: 'x' }, 'en'), 'x');
  assert.deepEqual(P.orderedIds(tree), ['stator_core', 'holder_front', 'rings']);
  assert.equal(P.groupMassKg(tree, 'housing'), 0.005);
  assert.equal(P.groupMassKg({ ...tree, parts: [] }, 'housing'), null);
  assert.deepEqual(P.highlightNodes(tree, { part: 'rings' }), ['Ring_front', 'Ring_back']);
  assert.deepEqual(P.highlightNodes(tree, { group: 'housing' }), ['Holder_front', 'Ring_front', 'Ring_back']);
  assert.deepEqual(P.highlightNodes(tree, {}), []);
  assert.deepEqual(P.highlightNodes(null, { part: 'rings' }), []);
  assert.deepEqual(P.initialOpen(tree), { stator: true, housing: true });
});
