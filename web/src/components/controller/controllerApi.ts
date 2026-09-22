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
