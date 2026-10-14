// Layout and orthogonal wire routing of the System graph.
//
// BLOCKS.  Context inputs (parameter sources) stand in a compact column at the left.  When every other block
// carries a `layout` hint {col, row} (the propeller S1 view) the blocks are placed on that grid: the main chain on
// one row, helper blocks above / below it.  Without hints (the modules view) dagre ranks the blocks, each loop
// laid out on its own and placed as one block, so no foreign block can land inside a loop's box.
//
// WIRES.  One wire per block pair, drawn with right angles: outputs leave the right side, inputs enter the left side
// at handles ordered by the other end's height (so wires do not cross at the ports); the vertical run of a wire
// is put next to its target or its source, whichever keeps the horizontal run clear of blocks; vertical runs in one
// gap never share a column when they overlap.  Feedback wires (and wires marked `lane`) leave the bottom of their
// block, run in a lane at the bottom of the enclosing loop group - longest span lowest - and come up into the
// bottom of the target.
import dagre from '@dagrejs/dagre';
import { nesting, splitLoops } from './systemGraph.ts';
import type { Box, SgDoc, SgLoop, SgNode } from './systemGraph.ts';
import { laneOwner } from './systemGraphView.ts';
import type { Wire } from './systemGraphView.ts';

export const NODE_W = 200;
export const NODE_H = 62;
export const CHIP_ROW = 20;
export const COMPACT_H = 40;
export const COL_PITCH = 262;
export const ROW_PITCH = 100;
const PAD = 16;       // between a loop's border and its content (fallback layout)
const HEAD = 24;      // the loop's label strip
const CTX_PITCH = COMPACT_H + 12;

/** Height of a block: context blocks are compact, others grow by one row per two chips. */
export function blockHeight(n: Pick<SgNode, 'context'>, chips: number): number {
  if (n.context) return COMPACT_H;
  return NODE_H + (chips > 0 ? Math.ceil(chips / 2) * CHIP_ROW : 0);
}

export interface LayoutOpts {
  /** block id -> height (blockHeight) */
  heights?: Record<string, number>;
  /** loop id -> number of lane wires under it */
  lanes?: Record<string, number>;
}

export interface Layout {
  nodes: Record<string, Box>;
  loops: Record<string, Box>;
  /** loops drawn as a ring on their members instead of a box */
  unboxed: string[];
  /** the "Inputs" label position (null when there is no context block) */
  inputs: { x: number; y: number } | null;
  /** origin and size of everything drawn (blocks and loop boxes) */
  left: number;
  top: number;
  width: number;
  height: number;
  manual: boolean;
}

// -- loops ----------------------------------------------------------------------------------------------------

function levels(loops: readonly SgLoop[]): Record<string, number> {
  const sets = loops.map((l) => ({ id: l.id, s: new Set(l.nodes) }));
  const lv = (id: string): number => {
    const me = sets.find((x) => x.id === id)!;
    const inner = sets.filter((y) => y.id !== id && y.s.size < me.s.size && [...y.s].every((n) => me.s.has(n)));
    return inner.length ? 1 + Math.max(...inner.map((y) => lv(y.id))) : 0;
  };
  const out: Record<string, number> = {};
  for (const x of sets) out[x.id] = lv(x.id);
  return out;
}

const union = (rs: Box[]): Box => {
  const x0 = Math.min(...rs.map((r) => r.x)), y0 = Math.min(...rs.map((r) => r.y));
  const x1 = Math.max(...rs.map((r) => r.x + r.w)), y1 = Math.max(...rs.map((r) => r.y + r.h));
  return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
};

/** Boxes of the loops around already placed blocks: a loop's box holds its members and its child loops' boxes,
 *  with a label strip on top and a lane strip at the bottom (`lanes` wires, 12 px each). */
function boxLoops(boxed: SgLoop[], nodes: Record<string, Box>, lanes: Record<string, number>): Record<string, Box> {
  const lv = levels(boxed);
  const out: Record<string, Box> = {};
  for (const l of [...boxed].sort((a, b) => lv[a.id] - lv[b.id])) {
    const mine = new Set(l.nodes);
    const rs: Box[] = l.nodes.map((n) => nodes[n]).filter(Boolean);
    for (const k of boxed) if (k.id !== l.id && out[k.id] && k.nodes.every((n) => mine.has(n)) && k.nodes.length < l.nodes.length) rs.push(out[k.id]);
    if (!rs.length) continue;
    const u = union(rs);
    const side = 18 + 10 * lv[l.id];
    const strip = lanes[l.id] ? 16 + 12 * lanes[l.id] : 0;
    out[l.id] = { x: u.x - side, y: u.y - side - HEAD, w: u.w + 2 * side, h: u.h + 2 * side + HEAD + strip };
  }
  return out;
}

// -- block placement ------------------------------------------------------------------------------------------

interface Item { id: string; w: number; h: number }
interface Sub { nodes: Record<string, Box>; loops: Record<string, Box>; w: number; h: number }

function place(items: Item[], links: Array<[string, string]>): { pos: Record<string, { x: number; y: number }>; w: number; h: number } {
  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: 'LR', nodesep: 30, ranksep: 70, marginx: 0, marginy: 0 });
  g.setDefaultEdgeLabel(() => ({}));
  for (const it of items) g.setNode(it.id, { width: it.w, height: it.h });
  const seen = new Set<string>();
  for (const [a, b] of links) {
    const k = `${a}\u0000${b}`;
    if (a === b || seen.has(k)) continue;
    seen.add(k);
    g.setEdge(a, b);
  }
  dagre.layout(g);
  const pos: Record<string, { x: number; y: number }> = {};
  let w = 0, h = 0;
  for (const it of items) {
    const d = g.node(it.id);
    pos[it.id] = { x: d.x - it.w / 2, y: d.y - it.h / 2 };
    w = Math.max(w, pos[it.id].x + it.w);
    h = Math.max(h, pos[it.id].y + it.h);
  }
  return { pos, w, h };
}

function level(doc: SgDoc, skip: Set<string>, hs: Record<string, number>, loop: SgLoop | null, boxed: SgLoop[],
               parent: Record<string, string | null>): Sub {
  const members = loop ? new Set(loop.nodes) : new Set(doc.nodes.map((n) => n.id));
  const kids = boxed.filter((l) => (parent[l.id] ?? null) === (loop ? loop.id : null));
  const inKid: Record<string, string> = {};
  for (const k of kids) for (const n of k.nodes) inKid[n] = k.id;
  const subs: Record<string, Sub> = {};
  const items: Item[] = [];
  for (const n of doc.nodes) if (members.has(n.id) && !inKid[n.id] && !skip.has(n.id)) items.push({ id: n.id, w: NODE_W, h: hs[n.id] ?? NODE_H });
  for (const k of kids) {
    subs[k.id] = level(doc, skip, hs, k, boxed, parent);
    items.push({ id: `loop:${k.id}`, w: subs[k.id].w + 2 * PAD, h: subs[k.id].h + 2 * PAD + HEAD });
  }
  const itemOf = (id: string): string | null => (!members.has(id) || skip.has(id) ? null : inKid[id] ? `loop:${inKid[id]}` : id);
  const links: Array<[string, string]> = [];
  for (const e of doc.edges) {
    if (e.feedback) continue;
    const a = itemOf(e.from.node), b = itemOf(e.to.node);
    if (a && b) links.push([a, b]);
  }
  const { pos, w, h } = place(items, links);
  const out: Sub = { nodes: {}, loops: {}, w, h };
  for (const it of items) {
    const p = pos[it.id];
    if (it.id.startsWith('loop:')) {
      const id = it.id.slice(5);
      out.loops[id] = { x: p.x, y: p.y, w: it.w, h: it.h };
      const dx = p.x + PAD, dy = p.y + PAD + HEAD;
      for (const [nid, b] of Object.entries(subs[id].nodes)) out.nodes[nid] = { ...b, x: b.x + dx, y: b.y + dy };
      for (const [lid, b] of Object.entries(subs[id].loops)) out.loops[lid] = { ...b, x: b.x + dx, y: b.y + dy };
    } else {
      out.nodes[it.id] = { x: p.x, y: p.y, w: it.w, h: it.h };
    }
  }
  return out;
}

/** Positions of every block and loop box (top-left corner + size). */
export function layoutGraph(doc: SgDoc, opts: LayoutOpts = {}): Layout {
  const hs = opts.heights ?? {};
  const lanes = opts.lanes ?? {};
  const ctx = doc.nodes.filter((n) => n.context);
  const rest = doc.nodes.filter((n) => !n.context);
  const manual = rest.length > 0 && rest.every((n) => n.layout);
  const { boxed, unboxed } = splitLoops(doc.loops);
  const nodes: Record<string, Box> = {};
  let loops: Record<string, Box> = {};
  let inputs: Layout['inputs'] = null;
  if (manual) {
    for (const n of rest) {
      const h = hs[n.id] ?? NODE_H;
      nodes[n.id] = { x: n.layout!.col * COL_PITCH, y: n.layout!.row * ROW_PITCH - h / 2, w: NODE_W, h };
    }
    const y0 = -(ctx.length * CTX_PITCH) / 2;
    ctx.forEach((n, i) => { nodes[n.id] = { x: 0, y: y0 + i * CTX_PITCH, w: NODE_W, h: COMPACT_H }; });
    if (ctx.length) inputs = { x: 0, y: y0 - 22 };
    loops = boxLoops(boxed, nodes, lanes);
  } else {
    const skip = new Set(ctx.map((n) => n.id));
    const { parent } = nesting(boxed);
    const top = level(doc, skip, hs, null, boxed, parent);
    const dx = ctx.length ? NODE_W + 70 : 0;
    for (const [id, b] of Object.entries(top.nodes)) nodes[id] = { ...b, x: b.x + dx };
    for (const [id, b] of Object.entries(top.loops)) loops[id] = { ...b, x: b.x + dx };
    ctx.forEach((n, i) => { nodes[n.id] = { x: 0, y: 26 + i * CTX_PITCH, w: NODE_W, h: COMPACT_H }; });
    if (ctx.length) inputs = { x: 0, y: 4 };
  }
  const all = [...Object.values(nodes), ...Object.values(loops)];
  const u = all.length ? union(all) : { x: 0, y: 0, w: 0, h: 0 };
  return { nodes, loops, unboxed: unboxed.map((l) => l.id), inputs, left: u.x, top: u.y, width: u.w, height: u.h, manual };
}

// -- wire routing ---------------------------------------------------------------------------------------------

export type Pt = [number, number];
export interface Route { id: string; points: Pt[]; label: Pt }
const GRID_STEP = 9;

const hit = (a: Pt, b: Pt, r: Box, m = 3): boolean => {
  const x0 = Math.min(a[0], b[0]), x1 = Math.max(a[0], b[0]), y0 = Math.min(a[1], b[1]), y1 = Math.max(a[1], b[1]);
  return x1 > r.x - m && x0 < r.x + r.w + m && y1 > r.y - m && y0 < r.y + r.h + m;
};
const crossings = (pts: Pt[], rects: Box[]): number => {
  let n = 0;
  for (let i = 0; i + 1 < pts.length; i++) for (const r of rects) if (hit(pts[i], pts[i + 1], r)) n++;
  return n;
};
const cy = (b: Box): number => b.y + b.h / 2;
const cx = (b: Box): number => b.x + b.w / 2;

/** Orthogonal routes of the wires.  Returns the points of every wire and the extent (top, height) with the lanes. */
export function routeWires(layout: Layout, loops: readonly SgLoop[], wires: readonly Wire[]): { routes: Record<string, Route>; top: number; height: number } {
  const N = layout.nodes;
  const routes: Record<string, Route> = {};
  const direct: Wire[] = [];
  const lane: Wire[] = [];
  for (const w of wires) {
    const a = N[w.from], b = N[w.to];
    if (!a || !b) continue;
    if (w.lane || b.x < a.x + a.w + 8) { lane.push(w); continue; }
    direct.push(w);
  }

  // handles: outputs on the right ordered by the target's height, inputs on the left ordered by the source's
  const outY: Record<string, number> = {}, inY: Record<string, number> = {};
  const byFrom: Record<string, Wire[]> = {}, byTo: Record<string, Wire[]> = {};
  for (const w of direct) { (byFrom[w.from] ||= []).push(w); (byTo[w.to] ||= []).push(w); }
  for (const [id, ws] of Object.entries(byFrom)) {
    ws.sort((p, q) => cy(N[p.to]) - cy(N[q.to]) || N[p.to].x - N[q.to].x);
    ws.forEach((w, i) => { outY[w.id] = N[id].y + (N[id].h * (i + 1)) / (ws.length + 1); });
  }
  for (const [id, ws] of Object.entries(byTo)) {
    ws.sort((p, q) => cy(N[p.from]) - cy(N[q.from]) || N[p.from].x - N[q.from].x);
    ws.forEach((w, i) => { inY[w.id] = N[id].y + (N[id].h * (i + 1)) / (ws.length + 1); });
  }

  // which end carries the horizontal run: the side that keeps it clear of other blocks
  type Pl = { w: Wire; sx: number; sy: number; tx: number; ty: number; mode: 'A' | 'B'; i: number; slot: number };
  const pls: Pl[] = direct.map((w) => {
    const a = N[w.from], b = N[w.to];
    const sx = a.x + a.w, sy = outY[w.id], tx = b.x, ty = inY[w.id];
    const rects = Object.entries(N).filter(([id]) => id !== w.from && id !== w.to).map(([, r]) => r);
    const A: Pt[] = [[sx, sy], [tx - 18, sy], [tx - 18, ty], [tx, ty]];
    const B: Pt[] = [[sx, sy], [sx + 18, sy], [sx + 18, ty], [tx, ty]];
    return { w, sx, sy, tx, ty, mode: crossings(A, rects) <= crossings(B, rects) ? 'A' : 'B', i: 0, slot: 0 };
  });

  // closest-first index: wires into one block (mode A) / out of one block (mode B), above and below it
  const groupKey = (p: Pl) => (p.mode === 'A' ? `A${p.w.to}` : `B${p.w.from}`);
  const groups: Record<string, Pl[]> = {};
  for (const p of pls) (groups[groupKey(p)] ||= []).push(p);
  for (const g of Object.values(groups)) {
    const mode = g[0].mode;
    const above = g.filter((p) => (mode === 'A' ? p.sy < p.ty : p.ty < p.sy));
    const below = g.filter((p) => !above.includes(p));
    // A: the topmost source runs closest to the target; B: the topmost target runs closest to the source
    above.sort((p, q) => (mode === 'A' ? p.sy - q.sy : p.ty - q.ty)).forEach((p, k) => { p.i = k; });
    below.sort((p, q) => (mode === 'A' ? q.sy - p.sy : q.ty - p.ty)).forEach((p, k) => { p.i = k; });
  }
  // distinct columns inside one gap for runs that overlap vertically
  const gaps: Record<string, Pl[]> = {};
  for (const p of pls) (gaps[p.mode === 'A' ? `A${Math.round(p.tx)}` : `B${Math.round(p.sx)}`] ||= []).push(p);
  for (const g of Object.values(gaps)) {
    g.sort((p, q) => p.i - q.i || p.w.id.localeCompare(q.w.id));
    const done: Pl[] = [];
    for (const p of g) {
      let k = p.i;
      const lo = Math.min(p.sy, p.ty), hi = Math.max(p.sy, p.ty);
      while (done.some((q) => q.slot === k && Math.min(q.sy, q.ty) <= hi + 4 && Math.max(q.sy, q.ty) >= lo - 4)) k++;
      p.slot = k;
      done.push(p);
    }
  }
  for (const p of pls) {
    const x = p.mode === 'A' ? Math.max(p.sx + 8, p.tx - 18 - GRID_STEP * p.slot) : Math.min(p.tx - 8, p.sx + 18 + GRID_STEP * p.slot);
    const pts: Pt[] = Math.abs(p.sy - p.ty) < 1 ? [[p.sx, p.sy], [p.tx, p.ty]] : [[p.sx, p.sy], [x, p.sy], [x, p.ty], [p.tx, p.ty]];
    // the label sits on the longest horizontal run
    let best: Pt = [(p.sx + p.tx) / 2, p.sy], len = -1;
    for (let k = 0; k + 1 < pts.length; k++) {
      const l = Math.abs(pts[k + 1][0] - pts[k][0]);
      if (pts[k][1] === pts[k + 1][1] && l > len) { len = l; best = [(pts[k][0] + pts[k + 1][0]) / 2, pts[k][1]]; }
    }
    routes[p.w.id] = { id: p.w.id, points: pts, label: best };
  }

  // lane wires: bottom of the block, a lane at the bottom of the owning loop group, bottom of the target.  A wire
  // that belongs to no loop runs under everything - or over everything when a block stands below / above its ends.
  let top = layout.top;
  let bottomEnd = layout.top + layout.height;
  const owners: Record<string, Wire[]> = {};
  const side: Record<string, 'b' | 't'> = {};
  const vertHits = (id: string, ySide: 'b' | 't', other: string): number => {
    const r = N[id];
    const x = cx(r);
    const y0 = ySide === 'b' ? r.y + r.h : r.y;
    const y1 = ySide === 'b' ? bottomEnd + 22 : top - 22;
    return Object.entries(N).filter(([k]) => k !== id && k !== other)
      .filter(([, q]) => x > q.x - 3 && x < q.x + q.w + 3 && Math.max(y0, y1) > q.y && Math.min(y0, y1) < q.y + q.h).length;
  };
  for (const w of lane) {
    const o = w.lane ? laneOwner(loops, w) : null;
    (owners[o && layout.loops[o] ? o : ''] ||= []).push(w);
    side[w.id] = 'b';
    if (!(o && layout.loops[o])) {
      const hb = vertHits(w.from, 'b', w.to) + vertHits(w.to, 'b', w.from);
      const ht = vertHits(w.from, 't', w.to) + vertHits(w.to, 't', w.from);
      if (ht < hb) side[w.id] = 't';
    }
  }
  const ends: Record<string, Array<{ w: Wire; end: 'from' | 'to'; partner: number }>> = {};
  for (const w of lane) {
    (ends[`${w.from}|${side[w.id]}`] ||= []).push({ w, end: 'from', partner: cx(N[w.to]) });
    (ends[`${w.to}|${side[w.id]}`] ||= []).push({ w, end: 'to', partner: cx(N[w.from]) });
  }
  const slotX: Record<string, number> = {};
  for (const [key, es] of Object.entries(ends)) {
    const id = key.slice(0, key.lastIndexOf('|'));
    const mine = cx(N[id]);
    const right = es.filter((e) => e.partner >= mine).sort((p, q) => q.partner - p.partner);     // far first = leftmost
    const left = es.filter((e) => e.partner < mine).sort((p, q) => q.partner - p.partner);       // near first
    [...right, ...left].forEach((e, j, arr) => { slotX[`${e.w.id}:${e.end}`] = N[id].x + (N[id].w * (j + 1)) / (arr.length + 1); });
  }
  for (const [o, ws] of Object.entries(owners)) {
    for (const sd of ['b', 't'] as const) {
      const g = ws.filter((w) => side[w.id] === sd);
      g.sort((p, q) => Math.abs(cx(N[q.to]) - cx(N[q.from])) - Math.abs(cx(N[p.to]) - cx(N[p.from])));
      g.forEach((w, k) => {
        let laneY: number;
        if (o) laneY = layout.loops[o].y + layout.loops[o].h - 10 - 12 * k;
        else if (sd === 'b') { laneY = bottomEnd + 22 + 12 * k; bottomEnd = Math.max(bottomEnd, laneY + 14); }
        else { laneY = layout.top - 22 - 12 * k; top = Math.min(top, laneY - 14); }
        const a = N[w.from], b = N[w.to];
        const x0 = slotX[`${w.id}:from`], x1 = slotX[`${w.id}:to`];
        const ya = sd === 'b' ? a.y + a.h : a.y, yb = sd === 'b' ? b.y + b.h : b.y;
        routes[w.id] = { id: w.id, points: [[x0, ya], [x0, laneY], [x1, laneY], [x1, yb]], label: [(x0 + x1) / 2, laneY] };
      });
    }
  }
  return { routes, top, height: bottomEnd - top };
}

/** SVG path through the points with rounded corners. */
export function roundedPath(pts: readonly Pt[], r = 6): string {
  if (!pts.length) return '';
  let d = `M ${pts[0][0]} ${pts[0][1]}`;
  for (let i = 1; i < pts.length - 1; i++) {
    const [px, py] = pts[i - 1], [x, y] = pts[i], [nx, ny] = pts[i + 1];
    const l1 = Math.hypot(x - px, y - py), l2 = Math.hypot(nx - x, ny - y);
    const rr = Math.min(r, l1 / 2, l2 / 2);
    if (rr < 1) { d += ` L ${x} ${y}`; continue; }
    d += ` L ${x - ((x - px) / l1) * rr} ${y - ((y - py) / l1) * rr} Q ${x} ${y} ${x + ((nx - x) / l2) * rr} ${y + ((ny - y) / l2) * rr}`;
  }
  const last = pts[pts.length - 1];
  return d + ` L ${last[0]} ${last[1]}`;
}
