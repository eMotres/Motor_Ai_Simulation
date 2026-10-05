// What the help assistant is told about the screen (owner 2026-10-05): a small
// snapshot that rides with every chat message and every ticket, so an answer can
// name the user's real numbers and a ticket arrives with the state it happened in.
//
//   * the recorder keeps the last ~10 FAILED calls to our own API (method, path
//     without query, status, a clipped message) - never a header, a token, a
//     cookie or a request body;
//   * Configure publishes a snapshot of its knobs and result tiles here while it
//     is open;
//   * `buildSupportContext` puts those and the app/browser facts together.
//
// The server sanitises all of it again (support_context.py) - this side keeps
// the snapshot compact and never reads anything secret in the first place.
//
// No imports (a node test loads this file itself): the recorder takes the API
// base, the build and the tab names as arguments.

export interface FailedCall { method: string; path: string; status: number; message: string; at: number; }

const MAX_CALLS = 10;
const calls: FailedCall[] = [];

/** Keep the path only: the query can carry the geometry JSON or a token. */
export function apiPath(url: string, apiBase: string): string | null {
  let rest = String(url ?? '');
  const base = String(apiBase ?? '').replace(/\/$/, '');
  if (base && rest.startsWith(base)) rest = rest.slice(base.length);
  else if (/^[a-z]+:\/\//i.test(rest)) return null;              // somebody else's server
  rest = rest.split('?')[0].split('#')[0];
  return rest.startsWith('/api/') ? rest : null;
}

/** Record one failed call (a status >= 400, or a network error with status 0).
 *  Calls of the support surface itself are not recorded. */
export function recordFailedCall(c: { method?: string; url: string; status: number; message?: string }, apiBase: string,
  now: number = Date.now()): void {
  const path = apiPath(c.url, apiBase);
  if (!path || path.startsWith('/api/support/')) return;
  calls.push({
    method: String(c.method || 'GET').toUpperCase().slice(0, 8), path, status: Number(c.status) || 0,
    message: String(c.message ?? '').replace(/\s+/g, ' ').trim().slice(0, 160), at: now,
  });
  while (calls.length > MAX_CALLS) calls.shift();
}

export function failedCalls(now: number = Date.now()): Array<Omit<FailedCall, 'at'> & { agoS: number }> {
  return calls.map((c) => ({ method: c.method, path: c.path, status: c.status, message: c.message,
    agoS: Math.max(0, Math.round((now - c.at) / 1000)) }));
}
export const clearFailedCalls = (): void => { calls.length = 0; };

/** The error text of a failed response: FastAPI's `detail` (a string, or a list of
 *  validation errors), else `error`, else the status text. Never the whole body. */
export function errorMessageOf(body: unknown, fallback: string): string {
  const b = body as { detail?: unknown; error?: unknown; message?: unknown } | null;
  const d = b?.detail;
  if (typeof d === 'string') return d;
  if (Array.isArray(d)) {
    const first = d[0] as { msg?: unknown } | undefined;
    if (first && typeof first.msg === 'string') return first.msg;
  }
  if (typeof b?.error === 'string') return b.error;
  if (typeof b?.message === 'string') return b.message;
  return fallback;
}

/** Wrap `fetch` once so failed calls to our API are remembered.  Reads only the
 *  status and the error text of a FAILED response (from a clone, so the caller's
 *  body is untouched); a successful call costs nothing. */
export function installFailedCallRecorder(apiBase: string): void {
  const w = globalThis as unknown as {
    fetch?: typeof fetch; __supportRecorderInstalled?: boolean;
  };
  if (!w.fetch || w.__supportRecorderInstalled) return;
  w.__supportRecorderInstalled = true;
  const orig = w.fetch.bind(globalThis);
  w.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.href
      : (input as Request).url ?? String(input);
    const method = init?.method ?? (typeof input === 'object' && 'method' in (input as Request) ? (input as Request).method : 'GET');
    try {
      const r = await orig(input as RequestInfo | URL, init);
      if (!r.ok) {
        let msg = r.statusText || `HTTP ${r.status}`;
        try { msg = errorMessageOf(await r.clone().json(), msg); } catch { /* not JSON: keep the status text */ }
        recordFailedCall({ method, url, status: r.status, message: msg }, apiBase);
      }
      return r;
    } catch (e) {
      recordFailedCall({ method, url, status: 0, message: e instanceof Error ? e.message : 'network error' }, apiBase);
      throw e;
    }
  };
}

// ── Configure's snapshot ─────────────────────────────────────────────────────
type Snap = Record<string, unknown>;
let configureSnap: Snap | null = null;

export const setConfigureSnapshot = (s: Snap | null): void => { configureSnap = s; };
export const getConfigureSnapshot = (): Snap | null => configureSnap;

/** Round for the model: three significant digits, finite numbers only. */
export function round3(v: unknown): number | null {
  const n = typeof v === 'number' ? v : NaN;
  if (!Number.isFinite(n)) return null;
  return n === 0 ? 0 : Number(n.toPrecision(3));
}

const compact = (o: Record<string, unknown>): Record<string, unknown> => {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(o)) {
    if (v === null || v === undefined || v === '' || (typeof v === 'number' && !Number.isFinite(v))) continue;
    out[k] = v;
  }
  return out;
};

export interface ConfigureSnapshotInput {
  machine: { name: string; die?: string | null; configuration?: string | null; fullCard?: boolean | null };
  preset?: string | null; presetModified?: boolean;
  knobs: { stackLength_mm: number; turnsPerSlot: number; wire_mm: number; connection: string; current_A_rms: number; speed_rpm: number };
  drive: { mode: 'sine' | 'pwm'; transistor?: string | null; pwm_kHz?: number | null };
  propeller?: { name: string; ambient_C: number; load: 'propeller' | 'manual' } | null;
  battery: { cells: number; cellV: [number, number, number]; pack_V: [number, number]; edited: boolean } | null;
  tiles: Record<string, number | null | undefined>;
  warnings: string[];
}

/** The Configure part of the context: knob values and the key result tiles, with
 *  refusals and red lines as plain sentences in `warnings`. */
export function buildConfigureSnapshot(i: ConfigureSnapshotInput): { machine: Snap; configure: Snap } {
  const tiles: Record<string, number> = {};
  for (const [k, v] of Object.entries(i.tiles)) { const r = round3(v); if (r !== null) tiles[k] = r; }
  return {
    machine: compact({
      name: i.machine.name, die: i.machine.die ?? null, configuration: i.machine.configuration ?? null,
      fullCard: i.machine.fullCard ?? null,
    }),
    configure: compact({
      preset: i.preset ?? null, presetModified: i.presetModified ? true : null,
      knobs: compact({
        stackLength_mm: round3(i.knobs.stackLength_mm), turnsPerSlot: round3(i.knobs.turnsPerSlot),
        wire_mm: round3(i.knobs.wire_mm), connection: i.knobs.connection,
        current_A_rms: round3(i.knobs.current_A_rms), speed_rpm: round3(i.knobs.speed_rpm),
      }),
      drive: compact({ mode: i.drive.mode, transistor: i.drive.transistor ?? null, pwm_kHz: round3(i.drive.pwm_kHz ?? NaN) }),
      propeller: i.propeller ? compact({ name: i.propeller.name, ambient_C: round3(i.propeller.ambient_C), load: i.propeller.load }) : null,
      battery: i.battery ? compact({
        cells: i.battery.cells, cellV_min_nom_max: i.battery.cellV.map(round3),
        pack_V_min_max: i.battery.pack_V.map(round3), editedByUser: i.battery.edited ? true : null,
      }) : null,
      tiles,
      warnings: i.warnings.map((w) => String(w).slice(0, 200)).slice(0, 8),
    }),
  };
}

// ── the whole context ────────────────────────────────────────────────────────
/** English names of the tabs (the model is told them in English). */
export const TAB_LABELS: Record<string, string> = {
  motors: 'Motors', geometry: 'Geometry', materials: 'Materials', mesh: 'Mesh',
  simulation: 'Electromagnetic', static3d: '3D', mechanical: 'Mechanical', thermal: 'Thermal',
  controller: 'Controller', sweep: 'Optimization', comparePoints: 'Compare', cost: 'Cost',
  compare: 'Configure', admin: 'Admin',
};

export interface ContextInputs {
  tab: string; lang: string; appVersion: string; appGitSha: string; userAgent: string;
  now?: number;
}

/** The snapshot sent with a chat message or a ticket. */
export function buildSupportContext(i: ContextInputs): Record<string, unknown> {
  const snap = getConfigureSnapshot();
  const onConfigure = i.tab === 'compare';
  const out: Record<string, unknown> = {
    tab: i.tab, tabLabel: TAB_LABELS[i.tab] ?? i.tab, lang: i.lang,
    app: { version: i.appVersion, gitSha: i.appGitSha },
    browser: String(i.userAgent ?? '').slice(0, 200),
  };
  if (snap && onConfigure) {
    if (snap.machine) out.machine = snap.machine;
    if (snap.configure) out.configure = snap.configure;
  }
  const fc = failedCalls(i.now);
  if (fc.length) out.failedCalls = fc;
  return out;
}
