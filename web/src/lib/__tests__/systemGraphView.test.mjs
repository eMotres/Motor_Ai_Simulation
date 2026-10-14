// node --test - what the System view draws: one wire per block pair (parallel edges merged), context inputs as chips,
// the status wording, and the layout + routing on the real graphs (fixtures = GET /api/system/graph output).
// Imports lib/systemGraphView.ts + systemGraphLayout.ts (Node >= 22.18 / 24; else skipped).
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

let V = null; let L = null;
try { V = await import('../systemGraphView.ts'); L = await import('../systemGraphLayout.ts'); } catch { /* old Node */ }
const t = V ? test : test.skip;
const fx = (n) => JSON.parse(fs.readFileSync(new URL(`./fixtures/${n}.json`, import.meta.url), 'utf8'));

const node = (id, extra = {}) => ({ id, kind: 'compute', title: id, summary: '', inputs: [], outputs: [], source: null, status: { state: 'unknown' }, ...extra });
const edge = (a, b, type = 'x', extra = {}) => ({ id: `${a}->${b}:${type}:${extra.label ?? ''}`, from: { node: a, port: null }, to: { node: b, port: null }, type, feedback: false, via: 'data', ...extra });
const doc = (nodes, edges, loops = []) => ({ version: 'v', view: 'x', title: '', nodes, edges, loops, notes: [], status_context: null });

t('parallel edges between two blocks become one wire with a count', () => {
  const d = doc([node('a'), node('b')], [edge('a', 'b', 'rpm'), edge('a', 'b', 'rpm', { label: 'again' }), edge('a', 'b', 'torque')]);
  const { wires } = V.buildView(d, false);
  assert.equal(wires.length, 1);
  assert.equal(wires[0].count, 3);
  assert.equal(wires[0].type, 'mixed');
  assert.equal(wires[0].items.length, 3);
  const same = V.buildView(doc([node('a'), node('b')], [edge('a', 'b', 'rpm'), edge('a', 'b', 'rpm', { label: 'x' })]), false).wires[0];
  assert.equal(same.type, 'rpm');
});

t('edges of a context block become chips on the consumer, not wires; show-all brings the wires back', () => {
  const d = doc([node('m', { context: true }), node('a'), node('b')], [edge('m', 'a', 'grid'), edge('m', 'a', 'geometry'), edge('m', 'b', 'grid'), edge('a', 'b', 'torque')]);
  const v = V.buildView(d, false);
  assert.deepEqual(v.wires.map((w) => w.id), ['a>b']);
  assert.equal(v.chips.a.length, 1);                              // two items from one source = one chip
  assert.equal(v.chips.a[0].items.length, 2);
  assert.equal(v.chips.a[0].source, 'm');
  assert.equal(v.chips.b.length, 1);
  const all = V.buildView(d, true);
  assert.equal(all.wires.length, 3);
  assert.deepEqual(all.chips, {});
});

t('a minor edge is a chip labelled with its meaning; a context-to-context edge is dropped', () => {
  const d = doc([node('c', { context: true }), node('d', { context: true }), node('a'), node('b')],
    [edge('a', 'b', 'rpm', { minor: true, label: 'rpm' }), edge('c', 'd', 'p'), edge('a', 'b', 'torque')]);
  const v = V.buildView(d, false);
  assert.equal(v.wires.length, 1);
  assert.equal(v.chips.b[0].label, 'rpm');
  assert.equal(v.chips.d, undefined);
});

t('feedback and lane edges are separate wires and are counted per loop', () => {
  const loops = [{ id: 'inner', kind: 'fixed_point', title: '', nodes: ['a', 'b'], edges: [] },
    { id: 'outer', kind: 'search', title: '', nodes: ['a', 'b', 'c'], edges: [] }];
  const d = doc([node('a'), node('b'), node('c')], [edge('a', 'b', 'x'), edge('b', 'a', 'T', { feedback: true }), edge('b', 'a', 'U', { feedback: true }),
    edge('c', 'a', 'v', { feedback: true }), edge('a', 'c', 'r', { route: 'lane' })], loops);
  const v = V.buildView(d, false);
  assert.equal(v.wires.length, 4);
  assert.equal(v.wires.find((w) => w.id === 'b>a~').count, 2);
  // b>a belongs to the inner loop; c>a and a>c leave it, so they run under the outer one
  assert.deepEqual(V.laneCounts(loops, v.wires), { inner: 1, outer: 2 });
});

t('status wording: no badge without a motor, "no run yet" without a record, never "unknown"', () => {
  assert.deepEqual(V.badgeOf({ state: 'done' }, false), { kind: 'none' });
  assert.deepEqual(V.badgeOf({ state: 'unknown' }, false), { kind: 'none' });
  assert.deepEqual(V.badgeOf({ state: 'unknown' }, true), { kind: 'norun' });
  assert.deepEqual(V.badgeOf(undefined, true), { kind: 'norun' });
  for (const s of ['done', 'running', 'error', 'cached']) assert.deepEqual(V.badgeOf({ state: s }, true), { kind: 'state', state: s });
});

t('hover focus keeps a block, its wires and its chip sources lit', () => {
  const d = doc([node('m', { context: true }), node('a'), node('b'), node('z')], [edge('m', 'a', 'g'), edge('a', 'b', 't')]);
  const v = V.buildView(d, false);
  const n = V.neighbours(v, 'a');
  assert.deepEqual([...n.nodes].sort(), ['a', 'b', 'm']);
  assert.deepEqual([...n.wires], ['a>b']);
  assert.deepEqual([...V.neighbours(v, 'm').nodes].sort(), ['a', 'm']);
});

// -- layout + routing on the real graphs ----------------------------------------------------------------------

const lt = L ? test : test.skip;
const prep = (name, showAll) => {
  const d = fx(name);
  const v = V.buildView(d, showAll);
  const cc = V.chipCounts(d, v);
  const hs = {};
  for (const n of d.nodes) hs[n.id] = L.blockHeight(n, cc[n.id]);
  const lay = L.layoutGraph(d, { heights: hs, lanes: V.laneCounts(d.loops, v.wires) });
  const r = L.routeWires(lay, d.loops, v.wires);
  return { d, v, lay, r };
};
const through = (pts, box) => pts.slice(1).some((b, i) => {
  const a = pts[i];
  return Math.max(a[0], b[0]) > box.x && Math.min(a[0], b[0]) < box.x + box.w && Math.max(a[1], b[1]) > box.y && Math.min(a[1], b[1]) < box.y + box.h;
});

for (const name of ['system_graph_propeller_s1', 'system_graph_modules']) {
  lt(`${name}: right-angle wires that cross no block, loop boxes hold exactly their members`, () => {
    const { d, v, lay, r } = prep(name, false);
    assert.equal(lay.manual, true);
    for (const w of v.wires) {
      const pts = r.routes[w.id].points;
      for (let i = 1; i < pts.length; i++) assert.ok(pts[i][0] === pts[i - 1][0] || pts[i][1] === pts[i - 1][1], `${w.id} is not orthogonal`);
      for (const [id, box] of Object.entries(lay.nodes)) if (id !== w.from && id !== w.to) assert.ok(!through(pts, box), `${w.id} crosses ${id}`);
    }
    for (const l of d.loops) {
      const bx = lay.loops[l.id];
      if (!bx) continue;
      for (const n of d.nodes) {
        const b = lay.nodes[n.id];
        const inside = b.x >= bx.x && b.y >= bx.y && b.x + b.w <= bx.x + bx.w && b.y + b.h <= bx.y + bx.h;
        const overlap = b.x < bx.x + bx.w && b.x + b.w > bx.x && b.y < bx.y + bx.h && b.y + b.h > bx.y;
        if (l.nodes.includes(n.id)) assert.ok(inside, `${n.id} in ${l.id}`); else assert.ok(!overlap, `${n.id} outside ${l.id}`);
      }
    }
  });
}

lt('propeller S1: the main chain is one row, the limit checks a column in front of the verdict', () => {
  const { lay } = prep('system_graph_propeller_s1', false);
  const mid = (id) => lay.nodes[id].y + lay.nodes[id].h / 2;
  const chain = ['rpm_search', 'propeller', 'torque_demand', 'current', 'voltage', 'losses', 'thermal', 'chk_demag', 'verdict', 'result'];
  for (const id of chain) assert.ok(Math.abs(mid(id) - mid('rpm_search')) < 1, id);
  for (let i = 1; i < chain.length; i++) assert.ok(lay.nodes[chain[i]].x > lay.nodes[chain[i - 1]].x, chain[i]);
  const col = ['chk_winding', 'chk_magnet', 'chk_demag', 'chk_controller', 'chk_battery'];
  for (const id of col) assert.equal(lay.nodes[id].x, lay.nodes.chk_demag.x);
  assert.ok(lay.nodes.chk_demag.x < lay.nodes.verdict.x);
  const ys = col.map(mid);
  assert.deepEqual([...ys].sort((a, b) => a - b), ys);
});

lt('propeller S1: parameter sources are chips (no wire from them), feedback runs in a lane at the bottom of its loop group', () => {
  const { d, v, lay, r } = prep('system_graph_propeller_s1', false);
  const ctx = new Set(d.nodes.filter((n) => n.context).map((n) => n.id));
  assert.ok(ctx.size >= 5);
  assert.ok(v.wires.every((w) => !ctx.has(w.from)));
  assert.ok(Object.values(v.chips).flat().some((c) => c.source === 'model'));
  const fb = v.wires.filter((w) => w.feedback);
  assert.ok(fb.length >= 3);
  for (const w of fb) {
    const owner = d.loops.filter((l) => l.nodes.includes(w.from) && l.nodes.includes(w.to)).sort((a, b) => a.nodes.length - b.nodes.length)[0];
    const box = lay.loops[owner.id];
    const laneY = r.routes[w.id].points[1][1];
    const src = lay.nodes[w.from];
    assert.ok(laneY > src.y + src.h, `${w.id} lane is below its block`);
    assert.ok(laneY < box.y + box.h && laneY > box.y + box.h - 16 - 12 * 3, `${w.id} lane sits in the strip at the bottom of ${owner.id}`);
  }
  // merged parallel edges: thermal -> voltage carries two data items
  assert.equal(v.wires.find((w) => w.id === 'thermal>voltage~').count, 2);
});

lt('handles are ordered by the other end: wires into one block do not cross at its ports', () => {
  const { v, lay, r } = prep('system_graph_propeller_s1', false);
  const into = v.wires.filter((w) => w.to === 'verdict' && !w.lane);
  assert.equal(into.length, 5);
  const byEnd = [...into].sort((a, b) => r.routes[a.id].points.at(-1)[1] - r.routes[b.id].points.at(-1)[1]);
  const bySrc = [...into].sort((a, b) => lay.nodes[a.from].y - lay.nodes[b.from].y);
  assert.deepEqual(byEnd.map((w) => w.id), bySrc.map((w) => w.id));
});
