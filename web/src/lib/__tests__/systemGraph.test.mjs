// node --test - the System graph helpers (Simulations > System): loop nesting and boxes, colours, badges, and the
// layout (no node of another group inside a loop's box).  Imports lib/systemGraph.ts + systemGraphLayout.ts (Node >= 22.18 / 24).
import test from 'node:test';
import assert from 'node:assert/strict';

let G = null; let L = null;
try { G = await import('../systemGraph.ts'); L = await import('../systemGraphLayout.ts'); } catch { /* old Node */ }
const t = G ? test : test.skip;

const loop = (id, nodes) => ({ id, kind: 'fixed_point', title: id, nodes, edges: [], max_iterations: null, tolerance: null, tolerance_unit: null });
const node = (id) => ({ id, kind: 'compute', title: id, summary: '', inputs: [], outputs: [], source: null, status: { state: 'unknown' } });
const edge = (a, b, feedback = false) => ({ id: `${a}->${b}`, from: { node: a, port: null }, to: { node: b, port: null }, type: 'x', feedback, via: 'data' });

t('splitLoops keeps a nested family and sets a partly overlapping loop aside', () => {
  const big = loop('big', ['a', 'b', 'c', 'd']);
  const inner = loop('inner', ['b', 'c']);
  const clash = loop('clash', ['c', 'x']);
  const { boxed, unboxed } = G.splitLoops([inner, clash, big]);
  assert.deepEqual(boxed.map((l) => l.id).sort(), ['big', 'inner']);
  assert.deepEqual(unboxed.map((l) => l.id), ['clash']);
  assert.deepEqual(G.nesting(boxed).parent, { big: null, inner: 'big' });
});

t('wire colours: known types fixed, others stable per name', () => {
  assert.equal(G.typeColor('MeshIR'), G.typeColor('MeshIR'));
  assert.notEqual(G.typeColor('MeshIR'), G.typeColor('GeometryIR'));
  assert.equal(G.typeColor('some_new_type'), G.typeColor('some_new_type'));
});

t('badge and time text', () => {
  assert.equal(G.loopBadge({ ...loop('l', []), max_iterations: 400, tolerance: 0.01, tolerance_unit: 'K' }), '≤400 it · tol 0.01 K');
  assert.equal(G.loopBadge(loop('l', [])), '');
  assert.equal(G.formatTime(0.25), '250 ms');
  assert.equal(G.formatTime(75), '1 min 15 s');
  assert.equal(G.formatTime(null), '');
});

const lt = L ? test : test.skip;
lt('layout: a loop box holds exactly its members; nested boxes sit inside their parent', () => {
  const ids = ['in', 'a', 'b', 'c', 'd', 'out', 'side'];
  const doc = {
    nodes: ids.map(node),
    edges: [edge('in', 'a'), edge('a', 'b'), edge('b', 'c'), edge('c', 'd'), edge('d', 'out'), edge('in', 'side'), edge('side', 'out'), edge('c', 'b', true)],
    loops: [loop('outer', ['a', 'b', 'c', 'd']), loop('inner', ['b', 'c'])],
  };
  const lay = L.layoutGraph(doc);
  const inside = (b, box) => b.x >= box.x && b.y >= box.y && b.x + b.w <= box.x + box.w && b.y + b.h <= box.y + box.h;
  const overlap = (b, box) => b.x < box.x + box.w && b.x + b.w > box.x && b.y < box.y + box.h && b.y + b.h > box.y;
  for (const lp of doc.loops) {
    for (const n of ids) {
      if (lp.nodes.includes(n)) assert.ok(inside(lay.nodes[n], lay.loops[lp.id]), `${n} in ${lp.id}`);
      else assert.ok(!overlap(lay.nodes[n], lay.loops[lp.id]), `${n} outside ${lp.id}`);
    }
  }
  assert.ok(inside(lay.loops.inner, lay.loops.outer));
  assert.deepEqual(lay.unboxed, []);
});
