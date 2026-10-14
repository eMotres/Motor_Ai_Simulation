// Orthogonal wire routing of the System graph, and the check that no wire is drawn through anything.
//
// WHAT IS AN OBSTACLE.  Everything that is drawn: every block (a wire may touch only its own source and target block),
// every loop group's HEADER BAND (title, rule lines and exit tags - no wire ever crosses it), a loop group's box for a
// wire that has neither end inside it (a wire may cross a box only to reach a member, and then only through the border,
// never through the header), the "Inputs" label, the rings of loops drawn as rings, and any extra rectangle the caller
// passes (the legend card).
//
// ROUTER.  Forward wires are routed by A* over the grid lines of the obstacle edges (kept `CLEARANCE` px away from every
// obstacle, with a few extra lines in each gap), leaving the right side of the source and entering the left side of the
// target, at handles ordered by the other end's height.  A bend, a wire running on the line of another net and a crossing
// of another wire each cost something, so wires keep to their own lines and cross rarely.  Feedback wires (and wires
// marked `lane`) leave the bottom of their block, run in a lane at the bottom of the enclosing loop group - longest span
// lowest - and come up into the bottom of the target.  When a wire finds no clean route, `buildWiring` widens the gaps
// of the layout and routes again instead of drawing through an obstacle.
//
// CHECK.  `validateWiring` lists every wire segment that touches an obstacle it must not touch, and every pair of wires of
// different nets running on the same line (nets: wires from one source share an output bus and may overlap).
import { layoutGraph } from './systemGraphLayout.ts';
import type { Layout, LayoutOpts } from './systemGraphLayout.ts';
import type { Box, SgDoc, SgLoop } from './systemGraph.ts';
import { laneOwner } from './systemGraphView.ts';
import type { View, Wire } from './systemGraphView.ts';

export type Pt = [number, number];
export interface Route { id: string; points: Pt[]; label: Pt }
export type ViolationKind = 'block' | 'header' | 'loop' | 'label' | 'extra' | 'overlap' | 'unrouted';
export interface Violation { kind: ViolationKind; wire: string; other: string; at: Pt }
export interface Extra { id: string; box: Box }
export interface RouteOpts { extra?: readonly Extra[]; ringed?: readonly string[] }

/** px kept between a wire and everything it must not touch. */
export const CLEARANCE = 8;
const RING = 6;                       // the ring of a loop drawn as a ring reaches this far out of its members
const BEND = 14, OVERLAP = 8, CROSS = 30;
const EPS = 0.01;

// -- obstacles ------------------------------------------------------------------------------------------------

interface Obs { id: string; kind: ViolationKind; box: Box; margin: number }
interface Obstacles { others: Obs[]; own: Array<{ id: string; box: Box }> }

/** The rectangles one wire must not enter.  `margin` is the clearance around them (the router) or 0 (the check). */
export function obstaclesFor(layout: Layout, loops: readonly SgLoop[], w: Pick<Wire, 'from' | 'to'>, margin: number, o: RouteOpts = {}): Obstacles {
  const ringed = new Set<string>();
  for (const l of loops) if ((o.ringed ?? []).includes(l.id)) for (const n of l.nodes) ringed.add(n);
  const others: Obs[] = [];
  const own: Obstacles['own'] = [];
  for (const [id, box] of Object.entries(layout.nodes)) {
    if (id === w.from || id === w.to) own.push({ id, box });
    else others.push({ id, kind: 'block', box, margin: ringed.has(id) ? Math.max(margin, RING) : margin });
  }
  for (const [id, box] of Object.entries(layout.loops)) {
    const l = loops.find((x) => x.id === id);
    const head = layout.heads[id] ?? 0;
    others.push({ id, kind: 'header', box: { x: box.x, y: box.y, w: box.w, h: head }, margin });
    if (l && !l.nodes.includes(w.from) && !l.nodes.includes(w.to)) others.push({ id, kind: 'loop', box, margin });
  }
  for (const lab of layout.labels) others.push({ id: lab.id, kind: 'label', box: lab.box, margin });
  for (const e of o.extra ?? []) others.push({ id: e.id, kind: 'extra', box: e.box, margin });
  return { others, own };
}

/** Does the open segment a-b enter the interior of `box` grown by `m`? */
const enters = (a: Pt, b: Pt, box: Box, m: number): boolean =>
  Math.max(a[0], b[0]) > box.x - m + EPS && Math.min(a[0], b[0]) < box.x + box.w + m - EPS
  && Math.max(a[1], b[1]) > box.y - m + EPS && Math.min(a[1], b[1]) < box.y + box.h + m - EPS;

const cy = (b: Box): number => b.y + b.h / 2;
const cx = (b: Box): number => b.x + b.w / 2;

// -- A* over the obstacle grid --------------------------------------------------------------------------------

const DX = [1, -1, 0, 0], DY = [0, 0, 1, -1];     // right, left, down, up

class Heap {
  private k: number[] = [];
  private v: number[] = [];
  get size() { return this.k.length; }
  push(key: number, val: number) {
    let i = this.k.length;
    this.k.push(key); this.v.push(val);
    while (i > 0) {
      const p = (i - 1) >> 1;
      if (this.k[p] <= key) break;
      this.k[i] = this.k[p]; this.v[i] = this.v[p]; i = p;
    }
    this.k[i] = key; this.v[i] = val;
  }
  pop(): number {
    const top = this.v[0];
    const lk = this.k.pop()!, lv = this.v.pop()!;
    const n = this.k.length;
    if (n > 0) {
      let i = 0;
      for (;;) {
        let c = 2 * i + 1;
        if (c >= n) break;
        if (c + 1 < n && this.k[c + 1] < this.k[c]) c++;
        if (this.k[c] >= lk) break;
        this.k[i] = this.k[c]; this.v[i] = this.v[c]; i = c;
      }
      this.k[i] = lk; this.v[i] = lv;
    }
    return top;
  }
}

interface Placed { h: Array<{ y: number; x0: number; x1: number; net: string }>; v: Array<{ x: number; y0: number; y1: number; net: string }> }

function placePoints(pl: Placed, pts: readonly Pt[], net: string) {
  for (let i = 0; i + 1 < pts.length; i++) {
    const [a, b] = [pts[i], pts[i + 1]];
    if (a[1] === b[1]) pl.h.push({ y: a[1], x0: Math.min(a[0], b[0]), x1: Math.max(a[0], b[0]), net });
    else if (a[0] === b[0]) pl.v.push({ x: a[0], y0: Math.min(a[1], b[1]), y1: Math.max(a[1], b[1]), net });
  }
}

/** Cost of laying a unit step on top of / across wires already placed (other nets only). */
function crowd(pl: Placed, a: Pt, b: Pt, net: string): number {
  let c = 0;
  if (a[1] === b[1]) {
    const x0 = Math.min(a[0], b[0]), x1 = Math.max(a[0], b[0]);
    for (const s of pl.h) if (s.net !== net && Math.abs(s.y - a[1]) < 0.5) c += OVERLAP * Math.max(0, Math.min(x1, s.x1) - Math.max(x0, s.x0));
    for (const s of pl.v) if (s.net !== net && s.x > x0 + 0.5 && s.x < x1 - 0.5 && a[1] > s.y0 + 0.5 && a[1] < s.y1 - 0.5) c += CROSS;
  } else {
    const y0 = Math.min(a[1], b[1]), y1 = Math.max(a[1], b[1]);
    for (const s of pl.v) if (s.net !== net && Math.abs(s.x - a[0]) < 0.5) c += OVERLAP * Math.max(0, Math.min(y1, s.y1) - Math.max(y0, s.y0));
    for (const s of pl.h) if (s.net !== net && s.y > y0 + 0.5 && s.y < y1 - 0.5 && a[0] > s.x0 + 0.5 && a[0] < s.x1 - 0.5) c += CROSS;
  }
  return c;
}

function gridLines(vals: number[], gap = 12, maxPerGap = 4): number[] {
  const u = [...new Set(vals.map((v) => Math.round(v * 100) / 100))].sort((p, q) => p - q);
  const out = [...u];
  for (let i = 0; i + 1 < u.length; i++) {
    const g = u[i + 1] - u[i];
    const n = Math.min(maxPerGap, Math.floor(g / gap) - 1);
    for (let k = 1; k <= n; k++) out.push(u[i] + (g * k) / (n + 1));
  }
  return [...new Set(out.map((v) => Math.round(v * 100) / 100))].sort((p, q) => p - q);
}

interface Grid { xs: number[]; ys: number[]; xi: Map<number, number>; yi: Map<number, number> }

function makeGrid(layout: Layout, loops: readonly SgLoop[], ports: Pt[], o: RouteOpts): Grid {
  const xs: number[] = [], ys: number[] = [];
  const add = (b: Box, m: number) => { xs.push(b.x - m, b.x + b.w + m); ys.push(b.y - m, b.y + b.h + m); };
  const probe = obstaclesFor(layout, loops, { from: '', to: '' }, CLEARANCE, o);
  for (const ob of probe.others) add(ob.box, ob.margin);
  for (const [, b] of Object.entries(layout.nodes)) add(b, CLEARANCE);
  add({ x: layout.left, y: layout.top, w: layout.width, h: layout.height }, 60);
  for (const p of ports) { xs.push(p[0]); ys.push(p[1]); }
  const gx = gridLines(xs), gy = gridLines(ys);
  return { xs: gx, ys: gy, xi: new Map(gx.map((v, i) => [v, i])), yi: new Map(gy.map((v, i) => [v, i])) };
}

const snap = (m: Map<number, number>, v: number): number => m.get(Math.round(v * 100) / 100) ?? -1;

function astar(g: Grid, start: Pt, goal: Pt, dirStart: number, dirGoal: number, ob: Obstacles, pl: Placed, net: string): Pt[] | null {
  const nx = g.xs.length, ny = g.ys.length;
  const si = snap(g.xi, start[0]), sj = snap(g.yi, start[1]), gi = snap(g.xi, goal[0]), gj = snap(g.yi, goal[1]);
  if (si < 0 || sj < 0 || gi < 0 || gj < 0) return null;
  const key = (i: number, j: number, d: number) => ((i * ny) + j) * 4 + d;
  const dist = new Map<number, number>();
  const from = new Map<number, number>();
  const blockedMemo = new Map<number, boolean>();
  const blocked = (i: number, j: number, d: number, ni: number, nj: number): boolean => {
    const k = key(i, j, d);
    const hit = blockedMemo.get(k);
    if (hit !== undefined) return hit;
    const a: Pt = [g.xs[i], g.ys[j]], b: Pt = [g.xs[ni], g.ys[nj]];
    let r = false;
    for (const o of ob.own) if (enters(a, b, o.box, 0)) { r = true; break; }
    if (!r) for (const o of ob.others) if (enters(a, b, o.box, o.margin)) { r = true; break; }
    blockedMemo.set(k, r);
    return r;
  };
  const h = (i: number, j: number) => Math.abs(g.xs[i] - g.xs[gi]) + Math.abs(g.ys[j] - g.ys[gj]);
  const heap = new Heap();
  const s0 = key(si, sj, dirStart);
  dist.set(s0, 0);
  heap.push(h(si, sj), s0);
  let expanded = 0;
  while (heap.size) {
    const cur = heap.pop();
    const d = cur % 4, ij = (cur - d) / 4, j = ij % ny, i = (ij - j) / ny;
    const gc = dist.get(cur)!;
    if (i === gi && j === gj && d === dirGoal) {
      const pts: Pt[] = [];
      for (let k: number | undefined = cur; k !== undefined; k = from.get(k)) {
        const dd = k % 4, q = (k - dd) / 4, jj = q % ny, ii = (q - jj) / ny;
        pts.push([g.xs[ii], g.ys[jj]]);
      }
      pts.reverse();
      return pts.filter((p, n) => n === 0 || n === pts.length - 1 || !((pts[n - 1][0] === p[0] && p[0] === pts[n + 1][0]) || (pts[n - 1][1] === p[1] && p[1] === pts[n + 1][1])));
    }
    if (++expanded > 250000) return null;
    const isStart = cur === s0;
    for (let nd = 0; nd < 4; nd++) {
      if (isStart ? nd !== dirStart : (nd ^ 1) === d) continue;            // leave the port outwards; never turn back
      const ni = i + DX[nd], nj = j + DY[nd];
      if (ni < 0 || nj < 0 || ni >= nx || nj >= ny) continue;
      if (blocked(i, j, nd, ni, nj)) continue;
      const a: Pt = [g.xs[i], g.ys[j]], b: Pt = [g.xs[ni], g.ys[nj]];
      const step = Math.abs(b[0] - a[0]) + Math.abs(b[1] - a[1]);
      const nc = gc + step + (nd !== d && !isStart ? BEND : 0) + crowd(pl, a, b, net);
      const nk = key(ni, nj, nd);
      if (nc < (dist.get(nk) ?? Infinity)) {
        dist.set(nk, nc);
        from.set(nk, cur);
        heap.push(nc + h(ni, nj), nk);
      }
    }
  }
  return null;
}

// -- routing ---------------------------------------------------------------------------------------------------

/** Orthogonal routes of the wires, the extent (top, height) with the lanes, and the wires that found no clean route. */
export function routeWires(layout: Layout, loops: readonly SgLoop[], wires: readonly Wire[], o: RouteOpts = {}):
  { routes: Record<string, Route>; top: number; height: number; unrouted: string[] } {
  const N = layout.nodes;
  const routes: Record<string, Route> = {};
  const direct: Wire[] = [];
  const lane: Wire[] = [];
  for (const w of wires) {
    const a = N[w.from], b = N[w.to];
    if (!a || !b) continue;
    const sameColumn = Math.abs(a.x - b.x) < 8;                   // stacked blocks (the Inputs column): a bracket on their right side
    if (w.lane || (b.x < a.x + a.w + 8 && !sameColumn)) lane.push(w); else direct.push(w);
  }
  const bracket = new Set(direct.filter((w) => Math.abs(N[w.from].x - N[w.to].x) < 8).map((w) => w.id));

  // lanes first: they sit at fixed places (bottom strip of the owning loop, or under / over everything)
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
    const ow = w.lane ? laneOwner(loops, w) : null;
    (owners[ow && layout.loops[ow] ? ow : ''] ||= []).push(w);
    side[w.id] = 'b';
    if (!(ow && layout.loops[ow])) {
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
  for (const [k, es] of Object.entries(ends)) {
    const id = k.slice(0, k.lastIndexOf('|'));
    const mine = cx(N[id]);
    const right = es.filter((e) => e.partner >= mine).sort((p, q) => q.partner - p.partner);     // far first = leftmost
    const left = es.filter((e) => e.partner < mine).sort((p, q) => q.partner - p.partner);       // near first
    [...right, ...left].forEach((e, j, arr) => { slotX[`${e.w.id}:${e.end}`] = N[id].x + (N[id].w * (j + 1)) / (arr.length + 1); });
  }
  const placed: Placed = { h: [], v: [] };
  for (const [ow, ws] of Object.entries(owners)) {
    for (const sd of ['b', 't'] as const) {
      const g = ws.filter((w) => side[w.id] === sd);
      g.sort((p, q) => Math.abs(cx(N[q.to]) - cx(N[q.from])) - Math.abs(cx(N[p.to]) - cx(N[p.from])));
      g.forEach((w, k) => {
        let laneY: number;
        if (ow) laneY = layout.loops[ow].y + layout.loops[ow].h - 10 - 12 * k;
        else if (sd === 'b') { laneY = bottomEnd + 22 + 12 * k; bottomEnd = Math.max(bottomEnd, laneY + 14); }
        else { laneY = layout.top - 22 - 12 * k; top = Math.min(top, laneY - 14); }
        const a = N[w.from], b = N[w.to];
        const x0 = slotX[`${w.id}:from`], x1 = slotX[`${w.id}:to`];
        const ya = sd === 'b' ? a.y + a.h : a.y, yb = sd === 'b' ? b.y + b.h : b.y;
        const pts: Pt[] = [[x0, ya], [x0, laneY], [x1, laneY], [x1, yb]];
        routes[w.id] = { id: w.id, points: pts, label: [(x0 + x1) / 2, laneY] };
        placePoints(placed, pts, w.from);
      });
    }
  }

  // handles: outputs on the right ordered by the target's height, inputs on the left ordered by the source's
  const outY: Record<string, number> = {}, inY: Record<string, number> = {};
  const byFrom: Record<string, Wire[]> = {}, byTo: Record<string, Wire[]> = {};
  const byToRight: Record<string, Wire[]> = {};
  for (const w of direct) {
    (byFrom[w.from] ||= []).push(w);
    if (bracket.has(w.id)) (byToRight[w.to] ||= []).push(w); else (byTo[w.to] ||= []).push(w);
  }
  for (const [id, ws] of Object.entries(byFrom)) {
    ws.sort((p, q) => cy(N[p.to]) - cy(N[q.to]) || N[p.to].x - N[q.to].x);
    ws.forEach((w, i) => { outY[w.id] = N[id].y + (N[id].h * (i + 1)) / (ws.length + 1); });
  }
  for (const [id, ws] of Object.entries(byTo)) {
    ws.sort((p, q) => cy(N[p.from]) - cy(N[q.from]) || N[p.from].x - N[q.from].x);
    ws.forEach((w, i) => { inY[w.id] = N[id].y + (N[id].h * (i + 1)) / (ws.length + 1); });
  }
  for (const [id, ws] of Object.entries(byToRight)) {
    ws.sort((p, q) => cy(N[p.from]) - cy(N[q.from]));
    ws.forEach((w, i) => { inY[w.id] = N[id].y + (N[id].h * (i + 1)) / (ws.length + 1); });
  }
  const goalX = (w: Wire) => (bracket.has(w.id) ? N[w.to].x + N[w.to].w : N[w.to].x);
  const ports: Pt[] = [];
  for (const w of direct) ports.push([N[w.from].x + N[w.from].w, outY[w.id]], [goalX(w), inY[w.id]]);
  const grid = makeGrid(layout, loops, ports, o);
  const unrouted: string[] = [];
  const order = [...direct].sort((p, q) => {
    const len = (w: Wire) => Math.abs(goalX(w) - (N[w.from].x + N[w.from].w)) + Math.abs(inY[w.id] - outY[w.id]);
    return len(p) - len(q) || p.id.localeCompare(q.id);
  });
  for (const w of order) {
    const a = N[w.from];
    const br = bracket.has(w.id);
    const start: Pt = [a.x + a.w, outY[w.id]], goal: Pt = [goalX(w), inY[w.id]];
    const ob = obstaclesFor(layout, loops, w, CLEARANCE, o);
    let pts = astar(grid, start, goal, 0, br ? 1 : 0, ob, placed, w.from);
    if (!pts) {
      unrouted.push(w.id);
      const xm = br ? goal[0] + 18 : goal[0] - 18;                                    // last resort: the plain three-segment wire (reported)
      pts = Math.abs(start[1] - goal[1]) < 1 ? [start, goal] : [start, [xm, start[1]], [xm, goal[1]], goal];
    }
    placePoints(placed, pts, w.from);
    let best: Pt = [(start[0] + goal[0]) / 2, start[1]], len = -1;
    for (let k = 0; k + 1 < pts.length; k++) {
      const l = Math.abs(pts[k + 1][0] - pts[k][0]);
      if (pts[k][1] === pts[k + 1][1] && l > len) { len = l; best = [(pts[k][0] + pts[k + 1][0]) / 2, pts[k][1]]; }
    }
    routes[w.id] = { id: w.id, points: pts, label: best };
  }
  return { routes, top, height: bottomEnd - top, unrouted };
}

// -- the check ---------------------------------------------------------------------------------------------------

/** Every wire segment that touches an obstacle it must not touch, and every collinear overlap of two wires of different nets.
 *  (`unrouted` wires are reported too.)  Empty = nothing is drawn through anything. */
export function validateWiring(layout: Layout, loops: readonly SgLoop[], wires: readonly Wire[], routes: Record<string, Route>, o: RouteOpts = {}): Violation[] {
  const out: Violation[] = [];
  for (const w of wires) {
    const r = routes[w.id];
    if (!r) { out.push({ kind: 'unrouted', wire: w.id, other: '', at: [0, 0] }); continue; }
    const ob = obstaclesFor(layout, loops, w, 0, o);
    for (let i = 0; i + 1 < r.points.length; i++) {
      const a = r.points[i], b = r.points[i + 1];
      const at: Pt = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
      for (const x of ob.own) if (enters(a, b, x.box, 0)) out.push({ kind: 'block', wire: w.id, other: x.id, at });
      for (const x of ob.others) if (enters(a, b, x.box, x.margin)) out.push({ kind: x.kind, wire: w.id, other: x.id, at });
    }
  }
  const segs = wires.filter((w) => routes[w.id]).map((w) => ({ w, pts: routes[w.id].points }));
  for (let p = 0; p < segs.length; p++) {
    for (let q = p + 1; q < segs.length; q++) {
      if (segs[p].w.from === segs[q].w.from) continue;                 // one output bus
      const A = segs[p].pts, B = segs[q].pts;
      for (let i = 0; i + 1 < A.length; i++) {
        for (let j = 0; j + 1 < B.length; j++) {
          const horiz = A[i][1] === A[i + 1][1] && B[j][1] === B[j + 1][1] && Math.abs(A[i][1] - B[j][1]) < 0.5;
          const vert = A[i][0] === A[i + 1][0] && B[j][0] === B[j + 1][0] && Math.abs(A[i][0] - B[j][0]) < 0.5;
          if (!horiz && !vert) continue;
          const k = horiz ? 0 : 1;
          const lo = Math.max(Math.min(A[i][k], A[i + 1][k]), Math.min(B[j][k], B[j + 1][k]));
          const hi = Math.min(Math.max(A[i][k], A[i + 1][k]), Math.max(B[j][k], B[j + 1][k]));
          if (hi - lo > 1) out.push({ kind: 'overlap', wire: segs[p].w.id, other: segs[q].w.id, at: horiz ? [(lo + hi) / 2, A[i][1]] : [A[i][0], (lo + hi) / 2] });
        }
      }
    }
  }
  return out;
}

/** Counts of violations by kind. */
export function summarize(v: readonly Violation[]): Record<string, number> {
  const out: Record<string, number> = {};
  for (const x of v) out[x.kind] = (out[x.kind] ?? 0) + 1;
  return out;
}

// -- layout + routing + check ---------------------------------------------------------------------------------------

export interface Wiring { layout: Layout; routes: Record<string, Route>; violations: Violation[]; widened: number }

/** Lay the blocks out, route the wires and check them; while the check finds anything, widen the gaps (columns and rows)
 *  and try again - a wire is never drawn through an obstacle to save space. */
export function buildWiring(doc: SgDoc, view: View, lo: LayoutOpts, o: RouteOpts = {}, steps = 4): Wiring {
  let best: Wiring | null = null;
  for (let s = 0; s < steps; s++) {
    const lay0 = layoutGraph(doc, { ...lo, extraCol: 26 * s, extraRow: 22 * s });
    const r = routeWires(lay0, doc.loops, view.wires, o);
    const layout: Layout = { ...lay0, top: r.top, height: r.height };
    const violations = validateWiring(layout, doc.loops, view.wires, r.routes, o);
    const cand = { layout, routes: r.routes, violations, widened: s };
    if (!best || violations.length < best.violations.length) best = cand;
    if (!violations.length) break;
  }
  return best!;
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
