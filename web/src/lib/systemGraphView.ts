// What the System view draws, derived from the backend graph: one WIRE per block pair (parallel edges merged,
// with a count), parameter-like links turned into CHIPS on the consuming block, and the status wording.
// Pure: web/src/lib/__tests__/systemGraphView.test.mjs imports this file.
import type { SgDoc, SgEdge, SgLoop, SgNode, SgStatus } from './systemGraph.ts';

export interface Wire {
  id: string;
  from: string;
  to: string;
  /** the merged data items (one per original edge) - the hover lists them */
  items: SgEdge[];
  /** one type when all items share it, else `mixed` */
  type: string;
  count: number;
  feedback: boolean;
  /** routed in a lane under a loop group (feedback, or an edge marked `route: lane`) */
  lane: boolean;
}

export interface Chip {
  id: string;
  /** the context block it comes from, else null (a minor link: `label` says what it is) */
  source: string | null;
  label: string | null;
  type: string;
  items: SgEdge[];
}

export interface View {
  wires: Wire[];
  /** consumer node id -> its chips (empty when all wires are shown) */
  chips: Record<string, Chip[]>;
}

export const contextIds = (doc: SgDoc): Set<string> => new Set(doc.nodes.filter((n) => n.context).map((n) => n.id));

/** Parameter-like: from a context block, or marked minor.  Feedback wires are always wires. */
const isChipEdge = (e: SgEdge, ctx: ReadonlySet<string>): boolean => !e.feedback && (ctx.has(e.from.node) || !!e.minor);

/** The wires and chips of a graph.  `showAll` = every link is a wire (no chips). */
export function buildView(doc: SgDoc, showAll: boolean): View {
  const ctx = contextIds(doc);
  const wires = new Map<string, Wire>();
  const chips: Record<string, Map<string, Chip>> = {};
  for (const e of doc.edges) {
    if (e.from.node === e.to.node) continue;
    if (!showAll && isChipEdge(e, ctx)) {
      if (ctx.has(e.to.node)) continue;                    // a context block feeding another: shown in its hover only
      const m = (chips[e.to.node] ||= new Map());
      const src = ctx.has(e.from.node) ? e.from.node : null;
      const label = src ? null : (e.label ?? e.type);
      const key = `${src ?? ''}|${label ?? ''}`;
      const c = m.get(key);
      if (c) c.items.push(e);
      else m.set(key, { id: `${e.to.node}<${key}`, source: src, label, type: e.type, items: [e] });
      continue;
    }
    const lane = e.feedback || e.route === 'lane';
    const key = `${e.from.node}>${e.to.node}${lane ? '~' : ''}`;
    const w = wires.get(key);
    if (w) {
      w.items.push(e);
      w.count += 1;
      if (w.type !== e.type) w.type = 'mixed';
    } else {
      wires.set(key, { id: key, from: e.from.node, to: e.to.node, items: [e], type: e.type, count: 1, feedback: e.feedback, lane });
    }
  }
  const out: Record<string, Chip[]> = {};
  for (const [k, m] of Object.entries(chips)) out[k] = [...m.values()];
  return { wires: [...wires.values()], chips: out };
}

/** The loop whose bottom strip carries a lane wire: the smallest loop holding both ends, else (the target lies outside)
 *  the smallest loop holding the source. */
export function laneOwner(loops: readonly SgLoop[], w: Pick<Wire, 'from' | 'to'>): string | null {
  const small = (ls: SgLoop[]) => ls.sort((a, b) => a.nodes.length - b.nodes.length)[0]?.id ?? null;
  return small(loops.filter((l) => l.nodes.includes(w.from) && l.nodes.includes(w.to)))
    ?? small(loops.filter((l) => l.nodes.includes(w.from)));
}

/** Lane wires owned by each loop (the loop box gets a strip that tall). */
export function laneCounts(loops: readonly SgLoop[], wires: readonly Wire[]): Record<string, number> {
  const out: Record<string, number> = {};
  for (const w of wires) {
    if (!w.lane) continue;
    const o = laneOwner(loops, w);
    if (o) out[o] = (out[o] ?? 0) + 1;
  }
  return out;
}

export function chipCounts(doc: SgDoc, view: View): Record<string, number> {
  const out: Record<string, number> = {};
  for (const n of doc.nodes) out[n.id] = view.chips[n.id]?.length ?? 0;
  return out;
}

/** Blocks whose wires / chips touch `id`: what stays lit when `id` is hovered. */
export function neighbours(view: View, id: string): { wires: Set<string>; nodes: Set<string> } {
  const wires = new Set<string>();
  const nodes = new Set<string>([id]);
  for (const w of view.wires) {
    if (w.from === id || w.to === id) { wires.add(w.id); nodes.add(w.from); nodes.add(w.to); }
  }
  for (const [consumer, cs] of Object.entries(view.chips)) {
    if (consumer === id) for (const c of cs) if (c.source) nodes.add(c.source);
    for (const c of cs) if (c.source === id) nodes.add(consumer);
  }
  return { wires, nodes };
}

// -- status wording -----------------------------------------------------------------------------------------

/** What a block's badge says.  With no motor selected there is NO badge; with a motor selected a block that has no
 *  record reads "no run yet" (never "unknown"); done / running / error / cached keep their words. */
export type Badge = { kind: 'none' } | { kind: 'norun' } | { kind: 'state'; state: 'done' | 'running' | 'error' | 'cached' };

export function badgeOf(status: SgStatus | undefined, hasMotor: boolean): Badge {
  if (!hasMotor) return { kind: 'none' };
  const s = status?.state;
  if (s === 'done' || s === 'running' || s === 'error' || s === 'cached') return { kind: 'state', state: s };
  return { kind: 'norun' };
}

export const blockHasStatus = (n: SgNode): boolean => !!n.status && n.status.state !== 'unknown';
