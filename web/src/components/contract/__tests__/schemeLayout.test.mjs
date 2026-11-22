// node --test: the automatic layout of "How it works" (schemeLayout.ts) - cards on the grid, ports per side, connectors that
// do not cross each other or run through a card, the labels of a pair apart.
import test from 'node:test';
import assert from 'node:assert/strict';
import { CARD_H, CARD_W, LABEL_STEP, crosses, placeNodes, polyline, route } from '../schemeLayout.ts';

const node = (id, col, row) => ({ id, label: id, col, row });
const edge = (from, to, kind) => ({ from, to, kind, label: `${from}-${to}` });
const CHAIN = {
  nodes: [node('customer', 0, 1), node('motres', 1, 0), node('mochi', 2, 1), node('oem', 2, 2)],
  edges: [edge('customer', 'motres', 'money'), edge('motres', 'customer', 'documents'), edge('motres', 'mochi', 'money'),
          edge('mochi', 'motres', 'documents'), edge('mochi', 'oem', 'money'), edge('oem', 'mochi', 'goods'),
          edge('mochi', 'customer', 'goods'), edge('mochi', 'customer', 'documents')],
};

const inside = (p, c) => p[0] > c.x + 0.5 && p[0] < c.x + CARD_W - 0.5 && p[1] > c.y + 0.5 && p[1] < c.y + CARD_H - 0.5;

test('the chain of four parties: no connector crosses another or runs through a card', () => {
  const { placed, routed } = route(CHAIN);
  assert.equal(routed.length, 8);
  for (let i = 0; i < routed.length; i += 1) {
    for (let j = i + 1; j < routed.length; j += 1) {
      assert.equal(crosses(polyline(routed[i]), polyline(routed[j])), false, `${i} x ${j}`);
    }
    const own = new Set([routed[i].edge.from, routed[i].edge.to]);
    const pts = polyline(routed[i]);
    for (const c of placed.filter((p) => !own.has(p.id))) {
      for (let k = 0; k < pts.length - 1; k += 1) {
        for (let t = 0; t <= 20; t += 1) {
          const p = [pts[k][0] + (pts[k + 1][0] - pts[k][0]) * t / 20, pts[k][1] + (pts[k + 1][1] - pts[k][1]) * t / 20];
          assert.equal(inside(p, c), false, `edge ${i} through ${c.id}`);
        }
      }
    }
  }
});

test('ports sit on the facing side of their card, and two connectors of one pair never share one', () => {
  const { placed, routed } = route(CHAIN);
  const at = new Map(placed.map((p) => [p.id, p]));
  const used = new Set();
  for (const r of routed) {
    for (const [port, id] of [[r.source, r.edge.from], [r.target, r.edge.to]]) {
      const c = at.get(id);
      const onSide = { left: port.x === c.x, right: port.x === c.x + CARD_W, top: port.y === c.y, bottom: port.y === c.y + CARD_H }[port.side];
      assert.ok(onSide, `${id} ${port.side}`);
      const key = `${id}|${port.side}|${port.frac}`;
      assert.ok(!used.has(key), `port ${key} used twice`);
      used.add(key);
    }
  }
});

test('the labels of the connectors between one pair of cards are LABEL_STEP apart', () => {
  const { routed } = route(CHAIN);
  const pair = routed.filter((r) => [r.edge.from, r.edge.to].sort().join() === 'mochi,motres');
  assert.equal(pair.length, 2);
  assert.ok(Math.abs(pair[0].labelY - pair[1].labelY) >= LABEL_STEP - 1e-9 || Math.abs(pair[0].labelX - pair[1].labelX) >= LABEL_STEP - 1e-9);
});

test('an older file with x / y (no col / row) lands on the grid', () => {
  const old = { nodes: [{ id: 'a', label: 'a', x: 30, y: 150 }, { id: 'b', label: 'b', x: 370, y: 5 }, { id: 'c', label: 'c', x: 750, y: 165 },
                        { id: 'd', label: 'd', x: 300, y: 310 }], edges: [] };
  assert.deepEqual(placeNodes(old.nodes).map((p) => [p.id, p.col, p.row]), [['a', 0, 1], ['b', 1, 0], ['c', 2, 1], ['d', 1, 2]]);
});
