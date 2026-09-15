/**
 * One Mechanical / Thermal answer → the flat block a Compare row carries.
 *
 * User 2026-09-07: *"нужно везде сделать такую же кнопку для сравнения всех
 * величин в механических и температурных моделированиях; в температурном нужно
 * все максимальные температуры всех частей мотора сравнивать между собой"* — the
 * Mechanical and Thermal tabs get the Configure tab's "+ Add to comparison"
 * button, and their answers become ROWS of the SAME table the EM points live in.
 *
 * These two functions are PURE and live in their own module for three reasons:
 *
 *   • they are the whole contract of what a stored row contains, so they are
 *     pinned by `__tests__/resultRows.test.mjs` (verbatim-copy convention — the
 *     modules the app ships are TypeScript and reach `import.meta.env`, which
 *     `node --test` cannot load);
 *   • the Compare table's columns read exactly the keys written here, and two
 *     places spelling `winding_max` differently is a column that is always "—";
 *   • nothing here fetches, reads a store or knows about staleness.  WHICH
 *     result may be turned into a row is `addToCompare`'s decision; this file
 *     only refuses a result that is missing or carries no numbers at all.
 *
 * The thermal block deliberately iterates `components` instead of naming the
 * parts: the backend grew five new domains on 2026-09-07 (insulation, wire
 * enamel, wire coating, air gap, pocket air) and will grow more, and a hard-coded
 * list would silently drop the temperature of every part added after this file
 * was written — which is the opposite of "все максимальные температуры всех
 * частей".
 */
import type { CoupledResult, ThermalField } from '../thermal/api';
import { outerCooling } from '../thermal/types';
import type {
  ModalResult, RotorStress, RotordynamicsResult,
} from '../mechanical/api';

/** A stored result block: flat, JSON, one level deep. */
export type ResultBlock = Record<string, number | string | boolean>;

/** A real number, or `undefined` — never `NaN`, never `Number(null) === 0`. */
function num(v: unknown): number | undefined {
  if (v === null || v === undefined || v === '') return undefined;
  const n = Number(v);
  return Number.isFinite(n) ? n : undefined;
}

/** A non-empty string, or `undefined`. */
function str(v: unknown): string | undefined {
  return typeof v === 'string' && v.trim() !== '' ? v : undefined;
}

/**
 * Write a key only when there is a MEASUREMENT behind it.
 *
 * A stored `null` and a stored `NaN` both print as a number-shaped hole in the
 * table and, worse, take part in the best/worst colouring as a zero.  A key
 * that is simply absent prints "—", which is the truth: this machine's answer
 * does not have that quantity (a sleeveless rotor has no sleeve stress, a
 * coarse mesh resolves no liner).
 */
function put(out: ResultBlock, key: string,
             v: number | string | boolean | null | undefined): void {
  if (v === null || v === undefined) return;
  if (typeof v === 'number' && !Number.isFinite(v)) return;
  if (typeof v === 'string' && v.trim() === '') return;
  out[key] = v;
}

/** Column heading for a `<part>_max` key: `slot_fill_max` → "Wire coating".
 *  The two-word air domains get their engineering names rather than the
 *  domain tags the solver uses internally. */
const PART_LABEL: Record<string, string> = {
  gap_air: 'Air gap',
  pocket_air: 'Pocket air',
  slot_fill: 'Slot fill',
};

export function partMaxLabel(key: string): string {
  const part = key.replace(/_max$/, '');
  const named = PART_LABEL[part];
  if (named) return named;
  const words = part.replace(/_/g, ' ');
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/* ═══════════════════════════════════════════════════════════════════════════
 * THERMAL
 * ═══════════════════════════════════════════════════════════════════════════ */

/**
 * The `results.thermal` block of a Compare row.
 *
 * `coupled` is the EM↔thermal fixed point when there is one that belongs to
 * THIS machine — the caller passes `null` for a missing or stale coupled run,
 * because a converged coil temperature from another geometry next to this
 * geometry's field is exactly the mismatch the Compare guard exists to stop.
 *
 * Throws (rather than returning an empty block) when there is nothing to add:
 * a row of dashes in a permanent, server-side library is worse than a refusal
 * the user can read.
 */
export function thermalRowFromResult(
  field: ThermalField | null | undefined,
  coupled?: CoupledResult | null,
): ResultBlock {
  if (!field) {
    throw new Error('There is no thermal result to add — press Solve on the '
      + 'Thermal tab first.');
  }
  const out: ResultBlock = {};

  put(out, 'T_max', num(field.T_max));
  put(out, 'T_min', num(field.T_min));

  // EVERY component the payload carries, whatever it is called — see the file
  // header.  `null` means "no elements of that domain on this mesh", which is
  // not a temperature and must not become one.
  const comps = (field.components ?? {}) as
    Record<string, { max?: number; avg?: number } | null | undefined>;
  for (const [part, c] of Object.entries(comps)) {
    if (!c) continue;
    put(out, `${part}_max`, num(c.max));
  }
  // …plus the MEAN of the two parts whose mean is read as often as their peak:
  // the winding (insulation class is judged on the peak, the I²R on the mean)
  // and the magnet (Br falls with the mean, the knee with the peak).
  put(out, 'winding_avg', num(comps.winding?.avg));
  put(out, 'magnet_avg', num(comps.magnet?.avg));

  if (out.T_max === undefined
      && !Object.keys(out).some((k) => k.endsWith('_max'))) {
    throw new Error('The thermal result carries no temperatures — it is a mesh, '
      + 'not a solve. Press Solve on the Thermal tab first.');
  }

  /* The two cooled surfaces, as the SOLVER echoed them back.  `outerCooling`
     is the one reader of that block (a payload cached before the two-surface
     split of 2026-09-07 is flat), so this block and the tab's tiles can never
     disagree about which h belongs to which surface. */
  const outer = outerCooling(field.cooling);
  const inner = field.cooling?.inner;
  put(out, 'sink_outer_c', num(outer.t_sink_c));
  put(out, 'h_outer', num(outer.h_conv ?? field.h_conv));
  put(out, 't_out_outer_c', num(outer.t_out_c));
  put(out, 'h_bore', num(inner?.h_conv));
  put(out, 't_out_bore_c', num(inner?.t_out_c));
  /* The rotor's third path: the shaft outside the housing.  Its conductance is
     a RESULT (the fin follows from the length, the diameter and the speed), so
     it belongs here and not among the inputs — the length and the side count
     are what the user chose, and those are in `thermalInputsFromResult`. */
  const shaft = field.cooling?.shaft_ends;
  put(out, 'shaft_ends_G', num(shaft?.G_W_per_K));
  put(out, 'h_shaft_ends', num(shaft?.h_conv));
  /* And the OPEN frame's two paths (2026-09-09), for the same reason: both
     conductances follow from the wash, the bundle and the measured channel, so
     they are results even though the frame itself is a choice. */
  put(out, 'end_windings_G', num(field.cooling?.end_windings?.G_W_per_K));
  put(out, 'h_end_windings', num(field.cooling?.end_windings?.h_conv));
  put(out, 'slot_channels_G', num(field.cooling?.slot_channels?.G_W_per_K));
  put(out, 'h_slot_channels', num(field.cooling?.slot_channels?.h_conv));

  /* Where the heat went, in machine watts, integrated on the solved mesh. */
  const budget = field.cooling?.heat_budget;
  put(out, 'housing_W', num(budget?.housing_W));
  put(out, 'bore_W', num(budget?.bore_W));
  put(out, 'gap_W', num(budget?.gap_W));
  put(out, 'shaft_ends_W', num(budget?.shaft_ends_W));
  put(out, 'end_windings_W', num(budget?.end_windings_W));
  put(out, 'slot_channels_W', num(budget?.slot_channels_W));
  put(out, 'losses_W', num(budget?.losses_W ?? field.P_loss_total_W));
  put(out, 'P_cu_W', num(field.P_cu_W));
  put(out, 'P_fe_W', num(field.P_fe_W));

  /* The two conductivities that decide the rotor's temperature: the gap film
     (a RESULT — Taylor–Couette on this machine's gap and speed) and the band
     in series with it. */
  put(out, 'gap_k_eff', num(field.cooling?.gap?.k_eff ?? field.gap_k));
  put(out, 'sleeve_k_radial', num(field.cooling?.sleeve?.k));

  /* Where the heat MAP came from — a reused Electromagnetic run, or one solved for
     this request.  Not decoration: two rows whose temperatures came from
     different loss maps are not comparable, and this is the only witness.
     Read structurally because `loss_source` is the backend's own dict
     ({kind, run_id, computed_at, note}) and is not in the payload type. */
  const src = (field as unknown as { loss_source?: { kind?: unknown } }).loss_source;
  put(out, 'loss_source_kind', str(src?.kind));

  if (coupled) {
    put(out, 'coupled_coil_c', num(coupled.coil_temp_converged_C));
    // Converged AND not run away: a loop that diverged reports a number, and
    // that number is where the iteration got to, not an equilibrium.
    out.coupled_converged = !!coupled.converged && !coupled.runaway;
  }

  return out;
}

/* ═══════════════════════════════════════════════════════════════════════════
 * MECHANICAL
 * ═══════════════════════════════════════════════════════════════════════════ */

/**
 * The `results.mechanical` block of a Compare row.
 *
 * ONE load case per row — the one being viewed, else the result's own primary
 * case, else `rated`, else the only one there is.  Three cases side by side in
 * one row would need three columns per quantity, and a single-speed answer
 * (the tab's default since 2026-09-06) has exactly one anyway.
 *
 * `modal` and `rotordyn` are optional and are passed as `null` by the caller
 * when they are missing or belong to a previous geometry, for the same reason
 * `coupled` is above.
 */
export function mechanicalRowFromResult(
  stress: RotorStress | null | undefined,
  modal?: ModalResult | null,
  rotordyn?: RotordynamicsResult | null,
  wantCase?: string,
): ResultBlock {
  if (!stress) {
    throw new Error('There is no mechanical result to add — press Solve on the '
      + 'Mechanical tab first.');
  }
  const names = Object.keys(stress.cases ?? {});
  if (!names.length) {
    throw new Error('The mechanical result carries no load case — press Solve '
      + 'on the Mechanical tab first.');
  }
  const name = (wantCase && names.includes(wantCase)) ? wantCase
    : (stress.primary_case && names.includes(stress.primary_case))
      ? stress.primary_case
      : (names.includes('rated') ? 'rated' : names[0]);
  const c = stress.cases[name];

  const out: ResultBlock = {};
  put(out, 'case', name);
  put(out, 'rpm', num(c?.rpm ?? stress.rpm));
  put(out, 'rpm_overspeed', num(stress.overspeed_rpm));

  const part = (n: string) => c?.parts?.[n];
  /** The safety factor to QUOTE: p05 (the field with the singular corner
   *  elements removed) when the backend reports it, the raw element minimum
   *  otherwise, the part's own factor last. */
  /* THE safety factor: strength over the governing AVERAGED stress, which is
     the stress every table beside it prints (user 2026-09-10 — one number for
     one part, the way ANSYS and Fusion report it).  `safety_factor` on the part
     is the same number; the percentile and the element minimum are the
     fallbacks for a result solved before the convention changed. */
  const sf = (n: string): number | undefined => {
    const s = c?.sf_min_per_part?.[n];
    return num(s?.averaged) ?? num(part(n)?.safety_factor)
      ?? num(s?.p05) ?? num(s?.min);
  };

  /* SLEEVE.  The payload has no p99.5 of the hoop stress — and does not need
     one: a hoop-wound band is a smooth annulus with no re-entrant corner, so
     its peak hoop element is not a singularity the way a bridge root is, and
     the max IS the number the burst check is written against.  The key keeps
     the p995 spelling the other stress columns use so one column means one
     thing across the table. */
  const sleeve = part('sleeve');
  put(out, 'sleeve_hoop_p995_mpa', num(sleeve?.hoop_max_mpa));
  put(out, 'sf_sleeve', sf('sleeve'));
  put(out, 'sleeve_rad_min_mpa', num(sleeve?.radial_min_mpa));

  /* MAGNET.  Sintered NdFeB does not yield, it cracks, so the checks are the
     tensile principal (p99.5) and the most compressive one. */
  const magnet = part('magnet');
  put(out, 'magnet_s1_p995_mpa', num(magnet?.principal_max_p995_mpa));
  put(out, 'magnet_s2_min_mpa', num(magnet?.principal_min_mpa));

  /* ROTOR IRON and SHAFT — von Mises against yield, p99.5 because the peak
     normally sits at a bridge root, which is a singularity in a sharp-cornered
     model. */
  put(out, 'iron_vm_p995_mpa', num(part('rotor')?.von_mises_p995_mpa));
  put(out, 'sf_iron', sf('rotor'));
  put(out, 'shaft_vm_p995_mpa', num(part('shaft')?.von_mises_p995_mpa));

  /* The number that comes straight off the air gap: the largest radial travel
     of the outermost rotating surface — the top of the band when there is one.
     The mean beside it says how much of that is the whole ring growing and how
     much is lobing between the poles (user 2026-09-10). */
  put(out, 'od_growth_um', num(c?.rotor_od_growth_um));
  put(out, 'od_growth_mean_um', num((c as { od_growth?: { mean_um?: unknown } } | null
                                     | undefined)?.od_growth?.mean_um));

  /* The interfaces, as PERCENT open — the same number the tab's table prints,
     so a row and the tab it came from never quote the joint differently. */
  const openPct = (pair: string): number | undefined => {
    const f = num(c?.interfaces?.[pair]?.open_fraction);
    return f === undefined ? undefined : f * 100;
  };
  put(out, 'open_sleeve_magnet', openPct('sleeve_magnet'));
  put(out, 'open_sleeve_rotor', openPct('sleeve_rotor'));
  put(out, 'open_magnet_rotor', openPct('magnet_rotor'));

  put(out, 'retention_verdict', str(c?.magnet_retention?.verdict));

  /* Can the torque get out of the poles?  `held` is a THREE-state answer —
     true / false / null "no verdict" (the solve ran away or a part came
     loose) — so it is stored only when it is a real boolean. */
  const tp = c?.torque_path;
  put(out, 'torque_path_verdict', str(tp?.verdict));
  if (typeof tp?.held === 'boolean') out.poles_held = tp.held;

  /* The two frequency models: the ring modes of the iron, and the shaft line's
     critical speeds.  Both are separate solves on the same tab, and a row that
     has one and not the other is normal. */
  const modes = modal?.modes ?? [];
  put(out, 'mode1_hz', num(modes[0]?.f_hz));
  put(out, 'mode2_hz', num(modes[1]?.f_hz));

  // The LOWEST critical speed is the one that decides whether the rated point
  // is allowed, whatever order the backend listed them in.
  const crits = (rotordyn?.critical_speeds ?? [])
    .filter((x) => num(x?.rpm) !== undefined);
  const first = crits.length
    ? crits.reduce((a, b) => (b.rpm < a.rpm ? b : a))
    : null;
  put(out, 'critical1_rpm', num(first?.rpm));
  put(out, 'critical_margin_pct', num(first?.margin_vs_rated_pct));

  return out;
}

/* ═══════════════════════════════════════════════════════════════════════════
 * THE TAB'S OWN STACK — one row of a LOCAL comparison table
 *
 * User 2026-09-07: *"сделай локальное сравнение по параметрам тепловой
 * симуляции, только как в Configure; так же сделай в механике"* — pressing "+
 * Add to comparison" must also stack the variant into a table ON THE TAB, the
 * way the Configure tab stacks its configurations, so two cooling designs (or
 * two fits) can be read against each other without leaving the physics.
 *
 * A local row is the SAME `results` block the Compare library gets — the two
 * must never disagree about what `sleeve_hoop_p995_mpa` means — plus an
 * `inputs` block, which the Compare row carries as flat `therm_` / `mech_`
 * params.  Both blocks are built HERE, pure, for the reasons in the file
 * header: the columns read exactly these keys, and the node test pins them.
 *
 * The inputs are read off the RESULT wherever the payload echoes them, exactly
 * as `addToCompare` does: switching the outer surface from air to a water
 * jacket without pressing Solve changes no geometry, so nothing marks the
 * result stale — but a row headed "liquid, 12 L/min" over temperatures solved
 * in still air is the one thing this file exists to prevent.  The panel's own
 * field is the fallback for a payload that does not echo that input.
 * ═══════════════════════════════════════════════════════════════════════════ */

/** One stacked variant: what was set, what came out, and when. */
export interface LocalRow {
  id: string;
  /** the user's label — auto-named on add, renameable in the table */
  name: string;
  /** ISO stamp of the moment it was stacked */
  at: string;
  inputs: ResultBlock;
  results: ResultBlock;
}

/**
 * How many variants one tab may stack.
 *
 * Not a taste limit: the rows persist through `/api/panel_settings/<panel>`,
 * whose whole settings blob is capped at 64 KiB by the backend, and a thermal
 * row is ~1 KB of JSON.  Sixteen leaves the cap a wide margin, and the panel
 * REFUSES the seventeenth with a sentence rather than letting the save fail
 * silently and losing the stack on the next reload.
 */
export const MAX_LOCAL_ROWS = 16;

/** One stored block, with everything that is not a plain scalar dropped.
 *  What comes back from the server is JSON somebody else's browser wrote. */
function flatBlock(v: unknown): ResultBlock {
  const out: ResultBlock = {};
  if (!v || typeof v !== 'object' || Array.isArray(v)) return out;
  for (const [k, x] of Object.entries(v as Record<string, unknown>)) {
    if (typeof x === 'number') { if (Number.isFinite(x)) out[k] = x; }
    else if (typeof x === 'string' || typeof x === 'boolean') out[k] = x;
  }
  return out;
}

/**
 * Adopt stored rows — from this browser's localStorage or from the server.
 *
 * Neither source is trusted: both are JSON that a previous version of this app
 * (or another browser, or a hand-edited file) wrote, and a row whose `results`
 * is a string would take the table's formatter down with it.  Anything that is
 * not a row is dropped rather than repaired, and the newest `MAX_LOCAL_ROWS`
 * survive — the same cap an add is refused at.
 */
export function normalizeLocalRows(v: unknown): LocalRow[] {
  if (!Array.isArray(v)) return [];
  const out: LocalRow[] = [];
  for (const r of v) {
    if (!r || typeof r !== 'object' || Array.isArray(r)) continue;
    const o = r as Record<string, unknown>;
    const id = typeof o.id === 'string' && o.id.trim() !== '' ? o.id : '';
    if (!id) continue;
    const inputs = flatBlock(o.inputs);
    const results = flatBlock(o.results);
    // A row with neither half is not a variant, it is noise.
    if (!Object.keys(inputs).length && !Object.keys(results).length) continue;
    out.push({
      id,
      name: typeof o.name === 'string' && o.name.trim() !== '' ? o.name : id,
      at: typeof o.at === 'string' ? o.at : '',
      inputs,
      results,
    });
  }
  return out.slice(-MAX_LOCAL_ROWS);
}

/** The operating point, as the Electromagnetic tab publishes it.  Optional in
 *  every field: a tab that has never been run has none of it, and an absent
 *  input is an absent column rather than a zero nobody typed. */
export interface OperatingPointInputs {
  I_phase_rms?: number; gamma_deg?: number; rpm?: number; coil_temp_c?: number;
}

/** The Thermal panel's cooling fields, as the store keeps them (text). */
export interface ThermalPanelInputs {
  coolMode: string; ambientT: string; airSpeed: string; fluid: string;
  tIn: string; hConv: string; flowLpm: string;
  boreMode: string; boreAirSpeed: string; boreFluid: string;
  boreTIn: string; boreFlowLpm: string;
  shaftExtMm: string; shaftExtSides: string;
  frame: string; openAirSpeed: string;
}

/**
 * What this temperature field was solved AGAINST: the two boundary conditions,
 * the operating point they were solved at, and the three conductivities that
 * decide a winding temperature (with the card each came from — a liner solved
 * with the assigned Nomex and one solved with our default are two claims).
 *
 * A field that belongs to a mode that is not selected is NOT written: an air
 * speed beside a water jacket is a number the solve never used, and as a
 * column it would read as a difference between two rows that have none.
 */
export function thermalInputsFromResult(
  field: ThermalField | null | undefined,
  st: ThermalPanelInputs,
  op?: OperatingPointInputs | null,
): ResultBlock {
  const out: ResultBlock = {};
  const outer = outerCooling(field?.cooling);
  const inner = field?.cooling?.inner;
  const mode = str(outer.mode) ?? st.coolMode;
  const bore = str(inner?.mode) ?? st.boreMode;

  put(out, 'cool_mode', mode);
  // The ambient is the air BOTH air modes work against, so it is written
  // whenever either surface blows air — and never otherwise.
  if (mode === 'air' || bore === 'air') {
    put(out, 'ambient_c', num(field?.ambient_temp) ?? num(st.ambientT));
  }
  if (mode === 'air') {
    put(out, 'air_speed_mps', num(outer.air_speed_mps) ?? num(st.airSpeed));
  }
  if (mode === 'liquid') {
    put(out, 'fluid', str(outer.fluid) ?? st.fluid);
    put(out, 'fluid_in_c', num(outer.t_in_c) ?? num(st.tIn));
    put(out, 'flow_lpm', num(outer.flow_lpm) ?? num(st.flowLpm));
  }
  // Only in manual mode: in every other mode the outer h is a RESULT (it is in
  // the results block as `h_outer`), and repeating it as an input would claim
  // the user chose it.
  if (mode === 'manual') {
    put(out, 'h_manual', num(outer.h_conv) ?? num(st.hConv));
  }

  put(out, 'bore_mode', bore);
  if (bore === 'air') {
    put(out, 'bore_air_speed_mps', num(inner?.air_speed_mps) ?? num(st.boreAirSpeed));
  }
  if (bore === 'liquid') {
    put(out, 'bore_fluid', str(inner?.fluid) ?? st.boreFluid);
    put(out, 'bore_in_c', num(inner?.t_in_c) ?? num(st.boreTIn));
    put(out, 'bore_flow_lpm', num(inner?.flow_lpm) ?? num(st.boreFlowLpm));
  }

  /* The exposed shaft, when there is one.  Written whenever the length is
     non-zero and never otherwise: a side count beside a stub of 0 mm is a
     column two rows would differ in without differing in any physics — the same
     rule the air speed beside a water jacket follows. */
  const shaft = field?.cooling?.shaft_ends;
  const shaftMm = num(shaft?.length_each_side_mm) ?? num(st.shaftExtMm);
  if (shaftMm !== undefined && shaftMm > 0) {
    put(out, 'shaft_out_mm', shaftMm);
    put(out, 'shaft_sides', num(shaft?.sides) ?? num(st.shaftExtSides));
  }

  /* HOW THE MACHINE IS BUILT (2026-09-09).  Written only when it is OPEN, the
     same rule the shaft above follows: `housed` is the default every row
     without this column already is, and a column that says "housed" on every
     row is a column nobody can read a difference off.  The SPEED goes with it,
     because on an open frame it is the boundary condition. */
  const frame = str(field?.cooling?.frame) ?? st.frame;
  if (frame === 'open') {
    put(out, 'frame', 'open');
    put(out, 'open_air_mps',
        num(field?.cooling?.end_windings?.air_speed_mps) ?? num(st.openAirSpeed));
  }

  /* The operating point — from the Electromagnetic tab, which is the only
     place it is ever read from (standing project rule).  It is an INPUT of
     this row because two cooling designs compared at two different currents
     are not a comparison of cooling designs. */
  put(out, 'I_A', num(op?.I_phase_rms));
  put(out, 'gamma_deg', num(op?.gamma_deg));
  put(out, 'rpm', num(op?.rpm));
  put(out, 'coil_c', num(op?.coil_temp_c));

  const mu = field?.materials_used;
  put(out, 'liner_mat', str(mu?.liner?.material));
  put(out, 'liner_k', num(mu?.liner?.k));
  put(out, 'enamel_mat', str(mu?.enamel?.material));
  put(out, 'enamel_k', num(mu?.enamel?.k));
  put(out, 'fill_mat', str(mu?.slot_fill?.material));
  put(out, 'fill_k', num(mu?.slot_fill?.k));

  return out;
}

/** One row of the Thermal tab's local table: the same `results` block the
 *  Compare library stores, plus what it was solved against. */
export function localThermalRow(
  field: ThermalField | null | undefined,
  coupled: CoupledResult | null | undefined,
  st: ThermalPanelInputs,
  op?: OperatingPointInputs | null,
): { inputs: ResultBlock; results: ResultBlock } {
  // Results FIRST: a missing or empty result must throw its sentence before
  // anything half-built is handed back.
  const results = thermalRowFromResult(field, coupled);
  return { inputs: thermalInputsFromResult(field, st, op), results };
}

/** The Mechanical panel's fields, as the store keeps them (text + contacts). */
export interface MechanicalPanelInputs {
  cases: string; loads: string; torque: string; rpm: string; rpm1: string;
  osf: string; interf: string; rotorTempC: string; sleeveTempC: string;
  meshMm: string;
  contacts: Record<string, { type: string; mu: number }>;
}

/** What this stress answer was solved WITH — speeds, loads, the fit, the two
 *  temperatures, the mesh, and the four joints as one string. */
export function mechanicalInputsFromResult(
  stress: RotorStress | null | undefined,
  st: MechanicalPanelInputs,
): ResultBlock {
  const out: ResultBlock = {};
  const single = (str(stress?.case_mode) ?? st.cases) === 'single';
  put(out, 'case_mode', str(stress?.case_mode) ?? st.cases);
  put(out, 'rpm', num(stress?.rpm) ?? num(single ? st.rpm1 : st.rpm));
  put(out, 'rpm_overspeed', num(stress?.overspeed_rpm));
  put(out, 'loads', str(stress?.loads) ?? st.loads);
  put(out, 'torque_nm', num(stress?.torque_nm) ?? num(st.torque));
  put(out, 'interference_mm', num(stress?.interference_mm) ?? num(st.interf));
  // The fit the band actually FEELS once the rotor is hot — an input of the
  // machine, not of the panel, and the number two thermal variants differ by.
  put(out, 'interference_eff_mm', num(stress?.interference_effective_mm));
  /* ONE temperature per solid (2026-09-08): "в механический расчёт тоже нужно
     делать каплинг, чтобы температуры везде были одинаковы".  The four are read
     off `thermal.part_temps_c`, which the solver always fills — whether each
     part was named or inherited the scalar — so a row never has to re-derive
     the fallback rule to say what was applied.  `rotor_c` KEEPS its key (it is
     the core, and rows stacked before today already carry it); an answer from
     before this field existed falls back to the two scalars and then to the
     panel's own boxes, exactly as it did. */
  const pt = stress?.thermal?.part_temps_c;
  put(out, 'rotor_c', num(pt?.rotor_core) ?? num(stress?.thermal?.rotor_temp_c)
    ?? num(st.rotorTempC));
  put(out, 'magnet_c', num(pt?.magnet) ?? num(stress?.thermal?.rotor_temp_c)
    ?? num(st.rotorTempC));
  put(out, 'shaft_c', num(pt?.shaft) ?? num(stress?.thermal?.rotor_temp_c)
    ?? num(st.rotorTempC));
  put(out, 'sleeve_c', num(pt?.sleeve) ?? num(stress?.thermal?.sleeve_temp_c)
    ?? num(st.sleeveTempC));
  put(out, 'mesh_mm', num(stress?.mesh?.mesh_size_mm) ?? num(st.meshMm));

  /* ONE string, because the four joints are one model: two rows whose contacts
     differ are not comparable, and a single column says so on one line.  The
     order is the store's, which is the panel's — a set that reads differently
     from row to row would look like a change that never happened. */
  const pairs = Object.keys(st.contacts ?? {});
  if (pairs.length) {
    put(out, 'contacts', pairs.map((p) => {
      const c = stress?.contacts?.[p] ?? st.contacts[p];
      if (!c) return `${p}:—`;
      return `${p}:${c.type}${c.type === 'separation' ? ` µ${c.mu}` : ''}`;
    }).join(' · '));
  }
  // The band is the part the whole tab is about; which one it is belongs
  // beside its hoop stress.
  put(out, 'sleeve_mat', str(stress?.materials?.sleeve?.material));

  return out;
}

/** One row of the Mechanical tab's local table. */
export function localMechanicalRow(
  stress: RotorStress | null | undefined,
  modal: ModalResult | null | undefined,
  rotordyn: RotordynamicsResult | null | undefined,
  st: MechanicalPanelInputs,
  wantCase?: string,
): { inputs: ResultBlock; results: ResultBlock } {
  const results = mechanicalRowFromResult(stress, modal, rotordyn, wantCase);
  const inputs = mechanicalInputsFromResult(stress, st);
  // WHICH case this row is about is a choice, not an outcome — and it is the
  // builder's choice, so it is copied rather than re-decided here.
  put(inputs, 'case', results.case);
  return { inputs, results };
}
