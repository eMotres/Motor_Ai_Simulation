// node --test: the automatic layout of "How it works" (schemeLayout.ts) - cards on the grid, ports per side, connectors that
// do not cross each other or run through a card, the labels of a pair apart.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { CARD_H, CARD_W, LABEL_STEP, crosses, extent, panelHeight, placeNodes, polyline, route } from '../schemeLayout.ts';

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

test('an older file with x / y lands on the grid by rank per axis: distinct x, distinct column', () => {
  const old = [{ id: 'a', label: 'a', x: 30, y: 150 }, { id: 'b', label: 'b', x: 370, y: 5 }, { id: 'c', label: 'c', x: 750, y: 165 },
               { id: 'd', label: 'd', x: 300, y: 310 }];
  assert.deepEqual(placeNodes(old).map((p) => [p.id, p.col, p.row]), [['a', 0, 1], ['b', 2, 0], ['c', 3, 2], ['d', 1, 3]]);
});

test('older cards side by side stay apart (x 0 / 140 / 280 at one y)', () => {
  const old = [0, 140, 280].map((x, i) => ({ id: `n${i}`, label: 'n', x, y: 0, w: 100, h: 40 }));
  const cells = placeNodes(old).map((p) => `${p.col},${p.row}`);
  assert.deepEqual(cells, ['0,0', '1,0', '2,0']);
});

test('explicit cells are always kept; a legacy node on a taken cell moves to the next free one', () => {
  const mixed = [{ id: 'g', label: 'g', col: 1, row: 0 }, { id: 'l1', label: 'l', x: 0, y: 0 }, { id: 'l2', label: 'l', x: 500, y: 0 },
                 { id: 'g2', label: 'g', col: 0, row: 0 }];
  const at = Object.fromEntries(placeNodes(mixed).map((p) => [p.id, `${p.col},${p.row}`]));
  assert.equal(at.g, '1,0');
  assert.equal(at.g2, '0,0');
  assert.equal(new Set(Object.values(at)).size, 4, JSON.stringify(at));
});

test('many connectors between one pair (40, the validator maximum) route at once without a factorial search', () => {
  const edges = Array.from({ length: 40 }, (_, i) => edge(i % 2 ? 'a' : 'b', i % 2 ? 'b' : 'a', ['money', 'goods', 'documents'][i % 3]));
  const t0 = Date.now();
  const { routed } = route({ nodes: [node('a', 0, 0), node('b', 1, 0)], edges });
  assert.equal(routed.length, 40);
  assert.ok(Date.now() - t0 < 2000, `took ${Date.now() - t0} ms`);
});

test('the panel is as tall as the drawing at its width: no tall empty area, still fits a phone', () => {
  const { placed } = route(CHAIN);
  const ext = extent(placed);
  assert.ok(Math.abs(panelHeight(900, ext) - 900 * ext.h / ext.w) <= 1);   // the drawing's own aspect ratio
  assert.equal(panelHeight(1300, ext), 560);                   // a wide page: capped, the drawing fills the height
  assert.equal(panelHeight(340, ext), 160);                    // a phone: the minimum, the whole drawing scaled to fit
  assert.equal(panelHeight(0, ext), 360);                      // not measured yet
  assert.equal(panelHeight(5000, ext), 560);                   // capped
});

test('the scheme is a static figure: the wheel and drags never zoom or pan it', () => {
  const src = readFileSync(new URL('../ContractScheme.tsx', import.meta.url), 'utf-8');
  for (const prop of ['zoomOnScroll={false}', 'zoomOnPinch={false}', 'zoomOnDoubleClick={false}', 'panOnDrag={false}',
                      'panOnScroll={false}', 'preventScrolling={false}', 'nodesDraggable={false}', 'elementsSelectable={false}']) {
    assert.ok(src.includes(prop), prop);
  }
});
