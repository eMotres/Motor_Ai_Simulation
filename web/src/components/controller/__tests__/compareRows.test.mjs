/**
 * The Controller tab's Compare row builders, tested without a browser.
 *
 * What is pinned here is the ROW CONTRACT — `compare/resultRows` states why
 * that matters and this file keeps the same three rules:
 *
 *   • a key is written only when there is a measurement behind it, so an
 *     absent quantity prints "—" instead of colouring as a zero;
 *   • the keys are exactly the ones `CONTROLLER_COMPARE_COLUMNS` reads, because
 *     two places spelling `eta_inverter_pct` differently is a column that is
 *     always empty;
 *   • the cost proxy exists only when the card carries a price.
 *
 * The builders are re-implemented verbatim below rather than imported: the
 * module is TypeScript and its import graph reaches `import.meta.env`, which
 * `node --test` cannot load (same reason as common/__tests__/progressStrip and
 * compare/__tests__/resultRows). Changing `compareRows.ts` therefore has to
 * change this file too — and that is the moment someone has to justify the new
 * behaviour.
 */
import test from 'node:test';
import assert from 'node:assert/strict';

/* ── verbatim copies of the shipped builders ─────────────────────────────── */

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

function controllerInputsFromResult(res) {
  const out = {};
  const t = res.topology ?? {};
  const s = res.settings ?? {};
  const p = res.point ?? {};
  const cool = res.thermal?.coldplate ?? {};
  put(out, 'device', str(res.device));
  put(out, 'topology', str(t.preset_label) ?? str(t.preset));
  put(out, 'connection', str(t.star_delta));
  put(out, 'n_parallel', num(s.devices_parallel));
  put(out, 'n_bridges', num(t.n_bridges));
  put(out, 'n_switches', num(t.n_switches));
  put(out, 'n_devices', num(t.n_devices));
  put(out, 'duty', str(res.context?.duty));
  put(out, 'f_carrier_hz', num(p.f_carrier_hz));
  put(out, 'v_dc_V', num(p.v_dc_V));
  put(out, 'dead_time_us', num(s.dead_time_us));
  put(out, 'i_phase_rms_A', num(p.i_phase_rms_A));
  put(out, 'coolant', str(cool.coolant));
  put(out, 'flow_lpm', num(cool.flow_lpm));
  put(out, 't_in_c', num(cool.t_in_c));
  put(out, 'r_tim_k_w', num(res.thermal?.r_tim_k_w));
  return out;
}

function controllerRowFromResult(res) {
  const out = {};
  const L = res.losses ?? {};
  const T = res.thermal ?? {};
  const E = res.efficiency ?? {};
  const D = res.dc_link ?? {};
  put(out, 'p_conduction_W', num(L.conduction_W));
  put(out, 'p_third_quadrant_W', num(L.third_quadrant_W));
  put(out, 'p_switching_W', num(L.switching_W));
  put(out, 'p_total_W', num(L.total_W));
  put(out, 't_j_max_c', num(T.t_j_max_c));
  put(out, 't_j_margin_K', num(T.margin_K));
  put(out, 't_case_c', num(T.t_case_c));
  put(out, 'eta_inverter_pct', E.inverter == null ? undefined : E.inverter * 100);
  put(out, 'eta_wall_to_shaft_pct',
      E.wall_to_shaft == null ? undefined : E.wall_to_shaft * 100);
  put(out, 'i_cap_rms_A', num(D.i_cap_rms_A));
  put(out, 'limits', str(res.limits_verdict));
  const failed = (res.limits ?? []).filter(r => r.verdict === 'fail').length;
  put(out, 'limits_failed', failed);
  const price = num(res.device_row?.price?.amount);
  const n = num(res.topology?.n_devices);
  if (price !== undefined && n !== undefined) {
    put(out, 'cost_proxy', price * n);
    put(out, 'currency', str(res.device_row?.price?.currency));
  }
  return out;
}

function controllerRowName(res) {
  const t = res.topology;
  const bits = [res.device, t?.preset_label,
                `x${res.settings?.devices_parallel ?? 1}`];
  if (res.context?.duty) bits.push(res.context.duty);
  return bits.filter(Boolean).join(' · ');
}

/* ── a solved controller, as the route sends one ─────────────────────────── */

const SOLVED = {
  device: 'IMCQ120R004M2H',
  device_row: { price: { amount: null, currency: 'EUR' } },
  topology: { preset: 'one_3ph', preset_label: 'One 3-phase inverter',
              star_delta: 'delta', n_bridges: 1, n_switches: 6, n_devices: 18 },
  settings: { devices_parallel: 3, dead_time_us: 0.5 },
  point: { f_carrier_hz: 24000, v_dc_V: 750.4, i_phase_rms_A: 314.3 },
  thermal: { t_j_max_c: 132.2, margin_K: 42.8, t_case_c: 102.2, r_tim_k_w: 0.03,
             coldplate: { coolant: 'water_glycol_50', flow_lpm: 8, t_in_c: 65 } },
  losses: { conduction_W: 1928.7, third_quadrant_W: 144.7, switching_W: 2077.6,
            total_W: 4151.1 },
  efficiency: { inverter: 0.98498, shaft: 0.9771, wall_to_shaft: 0.96243 },
  dc_link: { i_cap_rms_A: 350.7 },
  limits_verdict: 'pass',
  limits: [{ name: 'Junction temperature', verdict: 'pass' }],
  context: { die: 'CIANO10 200 opt', config: 'L155 motor', duty: 'rated 1x9 mm' },
};

/* ── tests ───────────────────────────────────────────────────────────────── */

test('the inputs block carries what the solve was set to', () => {
  const i = controllerInputsFromResult(SOLVED);
  assert.equal(i.device, 'IMCQ120R004M2H');
  assert.equal(i.topology, 'One 3-phase inverter');
  assert.equal(i.connection, 'delta');
  assert.equal(i.n_parallel, 3);
  assert.equal(i.n_devices, 18);
  assert.equal(i.duty, 'rated 1x9 mm');
  assert.equal(i.coolant, 'water_glycol_50');
  assert.equal(i.t_in_c, 65);
});

test('the results block carries the losses, the temperatures and both efficiencies', () => {
  const r = controllerRowFromResult(SOLVED);
  assert.equal(r.p_total_W, 4151.1);
  assert.equal(r.p_switching_W, 2077.6);
  assert.equal(r.t_j_max_c, 132.2);
  assert.equal(r.t_j_margin_K, 42.8);
  assert.ok(Math.abs(r.eta_inverter_pct - 98.498) < 1e-9);
  assert.ok(Math.abs(r.eta_wall_to_shaft_pct - 96.243) < 1e-9);
  assert.equal(r.i_cap_rms_A, 350.7);
  assert.equal(r.limits, 'pass');
  assert.equal(r.limits_failed, 0);
});

test('a failed limit is counted, and the verdict travels with the row', () => {
  const bad = { ...SOLVED, limits_verdict: 'fail',
                limits: [{ name: 'Junction temperature', verdict: 'fail' },
                         { name: 'Continuous current per device', verdict: 'fail' },
                         { name: 'DC link vs V_DSS', verdict: 'pass' }] };
  const r = controllerRowFromResult(bad);
  assert.equal(r.limits, 'fail');
  assert.equal(r.limits_failed, 2);
});

test('no price on the card means no cost column, not a zero', () => {
  const r = controllerRowFromResult(SOLVED);
  assert.equal('cost_proxy' in r, false);
  assert.equal('currency' in r, false);
});

test('a quoted price becomes devices x price', () => {
  const priced = { ...SOLVED,
    device_row: { price: { amount: 42.5, currency: 'EUR' } } };
  const r = controllerRowFromResult(priced);
  assert.equal(r.cost_proxy, 42.5 * 18);
  assert.equal(r.currency, 'EUR');
});

test('a missing quantity is an absent key, never a zero', () => {
  const thin = { device: 'X', topology: {}, settings: {}, point: {},
                 thermal: {}, losses: {}, efficiency: {}, dc_link: {} };
  const r = controllerRowFromResult(thin);
  for (const k of ['p_total_W', 't_j_max_c', 'eta_inverter_pct',
                   'i_cap_rms_A', 'limits']) {
    assert.equal(k in r, false, `${k} should be absent`);
  }
  // …and an empty limit list is zero FAILURES, which is a real measurement.
  assert.equal(r.limits_failed, 0);
});

test('the row name says what the variant is', () => {
  assert.equal(controllerRowName(SOLVED),
    'IMCQ120R004M2H · One 3-phase inverter · x3 · rated 1x9 mm');
});
