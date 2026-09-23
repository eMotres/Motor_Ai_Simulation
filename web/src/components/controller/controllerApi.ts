/**
 * Controller tab — the API surface and the shapes it returns.
 *
 * One module so the panel, the catalogue and the tests agree on the names the
 * backend actually sends (``src/motor_ai_sim/routes/controller.py``).
 */
const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;

export interface DeviceRow {
  part: string;
  manufacturer?: string | null;
  family?: string | null;
  technology?: string | null;
  package?: string | null;
  package_common_name?: string | null;
  cooling?: string | null;
  v_dss_V?: number | null;
  i_d_25c_A?: number | null;
  i_d_100c_A?: number | null;
  t_j_max_c?: number | null;
  r_ds_on_25c_mohm?: number | null;
  r_ds_on_175c_mohm?: number | null;
  r_th_jc_k_w?: number | null;
  r_th_jc_max_k_w?: number | null;
  package_size_mm?: { length_mm: number | null; width_mm: number | null; height_mm: number | null };
  weight_g?: number | null;
  image?: string | null;
  /** A GENERATED outline of the package — never vendor artwork. */
  package_svg?: string;
  /** ceil(I_switch_rms / I_DDC@100 °C) for the duty the catalogue was asked for. */
  suggested_parallel?: number | null;
  /** A quotation somebody typed on the card, never a datasheet value. */
  price?: { amount: number | null; currency: string; quantity: number | null;
            source: string | null; dated: string | null };
  datasheet_url?: string | null;
  datasheet_revision?: string | null;
  error?: string;
}

export interface CoilRow {
  index: number; phase: string; polarity: number;
  slot_go: number; slot_return: number; tooth: number | null;
  angle_elec_deg: number; label: string;
}

export interface MappingRow { coil: number; bridge: string; leg: string; polarity?: number; }

export interface LegResult {
  leg: string; coils: number[];
  i_leg_rms_A: number; i_switch_rms_A: number;
  i_device_rms_A: number; i_device_peak_A: number;
  p_conduction_W: number; p_third_quadrant_W: number; p_switching_W: number;
  p_e_oss_W: number; p_leg_W: number; p_switch_W: number; p_device_W: number;
  t_j_c: number; r_ds_on_mohm: number;
}

export interface BridgeResult {
  id: string; kind: string; label: string; connection: string;
  coils: number[]; devices_parallel: number; n_switches: number;
  modulation: string; modulation_index: number; p_loss_W: number;
  legs: LegResult[];
}

export interface LimitRow {
  name: string;
  value: number | null;
  limit: number | null;
  unit: string;
  margin: number | null;
  utilisation_pct: number | null;
  verdict: 'pass' | 'fail' | 'warn' | 'not_judged';
  source: string;
  note: string;
}

export interface ControllerResult {
  ok: boolean;
  /** every published limit of the chosen part is inside its number */
  feasible?: boolean;
  limits?: LimitRow[];
  limits_verdict?: 'pass' | 'fail' | 'warn';
  device: string;
  device_row: DeviceRow;
  topology: { preset: string; preset_label: string; star_delta: string;
              n_bridges: number; n_switches: number; n_devices: number;
              coils: CoilRow[]; mapping: MappingRow[]; notes: string[] };
  bridges: BridgeResult[];
  losses: Record<string, number | string>;
  thermal: Record<string, any>;
  dc_link: Record<string, number>;
  efficiency: { inverter: number | null; shaft: number | null;
                wall_to_shaft: number | null; note: string };
  point: Record<string, any>;
  settings: Record<string, any>;
  waveforms: { f_elec_hz: number; t_s: number[];
               coils: Record<string, { bridge: string; v_V: number[]; i_A: number[];
                                       v_mean_V: number; v_rms_V: number; i_rms_A: number }>;
               dead_time_us: number; dead_time_error_V?: number };
  warnings: string[];
  violations: string[];
  model_notes: string[];
  sources?: Record<string, string>;
  provenance?: Record<string, string>;
  schematic_svg?: string | null;
  context?: { die: string; config: string; duty: string } | null;
  cached?: boolean;
  served_from_history?: boolean;
  computed_at?: string;
  elapsed_s?: number;
  /** Which point of the duty this answer is for — e.g. "solved for the S1
   * point: 48.6 A rms · 1.99 kW in".  The backend resolves ``p_ac_W`` (never
   * typed by anyone) from the duty's own electromagnetic record, and a
   * continuous (S1) rating REPLACES that record's numbers with the verified
   * S1 machine — this line is what tells the tab (and the owner) which
   * machine the tiles below actually describe. ``null`` when the duty has no
   * electromagnetic answer at all (the solve was refused instead). */
  solved_for?: string | null;
  /** ``"coupled" | "pwm" | "standalone" | "none"`` — where the point above
   * was read from (:func:`report.duty_em_source`'s own vocabulary). */
  em_source?: string | null;
}

async function j<T>(r: Response): Promise<T> {
  const body = await r.json().catch(() => ({}));
  if (!r.ok) {
    const d = (body as any)?.detail;
    throw new Error(typeof d === 'string' ? d : (d?.message || `HTTP ${r.status}`));
  }
  return body as T;
}

/** `topology` sizes the "suggested parallel" column for the loaded duty. */
export const listDevices = (topology?: string) => {
  const p = new URLSearchParams();
  if (topology) p.set('topology', topology);
  return fetch(`${API}/api/controller/devices?${p}`).then(j<{
    dir: string; devices: DeviceRow[];
    i_switch_rms_A: number | null; i_switch_rms_basis: string | null;
    suggestion_note: string;
  }>);
};

export const getDevice = (part: string) =>
  fetch(`${API}/api/controller/devices/${encodeURIComponent(part)}`)
    .then(j<{ part: string; card: any; row: DeviceRow; provenance: Record<string, string>; file: string }>);

/** `card` may be the object itself or the YAML text of it (the route parses). */
export const addDevice = (card: any, overwrite = false) =>
  fetch(`${API}/api/controller/devices`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(typeof card === 'string'
      ? { card_yaml: card, overwrite } : { card, overwrite }),
  }).then(j<{ ok: boolean; part: string; file: string; devices: DeviceRow[] }>);

export const getTopologies = (q: { num_slots?: number; num_poles?: number; single_layer?: boolean }) => {
  const p = new URLSearchParams();
  if (q.num_slots != null) p.set('num_slots', String(q.num_slots));
  if (q.num_poles != null) p.set('num_poles', String(q.num_poles));
  if (q.single_layer != null) p.set('single_layer', String(q.single_layer));
  return fetch(`${API}/api/controller/topologies?${p}`).then(j<{
    presets: { id: string; label: string; hint: string }[];
    machine: Record<string, any>; star_delta: string | null;
    coils: CoilRow[]; error: string | null;
  }>);
};

export const postSchematic = (body: any) =>
  fetch(`${API}/api/controller/schematic`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }).then(j<{ topology: any; svg: string }>);

export const solveController = (body: any, fresh = false) =>
  fetch(`${API}/api/controller/solve?fresh=${fresh ? 'true' : 'false'}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }).then(j<ControllerResult>);

export const getLast = () =>
  fetch(`${API}/api/controller/last`).then(j<ControllerResult | Record<string, never>>);

/* ── settings — persisted WITH the configuration (owner 2026-09-22) ──────── */
//
// "при сохранении мотора текущий контроллер тоже должен сохраняться со всеми
// настройками" — the Controller tab's own FORM, never a solve result, saved
// on the same footing as the battery block. `null`/absent numeric fields mean
// "the duty's own" — the SAME blank-means-duty-default convention the panel's
// number boxes already use (Carrier / DC link).

export interface ControllerCoolingSettings {
  /** ``"liquid"`` (default, back-compatible with every save from before
   * 2026-09-22) | ``"air_forced"`` | ``"air_still"`` — see
   * ``inverter.losses.COOLING_MODES``. */
  mode?: string | null;
  coolant?: string | null;
  flow_lpm?: number | null;
  t_in_c?: number | null;
  r_tim_k_w?: number | null;
  /** air_forced only — a fan/slipstream, "wind speed" as in the thermal sim. */
  air_speed_mps?: number | null;
  /** air_forced / air_still — ambient air temperature. */
  t_ambient_c?: number | null;
  /** Wetted area, EITHER as one heatsink per device… */
  heatsink_area_cm2_per_device?: number | null;
  /** …or as one PCB pad shared by every device on it (wins if both are sent). */
  plate_area_cm2?: number | null;
  /** air_forced / air_still — stated constant, default 0.75. */
  fin_efficiency?: number | null;
  /** air_still only — stated constant, default 0.9. */
  emissivity?: number | null;
}

export interface ControllerMappingRowSettings { coil: number; bridge: string; leg: string; }

export interface ControllerSettings {
  saved_at?: string | null;
  device?: string | null;
  topology?: string;
  set_split?: string;
  h_bridge_modulation?: string;
  devices_parallel?: number;
  devices_parallel_by_bridge?: Record<string, number>;
  r_g_ext_ohm?: number | null;
  v_gs_off_V?: number | null;
  dead_time_us?: number | null;
  f_carrier_hz?: number | null;
  v_dc_V?: number | null;
  cooling?: ControllerCoolingSettings;
  mapping?: ControllerMappingRowSettings[];
  couple_with_em?: boolean;
}

/** ``{}`` (never an error) on a configuration that has never saved one. */
export const getControllerSettings = (die: string, config: string) => {
  const p = new URLSearchParams({ die, config });
  return fetch(`${API}/api/controller/settings?${p}`)
    .then(j<ControllerSettings | Record<string, never>>);
};

/** ``PATCH /api/family/config/{die}/{cfg}/controller`` — a WHOLE replace,
 * not a merge: the tab sends its complete state every time. */
export const saveControllerSettings = (die: string, config: string,
                                       settings: ControllerSettings) =>
  fetch(`${API}/api/family/config/${encodeURIComponent(die)}/`
       + `${encodeURIComponent(config)}/controller`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(settings),
  }).then(j<{ ok: boolean; controller: ControllerSettings }>);

/** The panel's own blank-field sentinel: ``''`` means "the duty's own". */
type NumOrBlank = number | '';

/** The panel's full editable state — every ``useState`` the settings column
 * holds, gathered in one shape so loading and saving can each be one pure
 * function, tested without a browser. */
export interface ControllerFormState {
  device: string;
  topology: string;
  setSplit: string;
  hbMod: string;
  nPar: NumOrBlank;
  rg: NumOrBlank;
  vgsOff: NumOrBlank;
  dead: NumOrBlank;
  fsw: NumOrBlank;
  vdc: NumOrBlank;
  coolant: string;
  flow: NumOrBlank;
  tin: NumOrBlank;
  rtim: NumOrBlank;
  /** ``"liquid" | "air_forced" | "air_still"``. */
  coolingMode: string;
  airSpeed: NumOrBlank;
  tAmbient: NumOrBlank;
  /** which of the two area fields ``areaCm2`` below is sent as. */
  areaBasis: 'heatsink' | 'plate';
  areaCm2: NumOrBlank;
  finEff: NumOrBlank;
  emissivity: NumOrBlank;
  mapping: Record<number, string>;
  coupleWithEm: boolean;
}

const toFormNumber = (v: number | null | undefined): NumOrBlank =>
  (v === null || v === undefined) ? '' : v;

const toSaveNumber = (v: NumOrBlank): number | null => (v === '' ? null : v);

/**
 * A saved settings block, put back into the panel's own state shape.
 *
 * ``{}`` or nothing saved yet → the CALLER's current defaults, unchanged —
 * "missing block = the tab's defaults, no error" (owner's own words). Every
 * field of the block is otherwise independent: an old configuration saved
 * before a field existed keeps that one field at the caller's default while
 * every other field it DOES carry still restores.
 */
export function formStateFromSettings(
  block: ControllerSettings | null | undefined,
  fallback: ControllerFormState,
): ControllerFormState {
  if (!block || Object.keys(block).length === 0) return fallback;
  const cooling = block.cooling || {};
  const rows = block.mapping || [];
  const mapping: Record<number, string> = {};
  for (const m of rows) mapping[m.coil] = `${m.bridge}/${m.leg}`;
  // A per-bridge override in the saved block (from the API/CLI, or an older
  // save) is IGNORED here on purpose — the web only ever shows and writes the
  // one global `devices_parallel` (owner 2026-09-22: «Давай сделаем одно
  // общее число»). `settingsForSave` below then clears it on the next save.
  const areaBasis: 'heatsink' | 'plate' = cooling.plate_area_cm2 != null ? 'plate'
    : cooling.heatsink_area_cm2_per_device != null ? 'heatsink' : fallback.areaBasis;
  return {
    device: block.device || fallback.device,
    topology: block.topology || fallback.topology,
    setSplit: block.set_split || fallback.setSplit,
    hbMod: block.h_bridge_modulation || fallback.hbMod,
    nPar: block.devices_parallel ?? fallback.nPar,
    rg: toFormNumber(block.r_g_ext_ohm),
    vgsOff: toFormNumber(block.v_gs_off_V),
    dead: toFormNumber(block.dead_time_us),
    fsw: toFormNumber(block.f_carrier_hz),
    vdc: toFormNumber(block.v_dc_V),
    coolant: cooling.coolant || fallback.coolant,
    flow: toFormNumber(cooling.flow_lpm),
    tin: toFormNumber(cooling.t_in_c),
    rtim: toFormNumber(cooling.r_tim_k_w),
    coolingMode: cooling.mode || fallback.coolingMode,
    airSpeed: toFormNumber(cooling.air_speed_mps),
    tAmbient: toFormNumber(cooling.t_ambient_c),
    areaBasis,
    areaCm2: toFormNumber(areaBasis === 'plate' ? cooling.plate_area_cm2
                                                : cooling.heatsink_area_cm2_per_device),
    finEff: toFormNumber(cooling.fin_efficiency),
    emissivity: toFormNumber(cooling.emissivity),
    mapping: rows.length ? mapping : fallback.mapping,
    coupleWithEm: block.couple_with_em ?? fallback.coupleWithEm,
  };
}

/** The panel's current state, as the PATCH body that saves it whole. */
export function settingsForSave(s: ControllerFormState): ControllerSettings {
  const mapping: ControllerMappingRowSettings[] = Object.entries(s.mapping)
    .map(([coil, v]) => {
      const [bridge, leg] = String(v).split('/');
      return { coil: Number(coil), bridge: bridge || 'INV1', leg: leg || 'A' };
    });
  return {
    device: s.device || null,
    topology: s.topology,
    set_split: s.setSplit,
    h_bridge_modulation: s.hbMod,
    devices_parallel: s.nPar === '' ? 1 : s.nPar,
    // ALWAYS {} — the tab has only the one global count now; this REPLACES
    // (never merges into) whatever a saved block held, so a stale per-bridge
    // override from the API/CLI does not survive the web's own save.
    devices_parallel_by_bridge: {},
    r_g_ext_ohm: toSaveNumber(s.rg),
    v_gs_off_V: toSaveNumber(s.vgsOff),
    dead_time_us: toSaveNumber(s.dead),
    f_carrier_hz: toSaveNumber(s.fsw),
    v_dc_V: toSaveNumber(s.vdc),
    cooling: { mode: s.coolingMode, coolant: s.coolant, flow_lpm: toSaveNumber(s.flow),
              t_in_c: toSaveNumber(s.tin), r_tim_k_w: toSaveNumber(s.rtim),
              air_speed_mps: toSaveNumber(s.airSpeed), t_ambient_c: toSaveNumber(s.tAmbient),
              heatsink_area_cm2_per_device: s.areaBasis === 'heatsink' ? toSaveNumber(s.areaCm2) : null,
              plate_area_cm2: s.areaBasis === 'plate' ? toSaveNumber(s.areaCm2) : null,
              fin_efficiency: toSaveNumber(s.finEff), emissivity: toSaveNumber(s.emissivity) },
    mapping,
    couple_with_em: s.coupleWithEm,
  };
}

/** The `ctrl.settings` localStorage mirror ``ControllerPanel`` writes, TAGGED
 * with the configuration it was captured for. */
export interface ControllerMirror {
  die: string;
  config: string;
  block: ControllerSettings;
}

/**
 * Whether a mirrored settings snapshot belongs to the motor being saved.
 *
 * Owner 2026-09-22, second round: *"при сохранении мотора текущий контроллер
 * тоже должен сохраняться со всеми настройками"* — not only the Controller
 * tab's own button.  ``ActiveFamilyStrip``'s "Save to duty" reads
 * ``ctrl.settings`` right after the duty save and PATCHes it in the same
 * flow — but ONLY when the tag matches: a mirror left over from a DIFFERENT
 * motor (the Controller tab was never opened for the one being saved now, or
 * it still holds an earlier session's snapshot) must never land on this one.
 */
export function controllerMirrorApplies(
  mirrored: ControllerMirror | null | undefined,
  die: string,
  config: string,
): boolean {
  return !!mirrored && !!mirrored.block
    && mirrored.die === die && mirrored.config === config;
}

/** The mirror's own block, when its tag matches ``die``/``config`` — ``null``
 *  otherwise (the Controller tab was never opened for this configuration this
 *  session, or it still holds a different one's snapshot). */
export function readControllerMirror(
  die: string, config: string,
): ControllerSettings | null {
  try {
    const raw = localStorage.getItem('ctrl.settings');
    const mirrored = raw ? JSON.parse(raw) as ControllerMirror : null;
    return controllerMirrorApplies(mirrored, die, config) ? mirrored!.block : null;
  } catch { return null; }
}

/** Save the Controller tab's CURRENT settings — the live mirror
 *  ``ControllerPanel`` keeps in ``ctrl.settings``, not only the last block the
 *  server has — to the active configuration.  Used by the Coupled panel's own
 *  "inverter (Controller)" drive selector (2026-09-22, second round): picking
 *  that option, or starting a coupled run while it is picked, used to lean on
 *  the user having ALREADY pressed the Controller tab's own "Save settings" —
 *  and the common case (choose a device, switch straight to the Coupled tab,
 *  never press Solve) left nothing saved at all, so ``drive: "inverter"``
 *  silently fell back to the duty's own stored controller solve, or to
 *  nothing (``GET /api/controller/settings`` answering ``{}``).  This closes
 *  that gap: the mirror is written on every keystroke, well before any Solve,
 *  so it is there to save even when the tab has never been asked to solve.
 *
 *  ``null`` = nothing to save — no device is chosen anywhere for this
 *  configuration, so the caller's own gating (``controllerReady``) already
 *  keeps the option disabled; this is a defensive no-op, never an error the
 *  caller has to show. */
export async function saveControllerFromMirror(
  die: string, config: string,
): Promise<{ ok: true; block: ControllerSettings } | { ok: false; error: string } | null> {
  const block = readControllerMirror(die, config);
  if (!block || !block.device) return null;
  try {
    const r = await saveControllerSettings(die, config, block);
    return { ok: true, block: r.controller ?? block };
  } catch (e) { return { ok: false, error: String(e) }; }
}

/** The short list the auto-save's HelpTip names — "device, topology, N
 *  parallel, dead time, carrier, DC link, cooling" — never every field, this
 *  is a caption, not a table (project UI rule). */
export function controllerSavedFieldsLine(block: ControllerSettings): string {
  const bits = [`device ${block.device}`];
  if (block.topology) bits.push('topology');
  if (block.devices_parallel != null) bits.push('N parallel');
  if (block.dead_time_us != null) bits.push('dead time');
  if (block.f_carrier_hz != null) bits.push('carrier');
  if (block.v_dc_V != null) bits.push('DC link');
  if (block.cooling && Object.values(block.cooling).some(v => v != null)) bits.push('cooling');
  return bits.join(', ');
}

/** The ``POST /solve`` request body — every field the ROUTE resolves
 * server-side (V_dc, carrier, current, power, connection, rpm — see
 * ``routes.controller._build_request``) is OMITTED here when blank, never
 * sent as ``''`` or ``null``.  Owner 2026-09-22 audit ("Error: v_dc_V is
 * required" — «проверь всё»): a blank number box must vanish from the wire
 * entirely (``JSON.stringify`` drops an ``undefined`` property), so the
 * route's own fallback chain runs — sending ``''`` would instead read as
 * "the request provided v_dc_V" and either crash on `float('')` or, worse,
 * silently shadow a real resolved value with a falsy one. */
export interface ControllerSolveBody {
  device: string;
  devices_parallel?: number;
  topology: string;
  set_split: string;
  h_bridge_modulation: string;
  r_g_ext_ohm?: number;
  v_gs_off_V?: number;
  dead_time_us?: number;
  f_carrier_hz?: number;
  v_dc_V?: number;
  r_tim_k_w?: number;
  cooling: { mode: string; coolant?: string; flow_lpm?: number; t_in_c?: number;
            air_speed_mps?: number; t_ambient_c?: number;
            heatsink_area_cm2_per_device?: number; plate_area_cm2?: number;
            fin_efficiency?: number; emissivity?: number };
  mapping?: ControllerMappingRowSettings[];
  // Deliberately no `devices_parallel_by_bridge` here — the web only ever
  // sends the one global `devices_parallel`; a per-bridge override remains a
  // backend/API-CLI-only feature (owner 2026-09-22: «Давай сделаем одно
  // общее число»).
}

const blank = (v: NumOrBlank): number | undefined => (v === '' ? undefined : v);

export function controllerSolveBody(
  s: ControllerFormState,
  customRows: ControllerMappingRowSettings[],
): ControllerSolveBody {
  const mode = s.coolingMode || 'liquid';
  // Only the fields THIS mode reads — ``ControllerCoolingSpec`` on the
  // backend ignores anything unused, but the wire body stays honest about
  // what the chosen mode actually needs (owner: "exactly the fields each
  // mode needs").
  const cooling: ControllerSolveBody['cooling'] = { mode };
  if (mode === 'liquid') {
    cooling.coolant = s.coolant;
    cooling.flow_lpm = blank(s.flow);
    cooling.t_in_c = blank(s.tin);
  } else {
    cooling.t_ambient_c = blank(s.tAmbient);
    cooling.fin_efficiency = blank(s.finEff);
    if (s.areaBasis === 'plate') cooling.plate_area_cm2 = blank(s.areaCm2);
    else cooling.heatsink_area_cm2_per_device = blank(s.areaCm2);
    if (mode === 'air_forced') cooling.air_speed_mps = blank(s.airSpeed);
    if (mode === 'air_still') cooling.emissivity = blank(s.emissivity);
  }
  return {
    device: s.device,
    devices_parallel: blank(s.nPar),
    topology: s.topology, set_split: s.setSplit, h_bridge_modulation: s.hbMod,
    r_g_ext_ohm: blank(s.rg),
    v_gs_off_V: blank(s.vgsOff),
    dead_time_us: blank(s.dead),
    f_carrier_hz: blank(s.fsw),
    v_dc_V: blank(s.vdc),
    r_tim_k_w: blank(s.rtim),
    cooling,
    mapping: s.topology === 'custom' ? customRows : undefined,
  };
}

/** What ``POST /solve`` WOULD use right now, before anything is solved —
 * the tab's "solving for: …" line. */
export interface ResolvedPoint {
  die: string | null;
  config: string | null;
  duty: string | null;
  i_phase_rms_A: number | null;
  p_ac_W: number | null;
  v_dc_V: number | null;
  f_carrier_hz: number | null;
  star_delta: string | null;
  rpm: number | null;
  modulation_index: number | null;
  power_factor: number | null;
  sources: Record<string, string>;
  line: string | null;
}

export const getResolvedPoint = (die?: string, config?: string, duty?: string) => {
  const p = new URLSearchParams();
  if (die) p.set('die', die);
  if (config) p.set('config', config);
  if (duty) p.set('duty', duty);
  return fetch(`${API}/api/controller/point?${p}`).then(j<ResolvedPoint>);
};

/** One electrical period as an SVG polyline, scaled to its own axis. */
export function polyline(values: number[], w: number, h: number, pad = 2): string {
  if (!values.length) return '';
  let lo = Infinity; let hi = -Infinity;
  for (const v of values) { if (v < lo) lo = v; if (v > hi) hi = v; }
  if (!isFinite(lo) || !isFinite(hi)) return '';
  const span = hi - lo || 1;
  const n = values.length;
  return values.map((v, i) => {
    const x = (i / Math.max(n - 1, 1)) * w;
    const y = pad + (1 - (v - lo) / span) * (h - 2 * pad);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(' ');
}

export const fmt = (v: any, digits = 1, dash = '—'): string =>
  (v === null || v === undefined || Number.isNaN(Number(v)))
    ? dash : Number(v).toLocaleString(undefined, { maximumFractionDigits: digits,
                                                   minimumFractionDigits: digits });

export const pct = (v: any, digits = 2): string =>
  (v === null || v === undefined) ? '—' : `${(Number(v) * 100).toFixed(digits)} %`;

/**
 * The tab's one status line, above the tiles.
 *
 * An error ALWAYS wins — the plain sentence the route sends (never a raw
 * validation error; the 422 for a duty with no electromagnetic record reads
 * "run the Simulation/coupled solve for this duty first…").  Otherwise, once
 * something has solved, it names which point of the duty the numbers below
 * are for — e.g. "solved for the S1 point: 48.6 A rms · 1.99 kW in" — because
 * a continuous (S1) rating REPLACES the coupled record's own machine with the
 * verified S1 one, and tiles with no such line would read like a plain solve
 * of whatever was last typed on the Simulation tab.  `null` before the first
 * solve, when there is nothing to say yet.
 */
export function statusLine(res: ControllerResult | null, err: string | null): string | null {
  if (err) return err;
  return res?.solved_for || null;
}
