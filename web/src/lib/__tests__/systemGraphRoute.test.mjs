// node --test - the wire check of the System graph: nothing is drawn through a block, a loop group's header band (title,
// rule lines, exit tags), a loop box a wire has no business in, the Inputs label or the legend card; two wires of different
// nets never run on the same line.  The check is exercised on hand-made layouts (it must see each fault) and on the real
// graphs (fixtures = GET /api/system/graph output) where it must find nothing.  Imports lib/systemGraphRoute.ts (Node >= 22.18 / 24).
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

let V = null; let L = null; let R = null; let G = null;
try { V = await import('../systemGraphView.ts'); L = await import('../systemGraphLayout.ts'); R = await import('../systemGraphRoute.ts'); G = await import('../systemGraph.ts'); } catch { /* old Node */ }
const t = R ? test : test.skip;
const fx = (n) => JSON.parse(fs.readFileSync(new URL(`./fixtures/${n}.json`, import.meta.url), 'utf8'));

// -- the check sees each fault ----------------------------------------------------------------------------------

const box = (x, y, w, h) => ({ x, y, w, h });
const lay = (nodes, loops = {}, heads = {}, labels = []) => ({ nodes, loops, heads, labels, unboxed: [], inputs: null, left: 0, top: 0, width: 1000, height: 600, manual: true });
const wire = (id, from, to) => ({ id, from, to, items: [], type: 'x', count: 1, feedback: false, lane: false });
const route = (id, pts) => ({ id, points: pts, label: pts[0] });
const loopOf = (id, nodes) => ({ id, kind: 'fixed_point', title: id, nodes, edges: [], max_iterations: null, tolerance: null, tolerance_unit: null });
const kinds = (v) => [...new Set(v.map((x) => `${x.kind}:${x.other}`))].sort();

t('a wire through a block it does not belong to is reported; touching its own blocks is fine', () => {
  const L0 = lay({ a: box(0, 0, 100, 50), b: box(400, 0, 100, 50), c: box(200, 0, 100, 50) });
  const w = wire('w', 'a', 'b');
  assert.deepEqual(kinds(R.validateWiring(L0, [], [w], { w: route('w', [[100, 25], [400, 25]]) })), ['block:c']);
  const clean = { w: route('w', [[100, 25], [150, 25], [150, 100], [350, 100], [350, 25], [400, 25]]) };
  assert.deepEqual(R.validateWiring(L0, [], [w], clean), []);
  // a wire that runs back across its own block
  assert.deepEqual(kinds(R.validateWiring(L0, [], [w], { w: route('w', [[100, 25], [50, 25], [50, 100]]) })), ['block:a']);
});

t('a loop group header band is never crossed, even by a wire to a member', () => {
  const lp = loopOf('lp', ['m']);
  const L0 = lay({ m: box(200, 120, 100, 50), s: box(0, 130, 100, 30) }, { lp: box(150, 40, 200, 160) }, { lp: 50 });
  const w = wire('w', 's', 'm');
  const through = { w: route('w', [[100, 145], [120, 145], [120, 60], [250, 60], [250, 120]]) };       // enters the box through its header
  assert.ok(R.validateWiring(L0, [lp], [w], through).some((v) => v.kind === 'header'));
  const viaBorder = { w: route('w', [[100, 145], [200, 145]]) };                                        // enters through the left border, below the header
  assert.deepEqual(R.validateWiring(L0, [lp], [w], viaBorder), []);
});

t('a loop box is closed to a wire that has neither end inside it', () => {
  const lp = loopOf('lp', ['m']);
  const L0 = lay({ m: box(200, 120, 100, 50), a: box(0, 130, 100, 30), b: box(500, 130, 100, 30) }, { lp: box(150, 40, 200, 160) }, { lp: 30 });
  const w = wire('w', 'a', 'b');
  assert.ok(R.validateWiring(L0, [lp], [w], { w: route('w', [[100, 145], [500, 145]]) }).some((v) => v.kind === 'loop' || v.kind === 'block'));
  assert.deepEqual(kinds(R.validateWiring(L0, [lp], [w], { w: route('w', [[100, 145], [120, 145], [120, 230], [480, 230], [480, 145], [500, 145]]) })), []);
});

t('the Inputs label and an extra rectangle (the legend card) are obstacles too', () => {
  const L0 = lay({ a: box(0, 100, 100, 40), b: box(400, 100, 100, 40) }, {}, {}, [{ id: 'inputs', box: box(200, 80, 90, 18) }]);
  const w = wire('w', 'a', 'b');
  assert.deepEqual(kinds(R.validateWiring(L0, [], [w], { w: route('w', [[100, 120], [150, 120], [150, 90], [350, 90], [350, 120], [400, 120]]) })), ['label:inputs']);
  const card = [{ id: 'legend', box: box(0, 0, 600, 60) }];
  assert.deepEqual(kinds(R.validateWiring(L0, [], [w], { w: route('w', [[100, 120], [150, 120], [150, 30], [350, 30], [350, 120], [400, 120]]) }, { extra: card })), ['extra:legend']);
});

t('two wires of different nets on the same line are reported; the same source may share its bus', () => {
  const L0 = lay({ a: box(0, 0, 50, 50), b: box(0, 200, 50, 50), c: box(400, 0, 50, 50), d: box(400, 200, 50, 50) });
  const w1 = wire('w1', 'a', 'c'), w2 = wire('w2', 'b', 'd'), w3 = wire('w3', 'a', 'd');
  const r1 = route('w1', [[50, 25], [200, 25], [200, 100], [400, 100]]);
  const r2 = route('w2', [[50, 225], [200, 225], [200, 80], [400, 80]]);                  // the vertical at x = 200 overlaps w1's
  assert.ok(R.validateWiring(L0, [], [w1, w2], { w1: r1, w2: r2 }).some((v) => v.kind === 'overlap'));
  const r3 = route('w3', [[50, 25], [200, 25], [200, 300], [400, 225]]);
  assert.ok(!R.validateWiring(L0, [], [w1, w3], { w1: r1, w3: r3 }).some((v) => v.kind === 'overlap'));   // same source
  assert.ok(R.validateWiring(L0, [], [w1], {}).some((v) => v.kind === 'unrouted'));
});

t('a block without a layout hint goes to a row under the hinted ones instead of dropping the whole grid', () => {
  const node = (id, layout) => ({ id, kind: 'compute', title: id, summary: '', inputs: [], outputs: [], source: null, status: { state: 'unknown' }, layout });
  const d = { version: 'v', view: 'x', title: '', nodes: [node('a', { col: 1, row: 0 }), node('b', { col: 2, row: 0 }), node('new', null)], edges: [], loops: [], notes: [], status_context: null };
  const l = L.layoutGraph(d, {});
  assert.equal(l.manual, true);
  assert.ok(l.nodes.new.y > l.nodes.a.y + l.nodes.a.h);
  assert.equal(l.nodes.b.x - l.nodes.a.x, L.COL_PITCH);
  const wide = L.layoutGraph(d, { extraCol: 26 });
  assert.equal(wide.nodes.b.x - wide.nodes.a.x, L.COL_PITCH + 26);
});

// -- the real graphs ----------------------------------------------------------------------------------------------

const lt = V ? test : test.skip;
function wiring(name, showAll) {
  const d = fx(name);
  const v = V.buildView(d, showAll);
  const cc = V.chipCounts(d, v);
  const hs = {}; const heads = {};
  for (const n of d.nodes) hs[n.id] = L.blockHeight(n, cc[n.id]);
  for (const l of d.loops) heads[l.id] = V.loopHead(V.loopText(l, (k) => k).lines);
  const ringed = G.splitLoops(d.loops).unboxed.map((l) => l.id);
  return { d, v, w: R.buildWiring(d, v, { heights: hs, lanes: V.laneCounts(d.loops, v.wires), heads }, { ringed }), ringed };
}

for (const name of ['system_graph_propeller_s1', 'system_graph_propeller_s1_motor', 'system_graph_modules', 'system_graph_modules_motor']) {
  lt(`${name}: no wire is drawn through a block, a loop header, a foreign loop box, a label or another wire's line`, () => {
    const { d, v, w, ringed } = wiring(name, false);
    assert.equal(w.violations.length, 0, JSON.stringify(R.summarize(w.violations)) + JSON.stringify(w.violations.slice(0, 3)));
    assert.equal(Object.keys(w.routes).length, v.wires.length);
    // and it is the check itself that says so: run it again on the final layout
    assert.deepEqual(R.validateWiring(w.layout, d.loops, v.wires, w.routes, { ringed }), []);
  });
  lt(`${name}: with "Show all wires" the count is reported (not asserted)`, () => {
    const { w } = wiring(name, true);
    assert.ok(Number.isInteger(w.violations.length));
    assert.ok(w.violations.every((x) => ['block', 'header', 'loop', 'label', 'extra', 'overlap', 'unrouted'].includes(x.kind)));
    console.log(`  [info] ${name} show-all: ${w.violations.length} ${JSON.stringify(R.summarize(w.violations))}`);
  });
}

lt('the loop header band is an obstacle on the real modules graph: no wire crosses the EM-thermal header', () => {
  const { d, w } = wiring('system_graph_modules_motor', false);
  const box0 = w.layout.loops.em_thermal_module;
  const band = { x: box0.x, y: box0.y, w: box0.w, h: w.layout.heads.em_thermal_module };
  assert.ok(band.h > 20);
  for (const r of Object.values(w.routes)) {
    for (let i = 0; i + 1 < r.points.length; i++) {
      const [a, b] = [r.points[i], r.points[i + 1]];
      const hit = Math.max(a[0], b[0]) > band.x && Math.min(a[0], b[0]) < band.x + band.w && Math.max(a[1], b[1]) > band.y && Math.min(a[1], b[1]) < band.y + band.h;
      assert.ok(!hit, `${r.id} crosses the header`);
    }
  }
  assert.ok(d.loops.length >= 3);
});
