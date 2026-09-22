/**
 * The Controller tab's pure helpers, tested without a browser.
 *
 * `polyline` is what draws the waveform card, and a chart that silently
 * collapses is worse than no chart: what is pinned here is that a flat trace
 * still draws (it must not divide by a zero span), that the polyline spans the
 * full width, and that the y axis is INVERTED the way SVG needs — the largest
 * value nearest the top. `fmt`/`pct` are pinned on the one thing a client-
 * facing table must never do: print a made-up number where there is none.
 *
 * The helpers are re-implemented verbatim below rather than imported: the
 * module is TypeScript and reads `import.meta.env`, which `node --test` cannot
 * load (the same reason as common/__tests__/progressStrip.test.mjs). Changing
 * `controllerApi.ts` therefore has to change this file too — and that is the
 * moment someone has to justify the new behaviour.
 */
import test from 'node:test';
import assert from 'node:assert/strict';

/* ── verbatim copies of the shipped helpers ──────────────────────────────── */

function polyline(values, w, h, pad = 2) {
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

const fmt = (v, digits = 1, dash = '—') =>
  (v === null || v === undefined || Number.isNaN(Number(v)))
    ? dash : Number(v).toLocaleString(undefined, { maximumFractionDigits: digits,
                                                   minimumFractionDigits: digits });

const pct = (v, digits = 2) =>
  (v === null || v === undefined) ? '—' : `${(Number(v) * 100).toFixed(digits)} %`;

function statusLine(res, err) {
  if (err) return err;
  return res?.solved_for || null;
}

/* ── polyline ────────────────────────────────────────────────────────────── */

test('polyline spans the full width and inverts y for SVG', () => {
  const pts = polyline([0, 1], 600, 100).split(' ').map(p => p.split(',').map(Number));
  assert.equal(pts.length, 2);
  assert.equal(pts[0][0], 0);
  assert.equal(pts[1][0], 600);
  // 0 is the smallest value, so it sits at the BOTTOM (largest y).
  assert.ok(pts[0][1] > pts[1][1]);
});

test('polyline keeps the trace inside the padded box', () => {
  const pts = polyline([-5, 0, 5], 100, 50, 2).split(' ').map(p => p.split(',').map(Number));
  for (const [, y] of pts) {
    assert.ok(y >= 2 - 1e-9 && y <= 48 + 1e-9, `y ${y} escaped the box`);
  }
});

test('a flat trace still draws — no division by a zero span', () => {
  const s = polyline([7, 7, 7], 300, 60);
  assert.ok(s.length > 0);
  assert.ok(!s.includes('NaN'));
});

test('an empty or non-finite series draws nothing rather than NaN', () => {
  assert.equal(polyline([], 300, 60), '');
  assert.equal(polyline([NaN, NaN], 300, 60), '');
});

test('one sample does not divide by zero', () => {
  const s = polyline([3], 300, 60);
  assert.ok(s.startsWith('0.0,'));
  assert.ok(!s.includes('NaN'));
});

/* ── fmt / pct ───────────────────────────────────────────────────────────── */

test('a missing number prints an em dash, never a zero', () => {
  assert.equal(fmt(null), '—');
  assert.equal(fmt(undefined), '—');
  assert.equal(fmt('not a number'), '—');
  assert.equal(pct(null), '—');
  assert.equal(pct(undefined), '—');
});

test('fmt keeps the digits it is asked for', () => {
  assert.equal(fmt(0, 0), '0');
  assert.equal(fmt(2625.6, 0), '2,626');
  assert.equal(fmt(0.07, 3), '0.070');
});

test('pct is a fraction turned into per cent', () => {
  assert.equal(pct(0.99045), '99.05 %');
  assert.equal(pct(1), '100.00 %');
  assert.equal(pct(0.9771, 3), '97.710 %');
});

/* ── settings persistence (owner 2026-09-22) ────────────────────────────────
 * "при сохранении мотора текущий контроллер тоже должен сохраняться со всеми
 * настройками" — the tab's own FORM, saved WITH the configuration, restored
 * when the tab reads it back. Verbatim copies of `formStateFromSettings` /
 * `settingsForSave`, same reason as the helpers above (TS + import.meta.env).
 */

const toFormNumber = (v) => (v === null || v === undefined) ? '' : v;
const toSaveNumber = (v) => (v === '' ? null : v);

function formStateFromSettings(block, fallback) {
  if (!block || Object.keys(block).length === 0) return fallback;
  const cooling = block.cooling || {};
  const rows = block.mapping || [];
  const mapping = {};
  for (const m of rows) mapping[m.coil] = `${m.bridge}/${m.leg}`;
  const parByBridge = block.devices_parallel_by_bridge || {};
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
    mapping: rows.length ? mapping : fallback.mapping,
    parByBridge: Object.keys(parByBridge).length ? parByBridge : fallback.parByBridge,
    coupleWithEm: block.couple_with_em ?? fallback.coupleWithEm,
  };
}

function settingsForSave(s) {
  const mapping = Object.entries(s.mapping).map(([coil, v]) => {
    const [bridge, leg] = String(v).split('/');
    return { coil: Number(coil), bridge: bridge || 'INV1', leg: leg || 'A' };
  });
  return {
    device: s.device || null,
    topology: s.topology,
    set_split: s.setSplit,
    h_bridge_modulation: s.hbMod,
    devices_parallel: s.nPar === '' ? 1 : s.nPar,
    devices_parallel_by_bridge: s.parByBridge,
    r_g_ext_ohm: toSaveNumber(s.rg),
    v_gs_off_V: toSaveNumber(s.vgsOff),
    dead_time_us: toSaveNumber(s.dead),
    f_carrier_hz: toSaveNumber(s.fsw),
    v_dc_V: toSaveNumber(s.vdc),
    cooling: { coolant: s.coolant, flow_lpm: toSaveNumber(s.flow),
              t_in_c: toSaveNumber(s.tin), r_tim_k_w: toSaveNumber(s.rtim) },
    mapping,
    couple_with_em: s.coupleWithEm,
  };
}

const DEFAULT_FORM = {
  device: '', topology: 'one_3ph', setSplit: 'series_split', hbMod: 'unipolar',
  nPar: 4, rg: 2.3, vgsOff: 0, dead: 0.5, fsw: '', vdc: '',
  coolant: 'water_glycol_50', flow: 8, tin: 65, rtim: 0.03,
  mapping: {}, parByBridge: {}, coupleWithEm: false,
};

test('an empty saved block leaves the tab at its own defaults', () => {
  assert.deepEqual(formStateFromSettings(null, DEFAULT_FORM), DEFAULT_FORM);
  assert.deepEqual(formStateFromSettings({}, DEFAULT_FORM), DEFAULT_FORM);
  assert.deepEqual(formStateFromSettings(undefined, DEFAULT_FORM), DEFAULT_FORM);
});

test('a saved block restores the panel state field by field', () => {
  const block = {
    device: 'IMCQ120R004M2H', topology: 'two_3ph', set_split: 'power_split',
    h_bridge_modulation: 'bipolar', devices_parallel: 3,
    devices_parallel_by_bridge: { INV2: 6 }, r_g_ext_ohm: 4.7, v_gs_off_V: -5,
    dead_time_us: 1.0, f_carrier_hz: 24000, v_dc_V: 750.4,
    cooling: { coolant: 'oil', flow_lpm: 10, t_in_c: 55, r_tim_k_w: 0.02 },
    mapping: [{ coil: 1, bridge: 'INV1', leg: 'A' }, { coil: 2, bridge: 'INV2', leg: 'B' }],
    couple_with_em: true,
  };
  const s = formStateFromSettings(block, DEFAULT_FORM);
  assert.equal(s.device, 'IMCQ120R004M2H');
  assert.equal(s.topology, 'two_3ph');
  assert.equal(s.setSplit, 'power_split');
  assert.equal(s.nPar, 3);
  assert.deepEqual(s.parByBridge, { INV2: 6 });
  assert.equal(s.rg, 4.7);
  assert.equal(s.fsw, 24000);
  assert.equal(s.vdc, 750.4);
  assert.equal(s.coolant, 'oil');
  assert.equal(s.flow, 10);
  assert.deepEqual(s.mapping, { 1: 'INV1/A', 2: 'INV2/B' });
  assert.equal(s.coupleWithEm, true);
});

test('a blank saved carrier/DC link restores as blank — "the duty\'s own"', () => {
  const block = { device: 'X', topology: 'one_3ph', devices_parallel: 1,
    f_carrier_hz: null, v_dc_V: null };
  const s = formStateFromSettings(block, DEFAULT_FORM);
  assert.equal(s.fsw, '');
  assert.equal(s.vdc, '');
});

test('settingsForSave round-trips through formStateFromSettings', () => {
  const edited = { ...DEFAULT_FORM, device: 'IMCQ120R004M2H', nPar: 3,
    fsw: 24000, vdc: '', mapping: { 1: 'INV1/A' }, coupleWithEm: true };
  const saved = settingsForSave(edited);
  assert.equal(saved.v_dc_V, null);           // blank -> null on the wire
  assert.equal(saved.f_carrier_hz, 24000);
  assert.deepEqual(saved.mapping, [{ coil: 1, bridge: 'INV1', leg: 'A' }]);

  const restored = formStateFromSettings(saved, DEFAULT_FORM);
  assert.equal(restored.device, 'IMCQ120R004M2H');
  assert.equal(restored.nPar, 3);
  assert.equal(restored.fsw, 24000);
  assert.equal(restored.vdc, '');
  assert.deepEqual(restored.mapping, { 1: 'INV1/A' });
  assert.equal(restored.coupleWithEm, true);
});

/* ── statusLine ──────────────────────────────────────────────────────────── */
// Owner 2026-09-22 production bug: "Error: p_ac_W is required" with no clue
// where to type it. The backend now refuses with one plain sentence instead,
// and a successful solve says which point of the duty it is FOR (the S1
// point beats out the setpoint that was typed on the Simulation tab).

test('an error always wins the status line, even with a stale solved_for', () => {
  const res = { solved_for: 'solved for the duty’s steady point: 10.0 A rms · 1.00 kW in' };
  assert.equal(statusLine(res, 'run the Simulation/coupled solve for this duty first'),
    'run the Simulation/coupled solve for this duty first');
});

test('a solved duty prints which point it was solved for', () => {
  const res = { solved_for: 'solved for the S1 point: 48.6 A rms · 1.99 kW in' };
  assert.equal(statusLine(res, null), 'solved for the S1 point: 48.6 A rms · 1.99 kW in');
});

test('nothing solved yet and no error is a blank status line, never "null"', () => {
  assert.equal(statusLine(null, null), null);
  assert.equal(statusLine({}, null), null);
});
