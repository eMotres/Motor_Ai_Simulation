// The MACHINE's bearings — identity, local memory, and the loss fetch.
//
// A bearing belongs to a motor, not to a panel.  This file is `machineBattery.ts`
// for the mechanical side, deliberately and line for line: on the vendor's own
// machines the pair lives in the family configuration yaml
// (config/dies/<die>/<cfg>.yaml, written by
// PATCH /api/family/config/{die}/{cfg}/bearings) and that file stays the single
// source of truth — the Mechanical tab's shaft-line defaults, the Electromagnetic
// summary's loss cells and the datasheet's mechanical rows all read it from there.
//
// Machines OUTSIDE that catalog (a client's ▶-copy, a legacy preset) have no yaml
// to write, so their bearings are remembered client-side — but keyed by the
// MACHINE, never in the global `mech.*` block.  That block travels with the panel:
// a bearing pair doing the same would silently put a 55 mm spindle bearing on the
// 40 mm motor and quietly triple its no-load loss.
//
// WHY THIS EXISTS AT ALL.  Until 2026-09-08 the app computed copper, iron, magnet,
// shaft and sleeve loss and the datasheet said outright that "bearing and windage
// losses are NOT included".  On the measured 150 mm free run those two were
// 43–170 W out of 83–319 W — the largest single term below 3000 rpm
// (docs/measurements/2026-08-04_noload_decomposition_150mm.md).  The bearings are
// not an accessory; they are half the loss picture.

import { readLocalContext } from './machineBattery';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;
const KEY = 'bearings.local';
const MAX_MACHINES = 24;

export type Lubrication = 'grease' | 'oil_air';
export type TempSource = 'manual' | 'thermal';

/** One end of the shaft line. `card` is a name in config/bearings_library.yaml. */
export interface BearingEnd {
  card: string;
  /** Override the card's seal type when THIS build differs (a 2Z variant of a
   *  card catalogued with seals). Absent = the card's own. */
  seals?: string | null;
  /** Override the card's default grease/oil. Absent = the card's own. */
  grease?: string | null;
}

export interface BearingsBlock {
  A?: BearingEnd | null;
  B?: BearingEnd | null;
  lubrication: Lubrication;
  /** Axial preload per bearing [N]. 0 on a plain deep-groove pair. */
  preload_n: number;
  temp_source: TempSource;
  /** Bearing temperature [°C] when `temp_source` is 'manual'. */
  temp_c?: number | null;
}

/** What GET /api/bearings/library serves for one card. */
export interface BearingCard {
  name: string;
  description?: string;
  type: string;
  d: number; D: number; B: number; d_m: number;
  contact_angle_deg?: number | null;
  balls?: string;
  seals?: string;
  default_lubricant?: string | null;
  n_limit_grease_rpm?: number | null;
  n_limit_oil_air_rpm?: number | null;
  C_kn?: number | null;
  C0_kn?: number | null;
  stiffness_n_per_m?: number | null;
  note?: string;
}

export interface BearingLibrary {
  bearings: Record<string, BearingCard>;
  lubricants: Record<string, { name: string; description?: string; kind?: string;
                               nu40?: number; nu100?: number; note?: string }>;
}

/** One bearing's answer inside GET /api/bearings/losses. */
export interface BearingResult {
  end: string;
  bearing: string;
  M_rr_Nm: number; M_sl_Nm: number; M_seal_Nm: number;
  M_total_Nm: number; P_W: number;
  F_r_N: number; F_a_N: number; F_g_N: number;
  nu_mm2_s: number;
  stiffness_n_per_m?: number | null;
  seal_estimate?: boolean;
  notes?: string[];
  speed: {
    n_dm: number; n_dm_limit: number | null; limit_rpm: number | null;
    ratio: number | null; ok: boolean | null; verdict: string;
    lubrication: string;
  };
}

export interface BearingLosses {
  has_bearings: boolean;
  rpm: number;
  /** The bearing temperature the grease viscosity was interpolated at [°C]. */
  temp_c?: number;
  lubrication?: Lubrication;
  preload_n?: number;
  rotor_mass_kg?: number;
  rotor_mass_source?: string;
  note?: string;
  bearings?: BearingResult[];
  windage?: { P_W: number; P_gap_W: number; P_faces_W: number;
              gap_regime: string; face_regime: string; delta_mm: number } | null;
  P_bearings_W?: number;
  P_windage_W?: number | null;
  P_mech_extra_W?: number;
  M_bearings_Nm?: number;
  notes?: string[];
  model?: string;
  die?: string | null;
  config?: string | null;
  /** True when the answer was computed for cards named in the REQUEST rather
   *  than for the pair saved on the machine. */
  preview?: boolean;
}

const DEFAULTS: BearingsBlock = {
  A: null, B: null, lubrication: 'grease', preload_n: 0,
  temp_source: 'manual', temp_c: 70,
};

const num = (v: unknown, fallback: number): number =>
  (v != null && Number.isFinite(Number(v))) ? Number(v) : fallback;

/** An END as the API takes it, or null when no card is named.
 *  A blank card is NOT a bearing: an empty string would be saved as a machine
 *  whose losses can never be computed, and the 422 would arrive weeks later. */
export function normalizeEnd(e: unknown): BearingEnd | null {
  if (!e || typeof e !== 'object') return null;
  const o = e as Record<string, unknown>;
  const card = String(o.card ?? '').trim();
  if (!card) return null;
  const out: BearingEnd = { card };
  const seals = String(o.seals ?? '').trim();
  const grease = String(o.grease ?? '').trim();
  if (seals) out.seals = seals;
  if (grease) out.grease = grease;
  return out;
}

/** A stored (or typed) block in the ONE shape the rest of the app reads.
 *  `null` when it names no bearing at all — "not decided yet", which must never
 *  be confused with "assigned, zero loss". */
export function normalizeBearings(b: unknown): BearingsBlock | null {
  if (!b || typeof b !== 'object') return null;
  const o = b as Record<string, unknown>;
  let A = normalizeEnd(o.A);
  let B = normalizeEnd(o.B);
  if (!A && !B) return null;
  // One card named means two of it — that is what a symmetric shaft line is,
  // and it is the same fallback the backend applies.
  if (!A) A = B ? { ...B } : null;
  if (!B) B = A ? { ...A } : null;
  const lub: Lubrication = o.lubrication === 'oil_air' ? 'oil_air' : 'grease';
  const ts: TempSource = o.temp_source === 'thermal' ? 'thermal' : 'manual';
  return {
    A, B,
    lubrication: lub,
    preload_n: Math.max(0, num(o.preload_n, 0)),
    temp_source: ts,
    temp_c: o.temp_c == null ? null : num(o.temp_c, DEFAULTS.temp_c as number),
  };
}

/** The PATCH body. Returns null when there is nothing to save — the caller then
 *  sends the CLEAR (an all-null body), which is a different intention. */
export function bearingsPayload(b: BearingsBlock | null | undefined) {
  const n = normalizeBearings(b);
  if (!n) return null;
  return {
    A: n.A, B: n.B,
    lubrication: n.lubrication,
    preload_n: n.preload_n,
    temp_source: n.temp_source,
    temp_c: n.temp_c ?? null,
  };
}

/** The client-side machine identity a bearing pair is filed under — the SAME
 *  rule the battery uses, so a machine has one identity, not two. */
export function machineKey(
  loc: { die?: string | null; config?: string | null } | null | undefined,
  presetId?: string | null,
): string | null {
  if (loc?.die && loc?.config) return `fam:${loc.die}/${loc.config}`;
  if (presetId) return `preset:${presetId}`;
  return null;
}

type Store = Record<string, { at: number; bearings: BearingsBlock }>;

function readStore(): Store {
  try { return JSON.parse(localStorage.getItem(KEY) || '{}') as Store; }
  catch { return {}; }
}

export function readLocalBearings(key: string | null): BearingsBlock | null {
  if (!key) return null;
  return normalizeBearings(readStore()[key]?.bearings);
}

export function writeLocalBearings(key: string | null, b: BearingsBlock): void {
  if (!key) return;
  const s = readStore();
  s[key] = { at: Date.now(), bearings: b };
  const names = Object.keys(s);
  if (names.length > MAX_MACHINES) {
    names.sort((a, c) => (s[a].at || 0) - (s[c].at || 0));
    for (const n of names.slice(0, names.length - MAX_MACHINES)) delete s[n];
  }
  try { localStorage.setItem(KEY, JSON.stringify(s)); } catch { /* quota */ }
}

/** "2 × 71910 CE/HCP4A · oil-air · 200 N" — what the machine is built with, in
 *  one line.  "no bearings" when none, because that is a real answer. */
export function bearingsChipLabel(b: BearingsBlock | null | undefined): string {
  const n = normalizeBearings(b);
  if (!n) return 'no bearings';
  const a = n.A?.card ?? '';
  const c = n.B?.card ?? '';
  const parts: string[] = [];
  parts.push(a && c && a === c ? `2 × ${a}` : [a, c].filter(Boolean).join(' / '));
  parts.push(n.lubrication === 'oil_air' ? 'oil-air' : 'grease');
  if (n.preload_n > 0) parts.push(`${Math.round(n.preload_n)} N preload`);
  return parts.filter(Boolean).join(' · ');
}

/** WHICH block this client should show, given the three places one can live.
 *
 *  Same precedence as the battery, and for the same reason: the server context
 *  belongs to the OWNER, so it only counts when it names the machine this client
 *  actually has open.  Pure, so it is testable without a browser.
 */
export function resolveBearings(args: {
  ctx?: { active?: boolean; can_write?: boolean; die?: string; config?: string;
          bearings?: unknown } | null;
  loc?: { die?: string | null; config?: string | null } | null;
  local?: unknown;
  treeEntry?: { bearings?: unknown } | null;
}): { bearings: BearingsBlock | null; die?: string | null;
      config?: string | null; canWrite: boolean } {
  const { ctx, loc, local, treeEntry } = args;
  // Owner / admin with a catalog machine loaded — the yaml IS the answer.
  if (ctx?.active && ctx?.can_write === true) {
    return { bearings: normalizeBearings(ctx.bearings), die: ctx.die,
             config: ctx.config, canWrite: true };
  }
  const base = { die: loc?.die ?? null, config: loc?.config ?? null,
                 canWrite: false };
  const mine = normalizeBearings(local);
  if (mine) return { bearings: mine, ...base };
  if (loc?.die && loc?.config) {
    if (ctx?.active && ctx.die === loc.die && ctx.config === loc.config) {
      return { bearings: normalizeBearings(ctx.bearings), ...base };
    }
    return { bearings: normalizeBearings(treeEntry?.bearings), ...base };
  }
  return { bearings: normalizeBearings(ctx?.bearings), ...base };
}

/* ── fetches ──────────────────────────────────────────────────────────────── */

export async function fetchBearingLibrary(): Promise<BearingLibrary | null> {
  try {
    const r = await fetch(`${API}/api/bearings/library`, { cache: 'no-store' });
    if (!r.ok) return null;
    return await r.json() as BearingLibrary;
  } catch { return null; }
}

/** The analytic loss at one operating point.  `null` when the backend cannot
 *  answer — the caller shows "—", never a zero. */
export async function fetchBearingLosses(p: {
  rpm: number; tempC?: number; rotorMassKg?: number | null;
  preloadN?: number | null; lubrication?: Lubrication | null;
  die?: string | null; cfg?: string | null;
  /** PREVIEW a pair the user has picked but not saved onto the machine yet.
   *  The answer comes back flagged `preview: true`. */
  cardA?: string | null; cardB?: string | null;
  signal?: AbortSignal;
}): Promise<BearingLosses | null> {
  if (!(p.rpm > 0)) return null;
  const q = new URLSearchParams({ rpm: String(p.rpm) });
  if (p.cardA) q.set('card_a', p.cardA);
  if (p.cardB) q.set('card_b', p.cardB);
  if (p.tempC != null && Number.isFinite(p.tempC)) q.set('temp_c', String(p.tempC));
  if (p.rotorMassKg != null && Number.isFinite(p.rotorMassKg)) {
    q.set('rotor_mass_kg', String(p.rotorMassKg));
  }
  if (p.preloadN != null && Number.isFinite(p.preloadN)) {
    q.set('preload_n', String(p.preloadN));
  }
  if (p.lubrication) q.set('lubrication', p.lubrication);
  if (p.die && p.cfg) { q.set('die', p.die); q.set('cfg', p.cfg); }
  try {
    const r = await fetch(`${API}/api/bearings/losses?${q}`,
                          { cache: 'no-store', signal: p.signal });
    if (!r.ok) return null;
    return await r.json() as BearingLosses;
  } catch { return null; }
}

/** Read this client's machine and its bearings, through the same three places
 *  the battery uses.  Never throws; `{bearings: null}` on a dead backend. */
export async function loadMachineBearings(presetId?: string | null): Promise<{
  bearings: BearingsBlock | null; die?: string | null; config?: string | null;
  canWrite: boolean;
}> {
  let ctx: Record<string, unknown> | null = null;
  try { ctx = await fetch(`${API}/api/family/context`).then(r => r.json()); }
  catch { ctx = null; }
  const loc = readLocalContext();
  const local = readLocalBearings(machineKey(loc, presetId ?? null));
  let treeEntry: { bearings?: unknown } | null = null;
  const c = ctx as { active?: boolean; can_write?: boolean; die?: string;
                     config?: string; bearings?: unknown } | null;
  const needTree = !(c?.active && c?.can_write === true) && !local
    && !!loc?.die && !(c?.active && c.die === loc.die && c.config === loc.config);
  if (needTree) {
    try {
      const t = await fetch(`${API}/api/family/tree`).then(r => r.json());
      const d = (t?.dies || []).find((x: { name?: string }) => x?.name === loc!.die);
      treeEntry = (d?.configs || []).find(
        (x: { name?: string }) => x?.name === loc!.config) ?? null;
    } catch { treeEntry = null; }
  }
  return resolveBearings({ ctx: c, loc, local, treeEntry });
}

/** Save to the MACHINE.  Writes the yaml when this client owns it, and this
 *  browser's memory when it does not — never a PATCH that will be refused. */
export async function saveMachineBearings(
  die: string | null | undefined, cfg: string | null | undefined,
  canWrite: boolean, b: BearingsBlock | null, presetId?: string | null,
): Promise<{ ok: boolean; message: string; bearings: BearingsBlock | null }> {
  const body = bearingsPayload(b) ?? {
    A: null, B: null, lubrication: 'grease', preload_n: 0,
    temp_source: 'manual', temp_c: null,
  };
  if (canWrite && die && cfg) {
    try {
      const r = await fetch(
        `${API}/api/family/config/${encodeURIComponent(die)}/${encodeURIComponent(cfg)}/bearings`,
        { method: 'PATCH', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body) });
      if (!r.ok) {
        let detail = `HTTP ${r.status}`;
        try { detail = JSON.stringify((await r.json()).detail ?? detail); }
        catch { /* keep */ }
        return { ok: false, message: `✗ ${detail}`, bearings: b };
      }
      const saved = normalizeBearings((await r.json())?.bearings);
      try { window.dispatchEvent(new CustomEvent('family-changed')); }
      catch { /* SSR */ }
      return { ok: true, message: `✓ bearings saved on ${die}/${cfg}`,
               bearings: saved };
    } catch (e) { return { ok: false, message: `✗ ${e}`, bearings: b }; }
  }
  const key = machineKey(readLocalContext(), presetId ?? null);
  if (!key) {
    return { ok: false, bearings: b,
             message: '✗ no motor is loaded — open one first, then set its bearings' };
  }
  const n = normalizeBearings(b);
  if (n) writeLocalBearings(key, n);
  return { ok: true, bearings: n,
           message: '✓ bearings saved for this motor (this browser)' };
}
