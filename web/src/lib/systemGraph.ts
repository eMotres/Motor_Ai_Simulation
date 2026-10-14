// The "System" graph (Simulations > System, owner 2026-10-14): types of GET /api/system/graph and the
// pure helpers the view uses (colours, loop nesting, loop boxes).  Schema: docs/SYSTEM_GRAPH.md.
//
// Pure, no imports: web/src/lib/__tests__/systemGraph.test.mjs imports this file.


export type SgState = 'done' | 'running' | 'error' | 'cached' | 'unknown';

export interface SgPort { port: string; type: string }
export interface SgSource { function: string; file: string; line: number }
export interface SgStatus {
  state: SgState;
  time_s?: number | null;
  finished_at?: number | string | null;
  summary?: string;
  source?: string;
  values?: Record<string, unknown>;
}
export interface SgNode {
  id: string;
  kind: string;
  group?: string;
  title: string;
  summary: string;
  inputs: SgPort[];
  outputs: SgPort[];
  source: SgSource | null;
  status: SgStatus;
  capability?: string;
  trust?: string;
  roadmap?: boolean;
  /** a parameter source feeding many blocks (motor model, limits, ...): drawn in the Inputs column, chips on its consumers */
  context?: boolean;
  /** optional layout hint: column and row (1 row = one block pitch); every non-context node needs one for the manual layout */
  layout?: { col: number; row: number } | null;
}
export interface SgEnd { node: string; port: string | null }
export interface SgEdge {
  id: string;
  from: SgEnd;
  to: SgEnd;
  type: string;
  feedback: boolean;
  via: string;
  label?: string;
  values?: Record<string, unknown>;
  /** a parameter-like link drawn as a chip on the target unless all wires are shown */
  minor?: boolean;
  /** `lane`: routed under the loop group (like a feedback wire) instead of across the blocks */
  route?: 'direct' | 'lane';
}
export interface SgLoop {
  id: string;
  kind: 'fixed_point' | 'search';
  title: string;
  nodes: string[];
  edges: string[];
  max_iterations: number | null;
  tolerance: number | null;
  tolerance_unit: string | null;
  detail?: Record<string, unknown>;
  source?: SgSource | null;
  /** stage 2: the iteration count / residual of the last run, when a record carries it */
  convergence?: { iterations?: number; residual?: number } | null;
}
export interface SgDoc {
  version: string;
  view: string;
  title: string;
  nodes: SgNode[];
  edges: SgEdge[];
  loops: SgLoop[];
  notes: string[];
  status_context: Record<string, string> | null;
}

export const STATE_COLORS: Readonly<Record<SgState, string>> = {
  done: '#22c55e', running: '#3b82f6', error: '#ef4444', cached: '#a78bfa', unknown: '#9ca3af',
};

/** Wire colours by contract / data type; any other type gets a stable hue from its name. */
const TYPE_COLORS: Readonly<Record<string, string>> = {
  GeometryIR: '#0ea5e9', MeshIR: '#14b8a6', ResultIR: '#f59e0b', MachineState: '#ec4899',
  ControlSignal: '#f43f5e', ParameterSet: '#8b5cf6', Excitation: '#84cc16', CostIR: '#eab308',
  MaterialIR: '#a16207', TransmissionIR: '#64748b', dependency: '#94a3b8',
  rpm: '#6366f1', torque: '#f97316', current: '#ef4444', temperature: '#f43f5e', loss_W: '#d946ef',
  power: '#eab308', air_speed: '#06b6d4', heat_transfer: '#22d3ee', air_density: '#38bdf8',
  slipstream_factor: '#0891b2', passport_grid: '#10b981', thermal_geometry: '#65a30d',
  thermal_calibration: '#16a34a', limits: '#dc2626', limit: '#dc2626', drive: '#7c3aed', pack: '#a855f7',
  verdict: '#475569', s1_rating: '#2563eb', mixed: '#94a3b8',
};

export function typeColor(type: string): string {
  const c = TYPE_COLORS[type];
  if (c) return c;
  let h = 0;
  for (let i = 0; i < type.length; i++) h = (h * 31 + type.charCodeAt(i)) % 360;
  return `hsl(${h} 60% 50%)`;
}

export const KIND_COLORS: Readonly<Record<string, string>> = {
  compute: '#3b82f6', ui: '#94a3b8', input: '#10b981', data: '#10b981', limit: '#ef4444',
  search: '#f59e0b', output: '#8b5cf6',
};
export const kindColor = (kind: string): string => KIND_COLORS[kind] ?? '#64748b';

export function formatTime(s: number | null | undefined): string {
  if (s == null || !Number.isFinite(s) || s <= 0) return '';
  if (s < 1) return `${Math.round(s * 1000)} ms`;
  if (s < 60) return `${s.toFixed(1)} s`;
  if (s < 3600) return `${Math.floor(s / 60)} min ${Math.round(s % 60)} s`;
  return `${(s / 3600).toFixed(1)} h`;
}

/** "<=400 it, tol 0.01 K" - what a loop badge prints; empty parts are left out. */
export function loopBadge(l: SgLoop): string {
  const parts: string[] = [];
  if (l.max_iterations != null) parts.push(`≤${l.max_iterations} it`);
  if (l.tolerance != null) parts.push(`tol ${l.tolerance}${l.tolerance_unit ? ' ' + l.tolerance_unit : ''}`);
  if (l.convergence?.iterations != null) parts.push(`last: ${l.convergence.iterations} it`);
  return parts.join(' · ');
}

export interface Box { x: number; y: number; w: number; h: number }

const subsetOf = (a: ReadonlySet<string>, b: ReadonlySet<string>): boolean => a.size < b.size && [...a].every((x) => b.has(x));

/** Loops that can be drawn as boxes: a laminar family (any two are disjoint or one contains the other), chosen
 *  greedily from the biggest.  A loop that would partly overlap an accepted one is `unboxed`: its members get a
 *  coloured ring instead (the modules view's two EM <-> thermal loops share the thermal solver). */
export function splitLoops(loops: readonly SgLoop[]): { boxed: SgLoop[]; unboxed: SgLoop[] } {
  const boxed: SgLoop[] = [];
  const unboxed: SgLoop[] = [];
  const set = (l: SgLoop) => new Set(l.nodes);
  for (const l of [...loops].sort((p, q) => q.nodes.length - p.nodes.length)) {
    const a = set(l);
    const clash = boxed.some((b) => {
      const c = set(b);
      const inter = [...a].filter((x) => c.has(x)).length;
      return inter > 0 && !subsetOf(a, c) && !subsetOf(c, a);
    });
    (clash ? unboxed : boxed).push(l);
  }
  return { boxed, unboxed };
}

/** Nesting of a laminar family: the parent (smallest strictly containing loop) of every loop. */
export function nesting(loops: readonly SgLoop[]): { parent: Record<string, string | null> } {
  const sets = loops.map((l) => ({ id: l.id, s: new Set(l.nodes) }));
  const parent: Record<string, string | null> = {};
  for (const x of sets) {
    const supers = sets.filter((y) => y.id !== x.id && subsetOf(x.s, y.s)).sort((p, q) => p.s.size - q.s.size);
    parent[x.id] = supers.length ? supers[0].id : null;
  }
  return { parent };
}
