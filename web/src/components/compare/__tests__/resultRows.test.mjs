/**
 * The Compare row-builders, pinned without a browser.
 *
 * `thermalRowFromResult` / `mechanicalRowFromResult` decide WHAT a comparison
 * row contains, and the Compare table's columns read exactly the keys they
 * write — so a renamed key is a column that silently prints "—" for every row
 * from that day on, in a library that is stored server-side and outlives the
 * session.  Three properties are worth a test each:
 *
 *   • EVERY component of the thermal payload becomes a `<part>_max`, including
 *     domains that did not exist when this file was written (the user asked for
 *     "все максимальные температуры всех частей мотора", and the backend grew
 *     five new domains on 2026-09-07 alone);
 *   • a missing or empty result THROWS a sentence an engineer can act on,
 *     rather than storing a permanent row of dashes;
 *   • the numbers are re-keyed, not re-computed — an open fraction becomes a
 *     percentage exactly once, and nothing else changes value on the way in.
 *
 * The bodies below are copied VERBATIM from `compare/resultRows.ts` (and, for
 * `outerCooling`, from `thermal/types.ts`) rather than imported: those modules
 * are TypeScript and reach `import.meta.env` through the api layer, which
 * `node --test` cannot load.  What is pinned is therefore the CONTRACT; a
 * rewrite that changes it has to change this file too, and that is the moment
 * someone has to justify the new shape.
 *
 *   node --test src/components/compare/__tests__/resultRows.test.mjs
 */
import test from 'node:test';
import assert from 'node:assert/strict';

/* ── verbatim copy of thermal/types.outerCooling ──────────────────────────── */

function outerCooling(c) {
  if (!c) return {};
  if (c.outer) return c.outer;
  return {
    mode: c.mode, fluid: c.fluid, h_conv: c.h_conv, t_sink_c: c.t_sink_c,
    air_speed_mps: c.air_speed_mps, flow_lpm: c.flow_lpm,
    t_in_c: c.fluid_temp_in_c, t_out_c: c.fluid_temp_out_c, note: c.note,
  };
}

/* ── verbatim copies of compare/resultRows.ts ─────────────────────────────── */

function num(v) {
  if (v === null || v === undefined || v === '') return undefined;
  const n = Number(v);
  return Number.isFinite(n) ? n : undefined;
}

function str(v) {
  return typeof v === 'string' && v.trim() !== '' ? v : undefined;
}

function put(out, key, v) {
  if (v === null || v === undefined) return;
  if (typeof v === 'number' && !Number.isFinite(v)) return;
  if (typeof v === 'string' && v.trim() === '') return;
  out[key] = v;
}

const PART_LABEL = {
  gap_air: 'Air gap',
  pocket_air: 'Pocket air',
  slot_fill: 'Slot fill',
};

function partMaxLabel(key) {
  const part = key.replace(/_max$/, '');
  const named = PART_LABEL[part];
  if (named) return named;
  const words = part.replace(/_/g, ' ');
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function thermalRowFromResult(field, coupled) {
  if (!field) {
    throw new Error('There is no thermal result to add — press Solve on the '
      + 'Thermal tab first.');
  }
  const out = {};

  put(out, 'T_max', num(field.T_max));
  put(out, 'T_min', num(field.T_min));

  const comps = field.components ?? {};
  for (const [part, c] of Object.entries(comps)) {
    if (!c) continue;
    put(out, `${part}_max`, num(c.max));
  }
  put(out, 'winding_avg', num(comps.winding?.avg));
  put(out, 'magnet_avg', num(comps.magnet?.avg));

  if (out.T_max === undefined
      && !Object.keys(out).some((k) => k.endsWith('_max'))) {
    throw new Error('The thermal result carries no temperatures — it is a mesh, '
      + 'not a solve. Press Solve on the Thermal tab first.');
  }

  const outer = outerCooling(field.cooling);
  const inner = field.cooling?.inner;
  put(out, 'sink_outer_c', num(outer.t_sink_c));
  put(out, 'h_outer', num(outer.h_conv ?? field.h_conv));
  put(out, 't_out_outer_c', num(outer.t_out_c));
  put(out, 'h_bore', num(inner?.h_conv));
  put(out, 't_out_bore_c', num(inner?.t_out_c));
  const shaft = field.cooling?.shaft_ends;
  put(out, 'shaft_ends_G', num(shaft?.G_W_per_K));
  put(out, 'h_shaft_ends', num(shaft?.h_conv));

  const budget = field.cooling?.heat_budget;
  put(out, 'housing_W', num(budget?.housing_W));
  put(out, 'bore_W', num(budget?.bore_W));
  put(out, 'gap_W', num(budget?.gap_W));
  put(out, 'shaft_ends_W', num(budget?.shaft_ends_W));
  put(out, 'losses_W', num(budget?.losses_W ?? field.P_loss_total_W));
  put(out, 'P_cu_W', num(field.P_cu_W));
  put(out, 'P_fe_W', num(field.P_fe_W));

  put(out, 'gap_k_eff', num(field.cooling?.gap?.k_eff ?? field.gap_k));
  put(out, 'sleeve_k_radial', num(field.cooling?.sleeve?.k));

  const src = field.loss_source;
  put(out, 'loss_source_kind', str(src?.kind));

  if (coupled) {
    put(out, 'coupled_coil_c', num(coupled.coil_temp_converged_C));
    out.coupled_converged = !!coupled.converged && !coupled.runaway;
  }

  return out;
}

function mechanicalRowFromResult(stress, modal, rotordyn, wantCase) {
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

  const out = {};
  put(out, 'case', name);
  put(out, 'rpm', num(c?.rpm ?? stress.rpm));
  put(out, 'rpm_overspeed', num(stress.overspeed_rpm));

  const part = (n) => c?.parts?.[n];
  const sf = (n) => {
    const s = c?.sf_min_per_part?.[n];
    return num(s?.p05) ?? num(s?.min) ?? num(part(n)?.safety_factor);
  };

  const sleeve = part('sleeve');
  put(out, 'sleeve_hoop_p995_mpa', num(sleeve?.hoop_max_mpa));
  put(out, 'sf_sleeve', sf('sleeve'));
  put(out, 'sleeve_rad_min_mpa', num(sleeve?.radial_min_mpa));

  const magnet = part('magnet');
  put(out, 'magnet_s1_p995_mpa', num(magnet?.principal_max_p995_mpa));
  put(out, 'magnet_s2_min_mpa', num(magnet?.principal_min_mpa));

  put(out, 'iron_vm_p995_mpa', num(part('rotor')?.von_mises_p995_mpa));
  put(out, 'sf_iron', sf('rotor'));
  put(out, 'shaft_vm_p995_mpa', num(part('shaft')?.von_mises_p995_mpa));

  put(out, 'od_growth_um', num(c?.rotor_od_growth_um));

  const openPct = (pair) => {
    const f = num(c?.interfaces?.[pair]?.open_fraction);
    return f === undefined ? undefined : f * 100;
  };
  put(out, 'open_sleeve_magnet', openPct('sleeve_magnet'));
  put(out, 'open_sleeve_rotor', openPct('sleeve_rotor'));
  put(out, 'open_magnet_rotor', openPct('magnet_rotor'));

  put(out, 'retention_verdict', str(c?.magnet_retention?.verdict));

  const tp = c?.torque_path;
  put(out, 'torque_path_verdict', str(tp?.verdict));
  if (typeof tp?.held === 'boolean') out.poles_held = tp.held;

  const modes = modal?.modes ?? [];
  put(out, 'mode1_hz', num(modes[0]?.f_hz));
  put(out, 'mode2_hz', num(modes[1]?.f_hz));

  const crits = (rotordyn?.critical_speeds ?? [])
    .filter((x) => num(x?.rpm) !== undefined);
  const first = crits.length
    ? crits.reduce((a, b) => (b.rpm < a.rpm ? b : a))
    : null;
  put(out, 'critical1_rpm', num(first?.rpm));
  put(out, 'critical_margin_pct', num(first?.margin_vs_rated_pct));

  return out;
}

const MAX_LOCAL_ROWS = 16;

function flatBlock(v) {
  const out = {};
  if (!v || typeof v !== 'object' || Array.isArray(v)) return out;
  for (const [k, x] of Object.entries(v)) {
    if (typeof x === 'number') { if (Number.isFinite(x)) out[k] = x; }
    else if (typeof x === 'string' || typeof x === 'boolean') out[k] = x;
  }
  return out;
}

function normalizeLocalRows(v) {
  if (!Array.isArray(v)) return [];
  const out = [];
  for (const r of v) {
    if (!r || typeof r !== 'object' || Array.isArray(r)) continue;
    const o = r;
    const id = typeof o.id === 'string' && o.id.trim() !== '' ? o.id : '';
    if (!id) continue;
    const inputs = flatBlock(o.inputs);
    const results = flatBlock(o.results);
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

function thermalInputsFromResult(field, st, op) {
  const out = {};
  const outer = outerCooling(field?.cooling);
  const inner = field?.cooling?.inner;
  const mode = str(outer.mode) ?? st.coolMode;
  const bore = str(inner?.mode) ?? st.boreMode;

  put(out, 'cool_mode', mode);
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

  const shaft = field?.cooling?.shaft_ends;
  const shaftMm = num(shaft?.length_each_side_mm) ?? num(st.shaftExtMm);
  if (shaftMm !== undefined && shaftMm > 0) {
    put(out, 'shaft_out_mm', shaftMm);
    put(out, 'shaft_sides', num(shaft?.sides) ?? num(st.shaftExtSides));
  }

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

function localThermalRow(field, coupled, st, op) {
  const results = thermalRowFromResult(field, coupled);
  return { inputs: thermalInputsFromResult(field, st, op), results };
}

function mechanicalInputsFromResult(stress, st) {
  const out = {};
  const single = (str(stress?.case_mode) ?? st.cases) === 'single';
  put(out, 'case_mode', str(stress?.case_mode) ?? st.cases);
  put(out, 'rpm', num(stress?.rpm) ?? num(single ? st.rpm1 : st.rpm));
  put(out, 'rpm_overspeed', num(stress?.overspeed_rpm));
  put(out, 'loads', str(stress?.loads) ?? st.loads);
  put(out, 'torque_nm', num(stress?.torque_nm) ?? num(st.torque));
  put(out, 'interference_mm', num(stress?.interference_mm) ?? num(st.interf));
  put(out, 'interference_eff_mm', num(stress?.interference_effective_mm));
  put(out, 'rotor_c', num(stress?.thermal?.rotor_temp_c) ?? num(st.rotorTempC));
  put(out, 'sleeve_c', num(stress?.thermal?.sleeve_temp_c) ?? num(st.sleeveTempC));
  put(out, 'mesh_mm', num(stress?.mesh?.mesh_size_mm) ?? num(st.meshMm));

  const pairs = Object.keys(st.contacts ?? {});
  if (pairs.length) {
    put(out, 'contacts', pairs.map((p) => {
      const c = stress?.contacts?.[p] ?? st.contacts[p];
      if (!c) return `${p}:—`;
      return `${p}:${c.type}${c.type === 'separation' ? ` µ${c.mu}` : ''}`;
    }).join(' · '));
  }
  put(out, 'sleeve_mat', str(stress?.materials?.sleeve?.material));

  return out;
}

function localMechanicalRow(stress, modal, rotordyn, st, wantCase) {
  const results = mechanicalRowFromResult(stress, modal, rotordyn, wantCase);
  const inputs = mechanicalInputsFromResult(stress, st);
  put(inputs, 'case', results.case);
  return { inputs, results };
}

/* ── fixtures, shaped like the payloads the two routes actually return ────── */

const comp = (max, avg) => ({ max, avg: avg ?? max - 5 });

const FIELD = {
  T_min: 41.2, T_max: 163.4,
  components: {
    winding: comp(163.4, 151.0),
    magnet: comp(118.7, 114.2),
    stator: comp(140.1),
    rotor: comp(121.5),
    shaft: comp(96.4),
    sleeve: comp(119.9),
    liner: comp(158.2),
    enamel: comp(160.0),
    slot_fill: comp(155.3),
    gap_air: comp(130.2),
    pocket_air: comp(120.4),
  },
  P_cu_W: 812.5, P_fe_W: 190.2, P_loss_total_W: 1030.9,
  h_conv: 42,
  gap_k: 0.55,
  loss_source: { kind: 'reused', run_id: 'r1' },
  cooling: {
    outer: { mode: 'air', h_conv: 42, t_sink_c: 40, air_speed_mps: 10,
             heat_removed_W: 900 },
    inner: { mode: 'liquid', h_conv: 2100, t_in_c: 35, t_out_c: 44.1,
             flow_lpm: 4, fluid: 'water' },
    shaft_ends: { mode: 'rotating shaft in air', length_each_side_mm: 100,
                  diameter_mm: 40, sides: 2, h_conv: 61.4, re_omega: 2.4e5,
                  G_W_per_K: 0.82, t_sink_c: 40, t_shaft_mean_c: 96.1,
                  heat_removed_W: 46, fin_efficiency: 0.71 },
    gap: { k_eff: 0.58, k_air: 0.026, Ta: 2.1e5, Nu: 22 },
    sleeve: { present: true, k: 0.8, thickness_mm: 1 },
    heat_budget: { losses_W: 1030.9, housing_W: 900, bore_W: 96, gap_W: 34,
                   shaft_ends_W: 46 },
  },
};

const COUPLED = {
  coil_temp_history_C: [120, 149, 154], coil_temp_converged_C: 154.3,
  converged: true, runaway: false, iterations: 3, field: FIELD,
};

const PART = (over) => ({
  material: 'x', von_mises_max_mpa: 0, von_mises_p995_mpa: 0,
  principal_max_mpa: 0, principal_max_p995_mpa: 0, principal_min_mpa: 0,
  hoop_max_mpa: 0, radial_min_mpa: 0, radial_max_mpa: 0,
  strength_mpa: 100, strength_kind: 'yield', safety_factor: 1, mass_kg: 0.1,
  ...over,
});

const CASE = {
  rpm: 23000,
  parts: {
    sleeve: PART({ hoop_max_mpa: 812.4, radial_min_mpa: -18.6, safety_factor: 2.1 }),
    magnet: PART({ principal_max_p995_mpa: 22.5, principal_min_mpa: -64.2 }),
    rotor: PART({ von_mises_p995_mpa: 291.8 }),
    shaft: PART({ von_mises_p995_mpa: 55.1 }),
  },
  sf_min_per_part: {
    sleeve: { min: 1.72, p05: 1.94, criterion: 'tensile' },
    rotor: { min: 0.98, p05: 1.31, criterion: 'yield' },
  },
  sf_min: 0.98, sf_min_part: 'rotor', sf_min_p05: 1.31, sf_min_p05_part: 'rotor',
  interfaces: {
    sleeve_magnet: { open_fraction: 0.0234 },
    sleeve_rotor: { open_fraction: 0.41 },
    magnet_rotor: { open_fraction: 0.5 },
  },
  interface_open_frac: {},
  magnet_retention: { magnet_centrifugal_kn_per_m: 480, carried_kn_per_m: {},
                      share: {}, verdict: 'sleeve carries the magnets' },
  contact: { converged: true, iterations: 4, max_penetration_um: 0.2,
             n_frozen_pairs: 0, n_bodies: 4, unretained_parts: [] },
  rotor_od_growth_um: 38.7,
  torque_path: { verdict: 'held by friction at the pole tops', worst_pair: 'magnet_rotor',
                 slip_fraction: 0.02, stick_fraction: 0.98, capacity_nm: 410,
                 applied_nm: 183, margin: 2.24, slip_max_um: 1.1, held: true },
};

const STRESS = {
  rpm: 23000, case_mode: 'single', primary_case: '23,000 rpm',
  overspeed_factor: 1, overspeed_rpm: 23000, interference_mm: 0.05,
  has_sleeve: true, stack_length_mm: 60,
  mesh: { n_nodes: 1, n_triangles: 2, element_order: 2, mesh_size_mm: 1.5,
          rigid_residual: 0, n_contact_pairs: 3, n_contact_facets: 100 },
  contacts: {}, materials: {},
  cases: { '23,000 rpm': CASE },
  lift_off_rpm: {},
};

const MODAL = { modes: [{ index: 0, f_hz: 3120, order: 2 },
                        { index: 1, f_hz: 4880, order: 3 }] };
const ROTORDYN = { critical_speeds: [
  { rpm: 41200, hz: 686, whirl: 'forward', mode: 2, excited_by_unbalance: true,
    margin_vs_rated_pct: 79.1, beyond_plot: false },
  { rpm: 28400, hz: 473, whirl: 'backward', mode: 1, excited_by_unbalance: false,
    margin_vs_rated_pct: 23.5, beyond_plot: false },
] };

/* ═══ THERMAL ═══════════════════════════════════════════════════════════════ */

test('every component present becomes a <part>_max — all of them, by name', () => {
  const r = thermalRowFromResult(FIELD, null);
  for (const part of Object.keys(FIELD.components)) {
    assert.equal(r[`${part}_max`], FIELD.components[part].max,
      `${part}_max is missing or wrong`);
  }
  // …and nothing else claims to be a part temperature.
  const maxKeys = Object.keys(r).filter((k) => k.endsWith('_max')).sort();
  assert.deepEqual(maxKeys,
    Object.keys(FIELD.components).map((p) => `${p}_max`).concat('T_max').sort());
});

test('a component the backend adds LATER is carried without a code change', () => {
  // The whole reason the builder iterates instead of naming parts: five domains
  // appeared on 2026-09-07 alone, and a hard-coded list would drop the sixth.
  const f = { ...FIELD, components: { ...FIELD.components,
    end_winding: comp(171.0), impregnation: comp(149.9) } };
  const r = thermalRowFromResult(f, null);
  assert.equal(r.end_winding_max, 171.0);
  assert.equal(r.impregnation_max, 149.9);
});

test('a component with no elements is NOT a temperature', () => {
  // `null` means the mesh resolved nothing there. Storing it as 0 °C would
  // colour that row green as the coldest liner in the table.
  const f = { ...FIELD, components: { ...FIELD.components, liner: null,
                                      enamel: { max: null, avg: null } } };
  const r = thermalRowFromResult(f, null);
  assert.ok(!('liner_max' in r));
  assert.ok(!('enamel_max' in r));
  assert.equal(r.winding_max, 163.4);
});

test('the winding and magnet MEANS ride along with their peaks', () => {
  const r = thermalRowFromResult(FIELD, null);
  assert.equal(r.winding_avg, 151.0);
  assert.equal(r.magnet_avg, 114.2);
});

test('the two cooled surfaces are re-keyed, never recomputed', () => {
  const r = thermalRowFromResult(FIELD, null);
  assert.equal(r.h_outer, 42);
  assert.equal(r.sink_outer_c, 40);
  assert.equal(r.h_bore, 2100);
  assert.equal(r.t_out_bore_c, 44.1);
  assert.equal(r.housing_W, 900);
  assert.equal(r.bore_W, 96);
  assert.equal(r.gap_W, 34);
  assert.equal(r.losses_W, 1030.9);
  assert.equal(r.gap_k_eff, 0.58);          // the gap block wins over gap_k
  assert.equal(r.sleeve_k_radial, 0.8);
  assert.equal(r.loss_source_kind, 'reused');
});

test('the exposed shaft ends are a THIRD outflow, kept apart from the bore', () => {
  // The rotor's end faces and the end windings are inside the closed housing and
  // are deliberately not modelled; the shaft stubs are, and their watts must not
  // be folded into `bore_W` — the two are different design decisions (a pump
  // through the shaft vs. how much of it sticks out) and a comparison table that
  // added them up could not tell two variants apart.
  const r = thermalRowFromResult(FIELD, null);
  assert.equal(r.shaft_ends_W, 46);
  assert.equal(r.shaft_ends_G, 0.82);
  assert.equal(r.h_shaft_ends, 61.4);
  assert.equal(r.bore_W, 96);
});

test('a machine with nothing sticking out has no shaft-ends columns at all', () => {
  // `mode: 'off'` is a statement, and a 0 W column that is always 0 would take
  // part in the best/worst colouring as if it were a measurement.
  const f = { ...FIELD, cooling: { ...FIELD.cooling,
    shaft_ends: { mode: 'off', G_W_per_K: 0, heat_removed_W: 0 },
    heat_budget: { ...FIELD.cooling.heat_budget, shaft_ends_W: 0 } } };
  const r = thermalRowFromResult(f, null);
  // The watts ARE written (0 is a measured outflow of a solved machine); the
  // INPUT columns are what disappear — see the inputs test below.
  assert.equal(r.shaft_ends_W, 0);
  assert.equal(r.shaft_ends_G, 0);
});

test('a payload from before the two-surface split still reports its outer film', () => {
  // Flat `cooling`, as cached before 2026-09-07 — outerCooling folds it.
  const f = { ...FIELD, cooling: { mode: 'liquid', h_conv: 1800, t_sink_c: 45,
                                   fluid_temp_in_c: 40, fluid_temp_out_c: 49 } };
  const r = thermalRowFromResult(f, null);
  assert.equal(r.h_outer, 1800);
  assert.equal(r.sink_outer_c, 45);
  assert.equal(r.t_out_outer_c, 49);
  assert.ok(!('h_bore' in r));
});

test('the coupled coil temperature rides only when a coupled result was passed', () => {
  const bare = thermalRowFromResult(FIELD, null);
  assert.ok(!('coupled_coil_c' in bare));
  assert.ok(!('coupled_converged' in bare));
  const both = thermalRowFromResult(FIELD, COUPLED);
  assert.equal(both.coupled_coil_c, 154.3);
  assert.equal(both.coupled_converged, true);
});

test('a runaway loop is not a converged one, whatever its flag says', () => {
  const r = thermalRowFromResult(FIELD,
    { ...COUPLED, converged: true, runaway: true });
  assert.equal(r.coupled_converged, false);
});

test('no thermal result throws a sentence, not a row of dashes', () => {
  assert.throws(() => thermalRowFromResult(null, null), /no thermal result/);
  assert.throws(() => thermalRowFromResult(undefined), /Thermal tab/);
});

test('a mesh with no field throws rather than storing an empty row', () => {
  assert.throws(() => thermalRowFromResult({ vertices: [], triangles: [] }, null),
    /carries no temperatures/);
  assert.throws(() => thermalRowFromResult({ components: { winding: null } }),
    /carries no temperatures/);
});

test('the part labels are the engineering names, not the domain tags', () => {
  assert.equal(partMaxLabel('gap_air_max'), 'Air gap');
  assert.equal(partMaxLabel('pocket_air_max'), 'Pocket air');
  assert.equal(partMaxLabel('slot_fill_max'), 'Slot fill');
  assert.equal(partMaxLabel('sleeve_max'), 'Sleeve');
  assert.equal(partMaxLabel('end_winding_max'), 'End winding');
});

/* ═══ MECHANICAL ════════════════════════════════════════════════════════════ */

test('a single-speed result reports its one case, named by its speed', () => {
  const r = mechanicalRowFromResult(STRESS, null, null);
  assert.equal(r.case, '23,000 rpm');
  assert.equal(r.rpm, 23000);
  assert.equal(r.rpm_overspeed, 23000);
});

test('the viewed case wins, and an impossible one falls back instead of blanking', () => {
  const three = { ...STRESS, primary_case: 'rated', case_mode: 'three',
    cases: { standstill: { ...CASE, rpm: 0 }, rated: CASE,
             overspeed: { ...CASE, rpm: 27600 } } };
  assert.equal(mechanicalRowFromResult(three, null, null, 'overspeed').case, 'overspeed');
  assert.equal(mechanicalRowFromResult(three, null, null, 'overspeed').rpm, 27600);
  // A remembered "rated" against a single-speed answer must not blank the row.
  assert.equal(mechanicalRowFromResult(STRESS, null, null, 'rated').case, '23,000 rpm');
  // No choice at all → the result's own primary case.
  assert.equal(mechanicalRowFromResult(three, null, null).case, 'rated');
});

test('the stresses are the ones the panel quotes', () => {
  const r = mechanicalRowFromResult(STRESS, null, null);
  assert.equal(r.sleeve_hoop_p995_mpa, 812.4);
  assert.equal(r.sleeve_rad_min_mpa, -18.6);
  assert.equal(r.magnet_s1_p995_mpa, 22.5);
  assert.equal(r.magnet_s2_min_mpa, -64.2);
  assert.equal(r.iron_vm_p995_mpa, 291.8);
  assert.equal(r.shaft_vm_p995_mpa, 55.1);
  assert.equal(r.od_growth_um, 38.7);
});

test('the safety factor quoted is p05, not the raw element minimum', () => {
  // p05 is the field with the singular corner elements removed — the number the
  // tab prints; the raw min at a bridge root is a mesh artefact.
  const r = mechanicalRowFromResult(STRESS, null, null);
  assert.equal(r.sf_sleeve, 1.94);
  assert.equal(r.sf_iron, 1.31);
  // With no per-part block the part's own factor is the last resort.
  const bare = { ...STRESS, cases: { '23,000 rpm': { ...CASE, sf_min_per_part: {} } } };
  assert.equal(mechanicalRowFromResult(bare, null, null).sf_sleeve, 2.1);
});

test('an open fraction becomes a percentage exactly once', () => {
  const r = mechanicalRowFromResult(STRESS, null, null);
  assert.ok(Math.abs(r.open_sleeve_magnet - 2.34) < 1e-9);
  assert.ok(Math.abs(r.open_sleeve_rotor - 41) < 1e-9);
  assert.ok(Math.abs(r.open_magnet_rotor - 50) < 1e-9);
});

test('the verdicts ride as text, and "not held" is stored as false', () => {
  const r = mechanicalRowFromResult(STRESS, null, null);
  assert.equal(r.retention_verdict, 'sleeve carries the magnets');
  assert.equal(r.torque_path_verdict, 'held by friction at the pole tops');
  assert.equal(r.poles_held, true);

  const notHeld = { ...STRESS, cases: { '23,000 rpm':
    { ...CASE, torque_path: { ...CASE.torque_path, held: false } } } };
  assert.equal(mechanicalRowFromResult(notHeld, null, null).poles_held, false);

  // "no verdict" (the solve ran away) is NOT a boolean and must not become one.
  const noVerdict = { ...STRESS, cases: { '23,000 rpm':
    { ...CASE, torque_path: { ...CASE.torque_path, held: null } } } };
  assert.ok(!('poles_held' in mechanicalRowFromResult(noVerdict, null, null)));
});

test('a machine with no sleeve simply has no sleeve columns', () => {
  const bare = { ...STRESS, cases: { '23,000 rpm': { ...CASE,
    parts: { magnet: CASE.parts.magnet, rotor: CASE.parts.rotor },
    sf_min_per_part: { rotor: CASE.sf_min_per_part.rotor },
    interfaces: { magnet_rotor: { open_fraction: 0.1 } } } } };
  const r = mechanicalRowFromResult(bare, null, null);
  assert.ok(!('sleeve_hoop_p995_mpa' in r));
  assert.ok(!('sf_sleeve' in r));
  assert.ok(!('open_sleeve_magnet' in r));
  assert.equal(r.iron_vm_p995_mpa, 291.8);
});

test('the modal and rotordynamic answers ride only when they were passed', () => {
  const alone = mechanicalRowFromResult(STRESS, null, null);
  assert.ok(!('mode1_hz' in alone));
  assert.ok(!('critical1_rpm' in alone));
  const full = mechanicalRowFromResult(STRESS, MODAL, ROTORDYN);
  assert.equal(full.mode1_hz, 3120);
  assert.equal(full.mode2_hz, 4880);
});

test('the critical speed quoted is the LOWEST one, whatever order it came in', () => {
  // It is the one that decides whether the rated point is reachable at all.
  const r = mechanicalRowFromResult(STRESS, MODAL, ROTORDYN);
  assert.equal(r.critical1_rpm, 28400);
  assert.equal(r.critical_margin_pct, 23.5);
});

test('no mechanical result, or one with no case, throws a sentence', () => {
  assert.throws(() => mechanicalRowFromResult(null), /no mechanical result/);
  assert.throws(() => mechanicalRowFromResult({ cases: {} }), /no load case/);
});

/* ═══ THE TAB'S OWN STACK (local comparison rows) ═══════════════════════════
 *
 * The LOCAL table's columns read these keys, and its "same for all" line is the
 * difference between a readable table and twenty-two columns of "40 °C".  Three
 * properties matter: the results half is the SAME block the Compare library
 * gets (one meaning per key, everywhere), the inputs are read off the RESULT
 * wherever it echoes them (a jacket selected but not solved must not head a row
 * of still-air temperatures), and a field belonging to a mode that is not
 * selected is never written.
 */

const THERM_ST = {
  coolMode: 'air', ambientT: '40', airSpeed: '10', fluid: 'water',
  tIn: '35', hConv: '50', flowLpm: '8',
  boreMode: 'none', boreAirSpeed: '10', boreFluid: 'water',
  boreTIn: '35', boreFlowLpm: '4',
  shaftExtMm: '0', shaftExtSides: '2',
};
const OP = { I_phase_rms: 120, gamma_deg: 12.5, rpm: 20000, coil_temp_c: 130 };

const MECH_ST = {
  cases: 'single', loads: 'both', torque: '180', rpm: '20000', rpm1: '23000',
  osf: '1.2', interf: '0.05', rotorTempC: '150', sleeveTempC: '150',
  meshMm: '1.5',
  contacts: {
    sleeve_magnet: { type: 'separation', mu: 0.2 },
    sleeve_rotor: { type: 'separation', mu: 0.2 },
    magnet_rotor: { type: 'separation', mu: 0.2 },
    shaft_rotor: { type: 'bonded', mu: 0 },
  },
};

test('a local row carries EXACTLY the Compare library\'s results block', () => {
  // One meaning per key across the app: the tab's table and the permanent
  // library must never disagree about what `sleeve_hoop_p995_mpa` is.
  const local = localThermalRow(FIELD, COUPLED, THERM_ST, OP);
  assert.deepEqual(local.results, thermalRowFromResult(FIELD, COUPLED));
  const mech = localMechanicalRow(STRESS, MODAL, ROTORDYN, MECH_ST);
  assert.deepEqual(mech.results,
    mechanicalRowFromResult(STRESS, MODAL, ROTORDYN, undefined));
});

test('the cooling inputs are the SOLVED ones, not the fields on screen', () => {
  // The payload says air at 10 m/s; the panel has since been switched to a
  // water jacket and not re-solved. The row must read as what was solved.
  const st = { ...THERM_ST, coolMode: 'liquid', airSpeed: '30' };
  const r = localThermalRow(FIELD, null, st, OP).inputs;
  assert.equal(r.cool_mode, 'air');
  assert.equal(r.air_speed_mps, 10);
  assert.ok(!('fluid' in r));
  assert.ok(!('flow_lpm' in r));
});

test('only the selected mode\'s own fields become inputs', () => {
  const liquid = { ...FIELD, cooling: { ...FIELD.cooling,
    outer: { mode: 'liquid', h_conv: 2400, t_in_c: 35, flow_lpm: 12,
             fluid: 'water_glycol_50' } } };
  const r = localThermalRow(liquid, null, THERM_ST, OP).inputs;
  assert.equal(r.fluid, 'water_glycol_50');
  assert.equal(r.fluid_in_c, 35);
  assert.equal(r.flow_lpm, 12);
  // A liquid jacket's h is a RESULT (results.h_outer) — never an input.
  assert.ok(!('h_manual' in r));
  assert.ok(!('air_speed_mps' in r));
  // …and the ambient decides nothing here: no air is blown anywhere.
  assert.ok(!('ambient_c' in r));

  const manual = { ...FIELD, cooling: { outer: { mode: 'manual', h_conv: 75 } } };
  assert.equal(localThermalRow(manual, null, THERM_ST, OP).inputs.h_manual, 75);
});

test('the bore is its own boundary, written only when it flows', () => {
  const bare = localThermalRow({ ...FIELD, cooling: { outer: { mode: 'air', air_speed_mps: 5 } } },
    null, THERM_ST, OP).inputs;
  assert.equal(bare.bore_mode, 'none');
  assert.ok(!('bore_flow_lpm' in bare));
  // FIELD's own bore is a 4 L/min water loop.
  const r = localThermalRow(FIELD, null, THERM_ST, OP).inputs;
  assert.equal(r.bore_mode, 'liquid');
  assert.equal(r.bore_fluid, 'water');
  assert.equal(r.bore_in_c, 35);
  assert.equal(r.bore_flow_lpm, 4);
});

test('the exposed shaft is an input only when something sticks out', () => {
  // FIELD carries 2 × 100 mm; a machine with nothing outside the housing must
  // not grow two columns that read the same in every row.
  const r = localThermalRow(FIELD, null, THERM_ST, OP).inputs;
  assert.equal(r.shaft_out_mm, 100);
  assert.equal(r.shaft_sides, 2);

  const none = { ...FIELD, cooling: { ...FIELD.cooling,
    shaft_ends: { mode: 'off', length_each_side_mm: 0 } } };
  const bare = localThermalRow(none, null, THERM_ST, OP).inputs;
  assert.ok(!('shaft_out_mm' in bare));
  assert.ok(!('shaft_sides' in bare));
});

test('a payload with no shaft block falls back to the panel\'s own field', () => {
  // Same rule as every other input here: the SOLVED value wins, and the field
  // on screen is what fills in for a payload cached before the block existed.
  const old = { ...FIELD, cooling: { ...FIELD.cooling, shaft_ends: undefined } };
  const st = { ...THERM_ST, shaftExtMm: '75', shaftExtSides: '1' };
  const r = localThermalRow(old, null, st, OP).inputs;
  assert.equal(r.shaft_out_mm, 75);
  assert.equal(r.shaft_sides, 1);
});

test('the panel\'s field is the fallback for what the payload does not echo', () => {
  const noEcho = { ...FIELD, cooling: undefined, ambient_temp: undefined };
  const r = localThermalRow(noEcho, null, THERM_ST, OP).inputs;
  assert.equal(r.cool_mode, 'air');
  assert.equal(r.air_speed_mps, 10);   // from st.airSpeed
  assert.equal(r.ambient_c, 40);       // from st.ambientT
});

test('the operating point rides as an INPUT of the thermal row', () => {
  // Two cooling designs compared at two different currents are not a
  // comparison of cooling designs.
  const r = localThermalRow(FIELD, null, THERM_ST, OP).inputs;
  assert.equal(r.I_A, 120);
  assert.equal(r.gamma_deg, 12.5);
  assert.equal(r.rpm, 20000);
  assert.equal(r.coil_c, 130);
  // No operating point at all → no columns, never zeros.
  const none = localThermalRow(FIELD, null, THERM_ST, null).inputs;
  assert.ok(!('I_A' in none));
  assert.ok(!('coil_c' in none));
});

test('the slot conductivities ride with the card each came from', () => {
  const f = { ...FIELD, materials_used: {
    liner: { k: 0.14, material: 'Nomex 410', source: 'library' },
    enamel: { k: 0.21, material: 'polyimide', source: 'library' },
    slot_fill: { k: 0.32, material: null, source: 'default' },
  } };
  const r = localThermalRow(f, null, THERM_ST, OP).inputs;
  assert.equal(r.liner_mat, 'Nomex 410');
  assert.equal(r.liner_k, 0.14);
  assert.equal(r.enamel_mat, 'polyimide');
  assert.equal(r.enamel_k, 0.21);
  assert.equal(r.fill_k, 0.32);
  assert.ok(!('fill_mat' in r));       // `null` is not a material name
});

test('a thermal row with nothing solved throws before anything is built', () => {
  assert.throws(() => localThermalRow(null, null, THERM_ST, OP), /no thermal result/);
});

test('the mechanical inputs are read off the result, the fields as fallback', () => {
  const r = localMechanicalRow(STRESS, null, null, MECH_ST).inputs;
  assert.equal(r.case_mode, 'single');
  assert.equal(r.rpm, 23000);            // the result's own speed
  assert.equal(r.rpm_overspeed, 23000);
  assert.equal(r.interference_mm, 0.05);
  assert.equal(r.mesh_mm, 1.5);
  // Not echoed by this payload → the panel's fields.
  assert.equal(r.loads, 'both');
  assert.equal(r.torque_nm, 180);
  assert.equal(r.rotor_c, 150);
  assert.equal(r.sleeve_c, 150);
  // …and what the result DOES echo wins over them.
  const solved = { ...STRESS, loads: 'centrifugal', torque_nm: 0,
    interference_effective_mm: 0.071,
    thermal: { rotor_temp_c: 120, sleeve_temp_c: 90, ref_temp_c: 20,
               active: true, parts: {}, fit: null, notes: [] },
    materials: { sleeve: { material: 'CFRP T700' } } };
  const s = localMechanicalRow(solved, null, null, MECH_ST).inputs;
  assert.equal(s.loads, 'centrifugal');
  assert.equal(s.rotor_c, 120);
  assert.equal(s.sleeve_c, 90);
  assert.equal(s.interference_eff_mm, 0.071);
  assert.equal(s.sleeve_mat, 'CFRP T700');
  // A solved torque of 0 N·m is a MEASUREMENT ("no torque acted"), so it must
  // not fall through to the 180 still sitting in the field.
  assert.equal(s.torque_nm, 0);
});

test('the four joints are ONE input column, in the panel\'s order', () => {
  const r = localMechanicalRow(STRESS, null, null, MECH_ST).inputs;
  assert.equal(r.contacts,
    'sleeve_magnet:separation µ0.2 · sleeve_rotor:separation µ0.2 · '
    + 'magnet_rotor:separation µ0.2 · shaft_rotor:bonded');
  // The µ of a bonded pair is not part of the model and is not printed.
  assert.ok(!/shaft_rotor:bonded µ/.test(String(r.contacts)));
});

test('which case the row is ABOUT is copied from the builder, not re-decided', () => {
  const three = { ...STRESS, primary_case: 'rated', case_mode: 'three',
    cases: { standstill: { ...CASE, rpm: 0 }, rated: CASE,
             overspeed: { ...CASE, rpm: 27600 } } };
  const r = localMechanicalRow(three, null, null, MECH_ST, 'overspeed');
  assert.equal(r.inputs.case, 'overspeed');
  assert.equal(r.results.case, 'overspeed');
});

/* ── what comes back from the store / the server ─────────────────────────── */

test('stored rows are adopted only when they ARE rows', () => {
  const good = { id: 'a1', name: 'air 10 m/s', at: '2026-09-07T10:00:00Z',
                 inputs: { cool_mode: 'air' }, results: { T_max: 163.4 } };
  assert.deepEqual(normalizeLocalRows([good]), [good]);
  // Not an array, not objects, no id, no content — all dropped, never repaired.
  assert.deepEqual(normalizeLocalRows(null), []);
  assert.deepEqual(normalizeLocalRows('rows'), []);
  assert.deepEqual(normalizeLocalRows([1, null, [], { name: 'no id' },
                                       { id: 'x', inputs: {}, results: {} }]), []);
});

test('a stored block keeps its scalars and drops everything else', () => {
  // What comes back is JSON another browser wrote: a nested object where a
  // number belongs would take the table\'s formatter down with it.
  const r = normalizeLocalRows([{ id: 'a1', inputs: { a: 1, b: 'x', c: true,
    d: { deep: 1 }, e: [1, 2], f: null, g: NaN }, results: { T_max: 100 } }])[0];
  assert.deepEqual(r.inputs, { a: 1, b: 'x', c: true });
  assert.equal(r.name, 'a1');   // a nameless row is named by its id
  assert.equal(r.at, '');
});

test('the stack is capped, keeping the NEWEST variants', () => {
  const many = Array.from({ length: MAX_LOCAL_ROWS + 4 }, (_, i) => ({
    id: `r${i}`, name: `#${i}`, at: '', inputs: { n: i }, results: { T_max: i },
  }));
  const kept = normalizeLocalRows(many);
  assert.equal(kept.length, MAX_LOCAL_ROWS);
  assert.equal(kept[0].id, 'r4');
  assert.equal(kept[kept.length - 1].id, `r${MAX_LOCAL_ROWS + 3}`);
});
