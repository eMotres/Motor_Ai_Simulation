// Auto layout of the System graph with dagre (left to right).
//
// Each loop that can be drawn as a box (a laminar family, see splitLoops) is laid out ON ITS OWN, innermost
// first, and enters its parent's layout as one block - so a loop's members sit together and no foreign node can
// land inside its box (dagre's own cluster mode was unusable here: it tripled the width).  Feedback edges are
// left out of the ranking so the flow reads left to right.
import dagre from '@dagrejs/dagre';
import { nesting, splitLoops } from './systemGraph.ts';
import type { Box, SgDoc, SgLoop } from './systemGraph.ts';

export const NODE_W = 196;
export const NODE_H = 64;
const PAD = 16;       // between a loop's border and its content
const HEAD = 24;      // the loop's label strip

export interface Layout {
  nodes: Record<string, Box>;
  loops: Record<string, Box>;
  /** loops drawn as a ring on their members instead of a box */
  unboxed: string[];
  width: number;
  height: number;
}

interface Sub { nodes: Record<string, Box>; loops: Record<string, Box>; w: number; h: number }
interface Item { id: string; w: number; h: number }

function place(items: Item[], links: Array<[string, string]>): { pos: Record<string, { x: number; y: number }>; w: number; h: number } {
  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: 'LR', nodesep: 26, ranksep: 54, marginx: 0, marginy: 0 });
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

function level(doc: SgDoc, loop: SgLoop | null, boxed: SgLoop[], parent: Record<string, string | null>): Sub {
  const members = loop ? new Set(loop.nodes) : new Set(doc.nodes.map((n) => n.id));
  const kids = boxed.filter((l) => (parent[l.id] ?? null) === (loop ? loop.id : null));
  const inKid: Record<string, string> = {};
  for (const k of kids) for (const n of k.nodes) inKid[n] = k.id;
  const subs: Record<string, Sub> = {};
  const items: Item[] = [];
  for (const n of doc.nodes) if (members.has(n.id) && !inKid[n.id]) items.push({ id: n.id, w: NODE_W, h: NODE_H });
  for (const k of kids) {
    subs[k.id] = level(doc, k, boxed, parent);
    items.push({ id: `loop:${k.id}`, w: subs[k.id].w + 2 * PAD, h: subs[k.id].h + 2 * PAD + HEAD });
  }
  const itemOf = (id: string): string | null => (!members.has(id) ? null : inKid[id] ? `loop:${inKid[id]}` : id);
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

/** Positions of every node and loop box (top-left corner + size). */
export function layoutGraph(doc: SgDoc): Layout {
  const { boxed, unboxed } = splitLoops(doc.loops);
  const { parent } = nesting(boxed);
  const top = level(doc, null, boxed, parent);
  return { nodes: top.nodes, loops: top.loops, unboxed: unboxed.map((l) => l.id), width: top.w, height: top.h };
}
