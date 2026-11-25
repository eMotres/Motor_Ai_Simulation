/**
 * The run settings of the Sweep / Optimization / Descent panels (owner 2026-11-24, one place for the settings).
 *
 * The panels have no settings of their own: a run uses the values the Mesh and Simulation tabs hold at the moment of Run
 * (steps per period, gap rows, wire cell, mesh sizes, sectors, demag).  They are sent as `{preset: 'tab', overrides}`; the SERVER
 * (POST /api/optimization/run-settings/resolve, src/motor_ai_sim/run_settings.py) labels them by match against the Optimization and
 * Simulation presets (else `custom`), applies the bounds and answers with the digest and the resolution token; the panel shows that
 * answer in one read-only line and the run request carries the same values plus `expect_sig` / `expect_token`.  Pure functions
 * here, the hook is in useRunSettings.ts.
 */
export type RunMode = 'sweep' | 'descent' | 'auto';

/** What the request carries: the visible values, resolved by the server under the preset name 'tab'. */
export interface RunRequest {
  preset: 'tab'; overrides: Record<string, number | boolean>;
  /** The Simulation tab's magnet temperature when its field holds a number; absent = the card as written (the field is blank). */
  magnet_temp_c?: number;
}

/** The magnet temperature a run solves at, as the server resolves it. */
export interface MagnetResolved {
  requested: number | null; used_c: number | null; source: 'typed' | 'card' | 'unknown'; card: string | null; card_c: number | null;
}

export interface RunResolved {
  preset: string; label: string; custom: boolean; preset_label: string; matched?: string | null;
  differs: Record<string, { preset: unknown; current: unknown }>;
  settings: Record<string, any>; sources: Record<string, string>;
  clamps: Array<{ key: string; requested: unknown; used: unknown; reason: string }>;
  sig: number; sig_scope?: number; token?: string; version: number; mode: RunMode; overrides: Record<string, unknown>;
  geometry?: { num_slots?: number | null; num_poles?: number | null };
  /** The magnet temperature of the run (typed, or the assigned card's own); absent on an answer from before the field existed. */
  magnet?: MagnetResolved | null;
  /** Not part of the run's identity: what the finalists of this run are solved with (cogging quality), when it differs. */
  finalist_note?: { min_steps: number; run_steps: number; reason: string } | null;
}

/** The values of the tabs this module needs (by the request names; a missing one is absent). */
export interface TabState {
  steps?: number; gap_layers?: number; mesh_size_mm?: number; min_size_mm?: number; n_sectors?: number; demag?: boolean;
  rotor_eddy?: boolean;
  component_mesh?: Record<string, number>;
  /** The Simulation tab's magnet temperature field as a number; absent when blank or not a number. */
  magnet_temp_c?: number;
}

/** The wire cell of the Mesh tab: a missing key is the canonical 1h (the Mesh tab's own reading). */
export const wireCellOf = (m: Record<string, number> | undefined): number =>
  (m && typeof m.coil_rel === 'number' && m.coil_rel > 0 ? m.coil_rel : 1);

const NEEDED: Array<[keyof TabState, string]> = [
  ['steps', 'steps per period'], ['gap_layers', 'gap rows'], ['mesh_size_mm', 'mesh size'], ['min_size_mm', 'min mesh size'],
  ['n_sectors', 'sectors'],
];

/**
 * The request of one run from the tab values, or `null` with the names of the values the tabs do not hold (the line then reads
 * "unknown" and Run is disabled: nothing is filled in silently).  The demag switch of a Descent run is the machine's: not sent.
 */
export function tabRequest(cur: TabState, mode: RunMode): { request: RunRequest | null; missing: string[] } {
  const missing = NEEDED.filter(([k]) => typeof cur[k] !== 'number' || !Number.isFinite(cur[k] as number)).map(([, n]) => n);
  if (typeof cur.rotor_eddy !== 'boolean') missing.push('rotor eddy');
  if (mode !== 'descent' && typeof cur.demag !== 'boolean') missing.push('demag');   // a Descent run reads the machine's demag
  if (missing.length) return { request: null, missing };
  const o: Record<string, number | boolean> = {
    steps_per_period: cur.steps as number, gap_layers: cur.gap_layers as number, mesh_size_mm: cur.mesh_size_mm as number,
    min_size_mm: cur.min_size_mm as number, n_sectors: cur.n_sectors as number, coil_rel: wireCellOf(cur.component_mesh),
    rotor_eddy: cur.rotor_eddy as boolean,
  };
  if (mode !== 'descent') o.demag = cur.demag as boolean;
  const mt = cur.magnet_temp_c;
  return { request: { preset: 'tab', overrides: o, ...(typeof mt === 'number' && Number.isFinite(mt) ? { magnet_temp_c: mt } : {}) }, missing: [] };
}

/** Two requests are the same values (key order does not matter). */
export const sameRequest = (a: RunRequest | null, b: RunRequest | null): boolean => {
  if (!a || !b) return a === b;
  if ((a.magnet_temp_c ?? null) !== (b.magnet_temp_c ?? null)) return false;
  const ka = Object.keys(a.overrides).sort(), kb = Object.keys(b.overrides).sort();
  return ka.length === kb.length && ka.every((k, i) => k === kb[i] && a.overrides[k] === b.overrides[k]);
};

/** The request fragment of a run: the values and the digest / token the panel displayed. */
export const runSettingsBody = (r: RunRequest, shown: RunResolved) =>
  ({ preset: r.preset, overrides: r.overrides, expect_sig: shown.sig, ...(shown.token ? { expect_token: shown.token } : {}),
     ...(r.magnet_temp_c != null ? { magnet_temp_c: r.magnet_temp_c } : {}) });

/** The body of the Descent "Draw baseline" request: the held resolve answer's steps, gap rows, mesh, sectors, rotor eddy and
 *  element order under `run_settings` (the server resolves the same values). */
export const baselineBody = (
  base: { operating_point: unknown; current_bump_pct: number },
  r: RunRequest, shown: RunResolved,
) => ({
  ...base,
  steps_per_period: Number(shown.settings.steps_per_period),
  gap_layers: Number(shown.settings.gap_layers),
  n_sectors: Number(shown.settings.n_sectors),
  run_settings: runSettingsBody(r, shown),
});

/** Run is allowed only while the held answer belongs to the current machine, duty, mode and values. */
export const answerKey = (ctx: string, mode: RunMode, r: RunRequest | null): string =>
  `${ctx}|${mode}|${JSON.stringify(r ? r.overrides : null)}${r?.magnet_temp_c != null ? `|m${r.magnet_temp_c}` : ''}`;

/** The words of the line, translated by the caller. */
export interface LineWords {
  preset: (label: string, version: number) => string;
  steps: (n: number) => string; gap: (n: number) => string; wire: (n: number) => string;
  /** where a value is set: the Simulation tab (steps), the Mesh tab (gap rows, wire cell) */
  fromSim: string; fromMesh: string;
  /** "not the Optimization preset" */
  notOptimization: string;
  /** one differing value: its name, its value, the preset's value */
  diff: (key: string, current: unknown, preset: unknown) => string;
  /** the magnet temperature with its source named once: "magnets 120 C (card F52SH_120C)" / "magnets 60 C (typed)" */
  magnet?: (tempC: number | null, source: 'typed' | 'card', card: string | null) => string;
}

/** The magnet temperature a STORED run (a sweep's scan params, a Descent / Auto record, a point) states.  A record from before the
 *  field existed has no such key: it is read as the card as written, labelled "card" (the same temperature those runs solved at).
 *  `scan_params.magnet` / `record.magnet` is the block; a point carries `magnet_temp_used_c`. */
export function magnetOfRecord(rec: unknown): MagnetResolved {
  const r = (rec && typeof rec === 'object' ? rec : {}) as Record<string, any>;
  const m = (r.scan_params?.magnet ?? r.magnet ?? r.eval_params?.magnet) as Partial<MagnetResolved> | undefined;
  if (m && typeof m === 'object') {
    const req = typeof m.requested === 'number' ? m.requested : null;
    const used = typeof m.used_c === 'number' ? m.used_c : req;
    return { requested: req, used_c: used ?? null, source: req != null ? 'typed' : 'card', card: m.card ?? null, card_c: m.card_c ?? null };
  }
  // a point carries only the temperature it was solved at: whether that was typed or the card's is unknown without the run's block (a
  // point of a typed sweep must never read "card"); a record with no magnet information at all is from before the field: the card
  const pt = typeof r.magnet_temp_used_c === 'number' ? r.magnet_temp_used_c : null;
  return { requested: null, used_c: pt, source: pt != null ? 'unknown' : 'card', card: null, card_c: null };
}

/** The one line, from the SERVER answer.  Each value names its own source once; a run that is not the Optimization preset says so
 *  and names every value that breaks it:
 *  "Optimization preset v2 \u00b7 48 steps (Simulation tab), 1 gap row (Mesh), wire cell 2h (Mesh)" or
 *  "108 steps (Simulation tab), 1 gap row (Mesh), wire cell 2h (Mesh) \u00b7 not the Optimization preset: steps 108 (preset 48)".
 *  (The server lists the differences from the Optimization preset; a run that equals the Simulation preset has none, and only says
 *  that it is not the Optimization preset.) */
export function lineText(r: RunResolved, w: LineWords): string {
  const st = r.settings;
  const values = [`${w.steps(Number(st.steps_per_period))} (${w.fromSim})`, `${w.gap(Number(st.gap_layers))} (${w.fromMesh})`,
                  `${w.wire(Number(st.coil_rel ?? 1))} (${w.fromMesh})`].join(', ');
  // the number shown is the server's used value (the typed one when it was typed); a typed answer without any number says nothing rather
  // than a wrong "0 C"
  const mgT = r.magnet ? (r.magnet.used_c ?? r.magnet.requested) : null;
  const mg = r.magnet && w.magnet && r.magnet.source !== 'unknown' && !(r.magnet.source === 'typed' && mgT == null)
    ? `, ${w.magnet(mgT, r.magnet.source, r.magnet.card)}` : '';
  const head = r.custom ? `${values}${mg}` : `${w.preset(r.preset_label, r.version)} \u00b7 ${values}${mg}`;
  if (!notOptimization(r)) return head;
  const names = Object.entries(r.differs).map(([k, d]) => w.diff(k, d.current, d.preset));
  return `${head} \u00b7 ${w.notOptimization}${names.length ? `: ${names.join(', ')}` : ''}`;
}

/** The line is a warning unless the run is the Optimization preset (colour AND the word). */
export const notOptimization = (r: RunResolved): boolean => r.label !== 'optimization';
