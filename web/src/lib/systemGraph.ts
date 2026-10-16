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
  /** a limit check's condition: i18n key + numbers read from the code (or from the selected motor's model) */
  condition?: { key: string; params: Record<string, string | number> } | null;
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
  /** what the loop does, read from the code (see SgRule); null for a loop the code does not describe */
  rule?: SgRule | null;
  /** iteration count and final residual of the newest stored run of the selected motor, when there is one */
  convergence?: { iterations?: number; max_iterations?: number | null; converged?: boolean; residual?: number | null; unit?: string } | null;
}
export interface SgExit { id: string; kind: 'continue' | 'warn' | 'fail'; param?: Record<string, number> }
export interface SgRule {
  iterates: string;
  stop?: { kind: 'max_delta' | 'all_pass' | 'budget'; quantity?: string; value?: number; unit?: string; bearing_K?: number; duty_ratio_pct?: number } | null;
  limit?: { max: number } | null;
  damping?: { start: number; factor?: number; min?: number } | null;
  fallback?: { damping: number; max: number } | null;
  steps?: { scan_points: number; refinement_passes: number; tolerance: number; unit: string } | null;
  exits: SgExit[];
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
  done: '#15803d', running: '#2563eb', error: '#dc2626', cached: '#7c3aed', unknown: '#6b7280',
};

/** Wire colours by contract / data type; any other type gets a stable hue from its name. */
const TYPE_COLORS: Readonly<Record<string, string>> = {
  GeometryIR: '#0d9edf', MeshIR: '#12a595', ResultIR: '#cd8409', MachineState: '#ec4899',
  ControlSignal: '#f43f5e', ParameterSet: '#8b5cf6', Excitation: '#69a311', CostIR: '#b68b06',
  MaterialIR: '#a16207', TransmissionIR: '#64748b', dependency: '#8895a9',
  rpm: '#6366f1', torque: '#ef6e15', current: '#ef4444', temperature: '#f43f5e', loss_W: '#d946ef',
  power: '#b68b06', air_speed: '#05a3be', heat_transfer: '#19a0b4', air_density: '#2f9ed0',
  slipstream_factor: '#0891b2', passport_grid: '#0ea674', thermal_geometry: '#65a30d',
  thermal_calibration: '#16a34a', limits: '#dc2626', limit: '#dc2626', drive: '#7c3aed', pack: '#a855f7',
  verdict: '#475569', s1_rating: '#2563eb', mixed: '#8895a9',
};

export function typeColor(type: string): string {
  const c = TYPE_COLORS[type];
  if (c) return c;
  let h = 0;
  for (let i = 0; i < type.length; i++) h = (h * 31 + type.charCodeAt(i)) % 360;
  return `hsl(${h} 65% 38%)`;
}

export const KIND_COLORS: Readonly<Record<string, string>> = {
  compute: '#3b82f6', ui: '#8895a9', input: '#0ea674', data: '#0ea674', limit: '#ef4444',
  search: '#cd8409', output: '#8b5cf6',
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
