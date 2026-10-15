// node --test - THE assembly tree model (lib/assemblyTree.ts): the server part tree + the nodes of the loaded GLB become the rows of the
// ONE tree component ("Motor Assembly"): groups > parts > instances, kit filtering (server side, here only honoured), visibility state,
// isolate, selection -> viewer highlight.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, existsSync, readdirSync, statSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

let A = null;
try { A = await import('../assemblyTree.ts'); } catch { /* old Node */ }
const t = A ? test : test.skip;

const fmt = (_k, _v, f) => f;
const P = (id, group, en, em, glb, extra = {}) => ({ id, group, name: en, names: { en, zh: en + '(zh)' }, em, glb, order: 0, ...extra });
const TREE = {
  groups: [
    { id: 'stator', name: { en: 'Stator', zh: '定子' }, parts: ['stator_core'] },
    { id: 'winding', name: { en: 'Winding', zh: '绕组' }, parts: ['slot'] },
    { id: 'magnets', name: { en: 'Magnets', zh: '磁钢' }, parts: ['magnet'] },
    { id: 'housing', name: { en: 'Housing & holders', zh: '外壳与支架' }, parts: ['rings'] },
    { id: 'bearings', name: { en: 'Bearings', zh: '轴承' }, parts: ['bearings'] },
    { id: 'fasteners', name: { en: 'Fasteners', zh: '紧固件' }, parts: ['screws'] },
  ],
  parts: [
    P('stator_core', 'stator', 'Stator core', true, ['Core']),
    P('slot', 'winding', 'Winding copper', true, ['Coils']),
    P('magnet', 'magnets', 'Magnets', true, ['Magnets', 'Magnets_S']),
    P('rings', 'housing', 'Glass-fibre rings', false, ['Ring_front', 'Ring_back']),
    P('bearings', 'bearings', 'Bearings', false, ['Bearings_front', 'Bearings_front_balls', 'Bearings_back']),
    P('screws', 'fasteners', 'Screws', false, ['Screws']),
  ],
};
const kids = (prefix, n, w = 2) => Array.from({ length: n }, (_, i) => `${prefix}_${String(i + 1).padStart(w, '0')}`);
const NODES = [
  { name: 'Core', family: 'Core', explode: 0, children: ['Core'], colour: '#999999' },
  { name: 'Coils', family: 'Coils', explode: 0, children: kids('Coil', 6), colour: '#e0821a' },
  { name: 'Magnets', family: 'Magnets', explode: 0, children: kids('Magnet', 14).filter((_, i) => i % 2 === 0), colour: '#e02718' },
  { name: 'Magnets_S', family: 'Magnets', explode: 0, children: kids('Magnet', 14).filter((_, i) => i % 2 === 1), colour: '#2e86ff' },
  { name: 'Ring_front', family: 'Ring_front', explode: 0, children: ['Ring_front'], colour: '#d9cf73' },
  { name: 'Ring_back', family: 'Ring_back', explode: 0, children: ['Ring_back'], colour: '#d9cf73' },
  { name: 'Bearings_front', family: 'Bearings_front', explode: 0, children: ['x'], colour: '#777' },
  { name: 'Bearings_front_balls', family: 'Bearings_front', explode: 0, children: ['y'], colour: '#777' },
  { name: 'Bearings_back', family: 'Bearings_back', explode: 0, children: ['z'], colour: '#777' },
  { name: 'Screws', family: 'Screws', explode: 0, children: [1, 2, 3, 4, 5, 6].map((i) => `Screw_${i}`), colour: '#ccc' },
];
const rows = (hidden = new Set()) => A.buildAssemblyRows(TREE, NODES, { lang: 'en', fmt, hidden });
const find = (rs, key) => { for (const r of rs) { if (r.key === key) return r; const c = r.group && find(r.group, key); if (c) return c; } return null; };

t('the rows are the server groups > parts in the server order, groups open, parts tagged EM / not EM', () => {
  const rs = rows();
  assert.deepEqual(rs.map((r) => r.key), ['g:stator', 'g:winding', 'g:magnets', 'g:housing', 'g:bearings', 'g:fasteners']);
  assert.deepEqual(rs.map((r) => r.label), ['Stator', 'Winding', 'Magnets', 'Housing & holders', 'Bearings', 'Fasteners']);
  assert.ok(rs.every((r) => r.defaultOpen && r.visible));
  assert.deepEqual(rs[0].group.map((p) => [p.key, p.tag]), [['stator_core', 'EM']]);
  assert.equal(find(rs, 'rings').tag, 'notEM');
  assert.equal(find(rs, 'rings').partId, 'rings');
  // zh-CN names come from the same tree
  const zh = A.buildAssemblyRows(TREE, NODES, { lang: 'zh-CN', fmt, hidden: new Set() });
  assert.equal(zh[0].label, '定子');
  assert.equal(find(zh, 'stator_core').label, 'Stator core(zh)');
});

t('per-instance children come from the GLB nodes: Coil n, Magnet n N|S, Screw n, bearing front / rear', () => {
  const rs = rows();
  assert.deepEqual(find(rs, 'slot').group.map((c) => c.label), ['Coil 1', 'Coil 2', 'Coil 3', 'Coil 4', 'Coil 5', 'Coil 6']);
  const mag = find(rs, 'magnet').group.map((c) => c.label);
  assert.equal(mag.length, 14);
  assert.deepEqual(mag.slice(0, 4), ['Magnet 1 N', 'Magnet 2 S', 'Magnet 3 N', 'Magnet 4 S']);      // N and S nodes interleaved by number
  assert.equal(find(rs, 'magnet').group[0].colour, '#e02718');
  assert.equal(find(rs, 'magnet').group[1].colour, '#2e86ff');
  assert.deepEqual(find(rs, 'screws').group.map((c) => c.label), ['Screw 1', 'Screw 2', 'Screw 3', 'Screw 4', 'Screw 5', 'Screw 6']);
  assert.deepEqual(find(rs, 'bearings').group.map((c) => c.label), ['Bearing, front', 'Balls, front', 'Bearing, rear']);
  assert.deepEqual(find(rs, 'rings').group.map((c) => c.label), ['Ring, front', 'Ring, rear']);
  assert.equal(find(rs, 'stator_core').group, undefined);                                       // one drawn object: no instance rows
  assert.equal(find(rs, 'stator_core').colour, '#999999');                                      // the swatch is the colour the model draws
  assert.equal(find(rs, 'slot').group[0].key, 'n:Coil_01');
});

t('no model yet: the tree is still there (parts, no instances), swatches from the group palette', () => {
  const rs = A.buildAssemblyRows(TREE, [], { lang: 'en', fmt, hidden: new Set() });
  assert.equal(rs.length, 6);
  assert.ok(find(rs, 'slot').group === undefined && find(rs, 'slot').colour === A.GROUP_COLOURS.winding);
});

t('a frameless kit tree (the server filtered it) shows only the kit groups; nothing else is invented from the model nodes', () => {
  const kit = { groups: [TREE.groups[0], TREE.groups[1], TREE.groups[2]], parts: TREE.parts.slice(0, 3) };
  const rs = A.buildAssemblyRows(kit, NODES, { lang: 'en', fmt, hidden: new Set() });
  assert.deepEqual(rs.map((r) => r.key), ['g:stator', 'g:winding', 'g:magnets']);
  assert.equal(find(rs, 'screws'), null);
  assert.equal(find(rs, 'bearings'), null);
});

t('visibility is a set of hidden keys; a hidden group / part / instance hides exactly its GLB nodes', () => {
  assert.deepEqual([...A.hiddenNodeNames(TREE, new Set())], []);
  assert.deepEqual([...A.hiddenNodeNames(TREE, new Set(['g:housing']))], ['Ring_front', 'Ring_back']);
  assert.deepEqual([...A.hiddenNodeNames(TREE, new Set(['magnet']))].sort(), ['Magnets', 'Magnets_S']);
  assert.deepEqual([...A.hiddenNodeNames(TREE, new Set(['n:Coil_03']))], ['Coil_03']);
  assert.deepEqual([...A.hiddenNodeNames(null, new Set(['magnet']))], []);
  const h = A.toggled(new Set(), 'slot');
  assert.equal(find(rows(h), 'slot').visible, false);
  assert.equal(find(rows(A.toggled(h, 'slot')), 'slot').visible, true);
  const g = rows(new Set(['n:Coil_02']));
  assert.deepEqual(find(g, 'slot').group.map((c) => c.visible), [true, false, true, true, true, true]);
});

t('isolate keeps the branch (itself, descendants, ancestors) and hides the rest; again shows everything', () => {
  const rs = rows();
  const iso = A.isolated(rs, new Set(), 'slot');
  assert.equal(iso.has('slot'), false);
  assert.equal(iso.has('g:winding'), false);
  assert.equal(iso.has('n:Coil_01'), false);
  assert.ok(iso.has('stator_core') && iso.has('g:stator') && iso.has('magnet') && iso.has('n:Magnet_01') && iso.has('n:Screw_1'));
  assert.deepEqual([...A.isolated(rs, iso, 'slot')], []);
  const one = A.isolated(rs, new Set(), 'n:Coil_02');
  assert.equal(one.has('n:Coil_02'), false);
  assert.equal(one.has('slot'), false);                                                          // its part and group stay visible
  assert.equal(one.has('n:Coil_01'), true);
  assert.deepEqual([...A.branchOf(rs, 'g:magnets')].filter((k) => k.startsWith('n:')).length, 14);
});

t('selection maps to the nodes the viewer highlights: group, part or one instance', () => {
  assert.deepEqual([...A.highlightedNodeNames(TREE, 'n:Screw_3')], ['Screw_3']);
  assert.deepEqual([...A.highlightedNodeNames(TREE, 'bearings')], ['Bearings_front', 'Bearings_front_balls', 'Bearings_back']);
  assert.deepEqual([...A.highlightedNodeNames(TREE, 'g:magnets')], ['Magnets', 'Magnets_S']);
  assert.deepEqual([...A.highlightedNodeNames(TREE, null)], []);
  assert.deepEqual([...A.highlightedNodeNames(TREE, 'nonsense')], []);
});

t('labels of unknown nodes stay readable; every key is unique', () => {
  assert.equal(A.instanceLabel('PhaseBoard_pins_003', fmt), 'Pin 3');
  assert.equal(A.instanceLabel('Prop_fix', fmt), 'Prop fix');
  const ks = A.allKeys(rows());
  assert.equal(new Set(ks).size, ks.length);
});

t('there is ONE tree component: the checkbox PartTree is gone and every host draws ComponentTreeView', () => {
  const root = join(dirname(fileURLToPath(import.meta.url)), '..', '..');
  assert.equal(existsSync(join(root, 'components', 'common', 'PartTree.tsx')), false);
  const walk = (d) => readdirSync(d).flatMap((f) => (statSync(join(d, f)).isDirectory() ? (f === '__tests__' ? [] : walk(join(d, f))) : [join(d, f)]));
  const files = walk(join(root, 'components')).concat(walk(join(root, 'lib'))).filter((f) => /\.(tsx?|mjs)$/.test(f));
  for (const f of files) assert.ok(!/from '[^']*\/PartTree'/.test(readFileSync(f, 'utf8')), f);
  const uses = (rel) => readFileSync(join(root, 'components', rel), 'utf8').includes('ComponentTreeView');
  for (const rel of ['model3d/Model3DPanel.tsx', 'geometry/GeometryFullPanel.tsx', 'materials/FullPartsPanel.tsx', 'viewer3d/ComponentTree.tsx', 'common/FieldViewer.tsx']) {
    assert.ok(uses(rel), rel);
  }
});
