// Layout and orthogonal wire routing of the System graph.
//
// BLOCKS.  Context inputs (parameter sources) stand in a compact column at the left.  When every other block
// carries a `layout` hint {col, row} (the propeller S1 view) the blocks are placed on that grid: the main chain on
// one row, helper blocks above / below it.  Without hints (the modules view) dagre ranks the blocks, each loop
// laid out on its own and placed as one block, so no foreign block can land inside a loop's box.
//
// The wires are routed by systemGraphRoute.ts.
import dagre from '@dagrejs/dagre';
import { nesting, splitLoops } from './systemGraph.ts';
import type { Box, SgDoc, SgLoop, SgNode } from './systemGraph.ts';

export const NODE_W = 200;
export const NODE_H = 62;
export const CHIP_ROW = 20;
export const COND_ROW = 14;
export const COMPACT_H = 40;
export const COL_PITCH = 262;
export const ROW_PITCH = 100;
const PAD = 16;       // between a loop's border and its content (fallback layout)
const HEAD = 24;      // the loop's label strip
const CTX_PITCH = COMPACT_H + 12;

/** Height of a block: context blocks are compact, others grow by one row per two chips. */
export function blockHeight(n: Pick<SgNode, 'context' | 'condition'>, chips: number): number {
  if (n.context) return COMPACT_H;
  return NODE_H + (n.condition ? COND_ROW : 0) + (chips > 0 ? Math.ceil(chips / 2) * CHIP_ROW : 0);
}

export interface LayoutOpts {
  /** block id -> height (blockHeight) */
  heights?: Record<string, number>;
  /** loop id -> number of lane wires under it */
  lanes?: Record<string, number>;
  /** loop id -> height of its header strip (the rule text lines) */
  heads?: Record<string, number>;
  /** extra px between columns / between rows (the router asks for more room when a wire finds no clean route) */
  extraCol?: number;
  extraRow?: number;
}

export interface Layout {
  nodes: Record<string, Box>;
  loops: Record<string, Box>;
  /** loops drawn as a ring on their members instead of a box */
  unboxed: string[];
  /** the "Inputs" label position (null when there is no context block) */
  inputs: { x: number; y: number } | null;
  /** loop id -> height of its header band (title, rule lines, exit tags): nothing may be drawn through it */
  heads: Record<string, number>;
  /** text labels that are obstacles too (the "Inputs" label) */
  labels: Array<{ id: string; box: Box }>;
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
function boxLoops(boxed: SgLoop[], nodes: Record<string, Box>, lanes: Record<string, number>, heads: Record<string, number>): Record<string, Box> {
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
    const head = heads[l.id] ?? HEAD;
    out[l.id] = { x: u.x - side, y: u.y - side - head, w: u.w + 2 * side, h: u.h + 2 * side + head + strip };
  }
  return out;
}

// -- block placement ------------------------------------------------------------------------------------------

interface Item { id: string; w: number; h: number }
interface Sub { nodes: Record<string, Box>; loops: Record<string, Box>; w: number; h: number }

function place(items: Item[], links: Array<[string, string]>, extra = 0): { pos: Record<string, { x: number; y: number }>; w: number; h: number } {
  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: 'LR', nodesep: 30 + extra, ranksep: 70 + extra, marginx: 0, marginy: 0 });
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

function level(doc: SgDoc, skip: Set<string>, hs: Record<string, number>, heads: Record<string, number>, loop: SgLoop | null, boxed: SgLoop[],
               parent: Record<string, string | null>, extra: number): Sub {
  const members = loop ? new Set(loop.nodes) : new Set(doc.nodes.map((n) => n.id));
  const kids = boxed.filter((l) => (parent[l.id] ?? null) === (loop ? loop.id : null));
  const inKid: Record<string, string> = {};
  for (const k of kids) for (const n of k.nodes) inKid[n] = k.id;
  const subs: Record<string, Sub> = {};
  const items: Item[] = [];
  for (const n of doc.nodes) if (members.has(n.id) && !inKid[n.id] && !skip.has(n.id)) items.push({ id: n.id, w: NODE_W, h: hs[n.id] ?? NODE_H });
  for (const k of kids) {
    subs[k.id] = level(doc, skip, hs, heads, k, boxed, parent, extra);
    items.push({ id: `loop:${k.id}`, w: subs[k.id].w + 2 * PAD, h: subs[k.id].h + 2 * PAD + (heads[k.id] ?? HEAD) });
  }
  const itemOf = (id: string): string | null => (!members.has(id) || skip.has(id) ? null : inKid[id] ? `loop:${inKid[id]}` : id);
  const links: Array<[string, string]> = [];
  for (const e of doc.edges) {
    if (e.feedback) continue;
    const a = itemOf(e.from.node), b = itemOf(e.to.node);
    if (a && b) links.push([a, b]);
  }
  const { pos, w, h } = place(items, links, extra);
  const out: Sub = { nodes: {}, loops: {}, w, h };
  for (const it of items) {
    const p = pos[it.id];
    if (it.id.startsWith('loop:')) {
      const id = it.id.slice(5);
      out.loops[id] = { x: p.x, y: p.y, w: it.w, h: it.h };
      const dx = p.x + PAD, dy = p.y + PAD + (heads[id] ?? HEAD);
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
  const manual = rest.some((n) => n.layout);                   // blocks without a hint go to a row under the hinted ones
  const { boxed, unboxed } = splitLoops(doc.loops);
  const nodes: Record<string, Box> = {};
  let loops: Record<string, Box> = {};
  let inputs: Layout['inputs'] = null;
  if (manual) {
    const colP = COL_PITCH + (opts.extraCol ?? 0), rowP = ROW_PITCH + (opts.extraRow ?? 0);
    const lastRow = Math.max(0, ...rest.filter((n) => n.layout).map((n) => n.layout!.row));
    let free = 0;
    for (const n of rest) {
      const h = hs[n.id] ?? NODE_H;
      const at = n.layout ?? { col: 1 + (free++), row: lastRow + 1.8 };
      nodes[n.id] = { x: at.col * colP, y: at.row * rowP - h / 2, w: NODE_W, h };
    }
    const y0 = -(ctx.length * CTX_PITCH) / 2;
    ctx.forEach((n, i) => { nodes[n.id] = { x: 0, y: y0 + i * CTX_PITCH, w: NODE_W, h: COMPACT_H }; });
    if (ctx.length) inputs = { x: 0, y: y0 - 22 };
    loops = boxLoops(boxed, nodes, lanes, opts.heads ?? {});
  } else {
    const skip = new Set(ctx.map((n) => n.id));
    const { parent } = nesting(boxed);
    const top = level(doc, skip, hs, opts.heads ?? {}, null, boxed, parent, Math.max(opts.extraCol ?? 0, opts.extraRow ?? 0));
    const dx = ctx.length ? NODE_W + 70 : 0;
    for (const [id, b] of Object.entries(top.nodes)) nodes[id] = { ...b, x: b.x + dx };
    for (const [id, b] of Object.entries(top.loops)) loops[id] = { ...b, x: b.x + dx };
    ctx.forEach((n, i) => { nodes[n.id] = { x: 0, y: 26 + i * CTX_PITCH, w: NODE_W, h: COMPACT_H }; });
    if (ctx.length) inputs = { x: 0, y: 4 };
  }
  const all = [...Object.values(nodes), ...Object.values(loops)];
  const u = all.length ? union(all) : { x: 0, y: 0, w: 0, h: 0 };
  const heads: Record<string, number> = {};
  for (const id of Object.keys(loops)) heads[id] = (opts.heads ?? {})[id] ?? HEAD;
  const labels = inputs ? [{ id: 'inputs', box: { x: inputs.x, y: inputs.y, w: 90, h: 18 } }] : [];
  return { nodes, loops, unboxed: unboxed.map((l) => l.id), inputs, heads, labels, left: u.x, top: u.y, width: u.w, height: u.h, manual };
}
