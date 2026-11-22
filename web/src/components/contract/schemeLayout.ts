// The layout of "How it works" (owner 2026-11-22: «используй тот же вид, как и везде» - the look of the assembly process in
// Production, web/src/modules/production/components/AssemblyProcess.tsx): equal cards on a grid, orthogonal connectors
// leaving and entering through ports spread along the facing side, one label chip in the middle of each connector.
// Pure functions, so the same rules are tested without a browser (and mirrored by the layout check of the stored files).
import type { SchemeData, SchemeEdge, SchemeNode } from './contractModel';

export const CARD_W = 230;
export const CARD_H = 96;
export const COL_GAP = 400;            // card left edge to the next column's card left edge
export const ROW_GAP = 180;
export const PAIR_GAP = 26;            // the middle segments of connectors between one pair of cards
export const MAX_PERMUTED = 6;         // the connectors of one pair tried in every order up to this many (6! = 720)
export const LABEL_STEP = 44;          // the labels of connectors between one pair of cards, along their middle segments
export const CHIP_MAX_W = 180;         // a label chip wraps at this width: two lines at most for every label of the schemes
export type Side = 'left' | 'right' | 'top' | 'bottom';

export interface Placed { id: string; col: number; row: number; x: number; y: number }
export interface Port { side: Side; frac: number; x: number; y: number; handle: string }
export interface Routed { edge: SchemeEdge; source: Port; target: Port; centerX: number; centerY: number; labelX: number; labelY: number }

/** The rank of each value among the distinct values (old files carry x / y, not col / row): distinct x, distinct column. */
function ranks(values: number[]): number[] {
  const sorted = [...new Set(values)].sort((a, b) => a - b);
  return values.map((v) => sorted.indexOf(v));
}

const hasCell = (n: SchemeNode) => Number.isInteger(n.col) && Number.isInteger(n.row);

/** Every card's grid cell: its own ``col`` / ``row`` when it has them (always kept); an older node's ``x`` / ``y`` mapped by
 *  rank per axis; a cell already taken moves the later card to the next free cell along its row (then the rows below). */
export function placeNodes(nodes: readonly SchemeNode[]): Placed[] {
  const legacy = nodes.filter((n) => !hasCell(n));
  const lc = ranks(legacy.map((n) => Number(n.x) || 0));
  const lr = ranks(legacy.map((n) => Number(n.y) || 0));
  const want = new Map<string, [number, number]>();
  legacy.forEach((n, i) => want.set(n.id, [lc[i], lr[i]]));
  const taken = new Set<string>();
  const cell = new Map<string, [number, number]>();
  const claim = (id: string, c: number, r: number) => {
    let col = c; let row = r;
    while (taken.has(`${col},${row}`)) {
      col += 1;
      if (col > c + nodes.length) { col = c; row += 1; }
    }
    taken.add(`${col},${row}`);
    cell.set(id, [col, row]);
  };
  nodes.filter(hasCell).forEach((n) => claim(n.id, n.col as number, n.row as number));   // explicit cells first
  legacy.forEach((n) => { const [c, r] = want.get(n.id)!; claim(n.id, c, r); });
  return nodes.map((n) => {
    const [col, row] = cell.get(n.id)!;
    return { id: n.id, col, row, x: col * COL_GAP, y: row * ROW_GAP };
  });
}

function sideOf(a: Placed, b: Placed): Side {
  if (a.col !== b.col) return b.col > a.col ? 'right' : 'left';
  return b.row > a.row ? 'bottom' : 'top';
}

const opposite: Record<Side, Side> = { left: 'right', right: 'left', top: 'bottom', bottom: 'top' };

/** Route every edge: the facing sides (columns differ: left / right, else top / bottom), the ports spread along each side
 *  in the order of the neighbour they lead to (so connectors do not cross at a card), and the middle segment of
 *  connectors between the same pair of cards set apart by PAIR_GAP. */
export function route(data: SchemeData): { placed: Placed[]; routed: Routed[] } {
  const placed = placeNodes(data.nodes);
  const at = new Map(placed.map((p) => [p.id, p]));
  const ends: Array<{ node: string; side: Side; other: Placed; edge: number; role: 'source' | 'target' }> = [];
  data.edges.forEach((e, i) => {
    const a = at.get(e.from); const b = at.get(e.to);
    if (!a || !b) return;
    const s = sideOf(a, b);
    ends.push({ node: a.id, side: s, other: b, edge: i, role: 'source' });
    ends.push({ node: b.id, side: opposite[s], other: a, edge: i, role: 'target' });
  });
  const ports = new Map<string, Port>();
  const bySide = new Map<string, typeof ends>();
  for (const e of ends) bySide.set(`${e.node}|${e.side}`, [...(bySide.get(`${e.node}|${e.side}`) ?? []), e]);
  for (const [key, list] of bySide) {
    const [node, side] = key.split('|') as [string, Side];
    const p = at.get(node)!;
    const along = (o: Placed) => (side === 'left' || side === 'right' ? o.y : o.x);
    list.sort((u, v) => along(u.other) - along(v.other) || u.edge - v.edge);
    list.forEach((e, k) => {
      const frac = (k + 1) / (list.length + 1);
      const x = side === 'left' ? p.x : side === 'right' ? p.x + CARD_W : p.x + frac * CARD_W;
      const y = side === 'top' ? p.y : side === 'bottom' ? p.y + CARD_H : p.y + frac * CARD_H;
      ports.set(`${e.edge}|${e.role}`, { side, frac, x, y, handle: `${side}-${k}-${e.role}` });
    });
  }
  const pairIndex = new Map<string, number[]>();
  data.edges.forEach((e, i) => { const k = [e.from, e.to].sort().join('|'); pairIndex.set(k, [...(pairIndex.get(k) ?? []), i]); });
  const make = (i: number, slot: number, n: number): Routed | null => {
    const edge = data.edges[i];
    const source = ports.get(`${i}|source`); const target = ports.get(`${i}|target`);
    if (!source || !target) return null;
    const shift = (slot - (n - 1) / 2) * PAIR_GAP;
    const horizontal = source.side === 'left' || source.side === 'right';
    const centerX = horizontal ? (source.x + target.x) / 2 + shift : (source.x + target.x) / 2;
    const centerY = horizontal ? (source.y + target.y) / 2 : (source.y + target.y) / 2 + shift;
    return { edge, source, target, centerX, centerY,
      labelX: horizontal ? centerX : (source.x + target.x) / 2, labelY: horizontal ? (source.y + target.y) / 2 : centerY };
  };
  const out = new Map<number, Routed>();
  for (const group of pairIndex.values()) {
    // the order of the middle segments that keeps the pair's connectors from crossing each other (the first that works)
    // (more connectors than MAX_PERMUTED: the order they come in, never a factorial search)
    let best: Routed[] | null = null;
    const identity = group.map((_, k) => k);
    for (const order of group.length <= MAX_PERMUTED ? permutations(identity) : [identity]) {
      const rs = group.map((i, k) => make(i, order[k], group.length)).filter((r): r is Routed => !!r);
      if (!best) best = rs;
      if (!rs.some((a, x) => rs.some((b, y) => y > x && crosses(polyline(a), polyline(b))))) { best = rs; break; }
    }
    // the pair's labels evenly around their common middle, in the order of the connectors (never moved towards each other)
    const rs = best ?? [];
    if (rs.length > 1) {
      const hor = (r: Routed) => r.source.side === 'left' || r.source.side === 'right';
      const key = (r: Routed) => (hor(r) ? r.labelY : r.labelX);
      const mid = rs.reduce((a, r) => a + key(r), 0) / rs.length;
      [...rs].sort((a, b) => key(a) - key(b) || data.edges.indexOf(a.edge) - data.edges.indexOf(b.edge)).forEach((r, k) => {
        const v = mid + (k - (rs.length - 1) / 2) * LABEL_STEP;
        if (hor(r)) r.labelY = v; else r.labelX = v;
      });
    }
    rs.forEach((r) => out.set(data.edges.indexOf(r.edge), r));
  }
  const routed = data.edges.map((_, i) => out.get(i)).filter((r): r is Routed => !!r);
  return { placed, routed };
}

/** The drawing's extent (cards plus a margin for the connectors' outer turns and the chips): the panel's aspect ratio. */
export function extent(placed: readonly Placed[]): { w: number; h: number } {
  if (!placed.length) return { w: CARD_W, h: CARD_H };
  const w = Math.max(...placed.map((p) => p.x)) - Math.min(...placed.map((p) => p.x)) + CARD_W;
  const h = Math.max(...placed.map((p) => p.y)) - Math.min(...placed.map((p) => p.y)) + CARD_H;
  return { w: w + 40, h: h + 40 };
}

/** The panel height that shows the whole drawing at the panel's width (no tall empty area), within ``[min, max]``. */
export function panelHeight(width: number, ext: { w: number; h: number }, min = 160, max = 560): number {
  if (!(width > 0)) return 360;
  return Math.round(Math.min(max, Math.max(min, (width * ext.h) / ext.w)));
}

/** The orthogonal path of a routed edge as points (what smoothstep draws, without the rounded corners). */
export function polyline(r: Routed): Array<[number, number]> {
  const { source: s, target: t } = r;
  if (s.side === 'left' || s.side === 'right') return [[s.x, s.y], [r.centerX, s.y], [r.centerX, t.y], [t.x, t.y]];
  return [[s.x, s.y], [s.x, r.centerY], [t.x, r.centerY], [t.x, t.y]];
}

/** Every order of ``xs``, lazily (the first order that routes without crossings ends the search). */
function* permutations(xs: number[]): Generator<number[]> {
  if (xs.length <= 1) { yield xs; return; }
  for (let i = 0; i < xs.length; i += 1) {
    for (const p of permutations([...xs.slice(0, i), ...xs.slice(i + 1)])) yield [xs[i], ...p];
  }
}

type Pt = [number, number];

/** Do two axis-aligned segments cross or run along each other (touching at an end does not count)? */
function segmentsMeet(p: Pt, q: Pt, r: Pt, s: Pt): boolean {
  const vert = (a: Pt, b: Pt) => a[0] === b[0];
  if (vert(p, q) && vert(r, s)) {
    return Math.abs(p[0] - r[0]) < 1 && Math.min(Math.max(p[1], q[1]), Math.max(r[1], s[1])) - Math.max(Math.min(p[1], q[1]), Math.min(r[1], s[1])) > 1;
  }
  if (!vert(p, q) && !vert(r, s)) {
    return Math.abs(p[1] - r[1]) < 1 && Math.min(Math.max(p[0], q[0]), Math.max(r[0], s[0])) - Math.max(Math.min(p[0], q[0]), Math.min(r[0], s[0])) > 1;
  }
  const [h1, h2, v1, v2] = vert(p, q) ? [r, s, p, q] : [p, q, r, s];
  return Math.min(h1[0], h2[0]) + 1 < v1[0] && v1[0] < Math.max(h1[0], h2[0]) - 1
    && Math.min(v1[1], v2[1]) + 1 < h1[1] && h1[1] < Math.max(v1[1], v2[1]) - 1;
}

/** Do two polylines cross anywhere? */
export function crosses(a: Pt[], b: Pt[]): boolean {
  for (let i = 0; i < a.length - 1; i += 1) {
    for (let j = 0; j < b.length - 1; j += 1) {
      if (a[i][0] === a[i + 1][0] && a[i][1] === a[i + 1][1]) continue;
      if (b[j][0] === b[j + 1][0] && b[j][1] === b[j + 1][1]) continue;
      if (segmentsMeet(a[i], a[i + 1], b[j], b[j + 1])) return true;
    }
  }
  return false;
}
