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
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));

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
  // A per-bridge override in the saved block (API/CLI, or an older save) is
  // ignored — the web only ever shows/writes the one global devices_parallel.
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
    areaBasis: cooling.plate_area_cm2 != null ? 'plate'
      : cooling.heatsink_area_cm2_per_device != null ? 'heatsink' : fallback.areaBasis,
    areaCm2: toFormNumber(cooling.plate_area_cm2 != null ? cooling.plate_area_cm2
                                                          : cooling.heatsink_area_cm2_per_device),
    finEff: toFormNumber(cooling.fin_efficiency),
    emissivity: toFormNumber(cooling.emissivity),
    mapping: rows.length ? mapping : fallback.mapping,
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
    // ALWAYS {} — the whole-replace PATCH clears any per-bridge override a
    // saved block held, since the web has only the one global count now.
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

const DEFAULT_FORM = {
  device: '', topology: 'one_3ph', setSplit: 'series_split', hbMod: 'unipolar',
  nPar: 4, rg: 2.3, vgsOff: 0, dead: 0.5, fsw: '', vdc: '',
  coolant: 'water_glycol_50', flow: 8, tin: 65, rtim: 0.03,
  coolingMode: 'liquid', airSpeed: 5, tAmbient: 40, areaBasis: 'heatsink',
  areaCm2: '', finEff: 0.75, emissivity: 0.9,
  mapping: {}, coupleWithEm: false,
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
  // A per-bridge override in the block (from the API/CLI) has no seat in the
  // panel state at all — the web only shows/edits the one global count.
  assert.equal(s.parByBridge, undefined);
  assert.equal(s.rg, 4.7);
  assert.equal(s.fsw, 24000);
  assert.equal(s.vdc, 750.4);
  assert.equal(s.coolant, 'oil');
  assert.equal(s.flow, 10);
  assert.deepEqual(s.mapping, { 1: 'INV1/A', 2: 'INV2/B' });
  assert.equal(s.coupleWithEm, true);
});

test('settingsForSave always clears a per-bridge override — the global '
   + 'count is the only one the web ever writes', () => {
  const edited = { ...DEFAULT_FORM, nPar: 5 };
  const saved = settingsForSave(edited);
  assert.deepEqual(saved.devices_parallel_by_bridge, {});
  assert.equal(saved.devices_parallel, 5);
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

/* ── controllerSolveBody — the wire payload (owner 2026-09-22, third round:
 * "Error: v_dc_V is required" — «проверь всё») ─────────────────────────────
 * Every machine/point value (V_dc, carrier, current, power, connection) is
 * resolved server-side; the tab must never send '' or null for a blank
 * number box, only OMIT the key entirely, or the route reads "the request
 * provided this field" and the server-side fallback chain never runs.
 */

const blank = (v) => (v === '' ? undefined : v);

function controllerSolveBody(s, customRows) {
  const mode = s.coolingMode || 'liquid';
  const cooling = { mode };
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

test('a blank V_dc/carrier/R_g/dead-time is OMITTED from the wire, never null', () => {
  const s = { ...DEFAULT_FORM, vdc: '', fsw: '', rg: '', vgsOff: '', dead: '',
    rtim: '', flow: '', tin: '' };
  const body = controllerSolveBody(s, []);
  const json = JSON.stringify(body);
  for (const key of ['v_dc_V', 'f_carrier_hz', 'r_g_ext_ohm', 'v_gs_off_V',
                      'dead_time_us', 'r_tim_k_w']) {
    assert.equal(body[key], undefined, `${key} must be undefined, not a blank string`);
    assert.ok(!json.includes(`"${key}"`), `${key} leaked into the JSON wire body`);
  }
  // cooling is a nested object — its own blanks must vanish the same way
  // (JSON.stringify drops an undefined-valued key even inside a sub-object).
  assert.equal(body.cooling.flow_lpm, undefined);
  assert.equal(body.cooling.t_in_c, undefined);
  assert.ok(!json.includes('flow_lpm'));
  assert.ok(!json.includes('"t_in_c"'));
  assert.ok(!json.includes('null'), 'no field may cross the wire as null either');
});

test('a real zero (V_GS off = 0 V) is sent, never mistaken for blank', () => {
  const s = { ...DEFAULT_FORM, vgsOff: 0, rg: 0 };
  const body = controllerSolveBody(s, []);
  assert.equal(body.v_gs_off_V, 0);
  assert.equal(body.r_g_ext_ohm, 0);
  assert.ok(JSON.stringify(body).includes('"v_gs_off_V":0'));
});

test('filled fields cross the wire exactly as typed', () => {
  const s = { ...DEFAULT_FORM, vdc: 750.4, fsw: 24000, rg: 4.7 };
  const body = controllerSolveBody(s, []);
  assert.equal(body.v_dc_V, 750.4);
  assert.equal(body.f_carrier_hz, 24000);
  assert.equal(body.r_g_ext_ohm, 4.7);
});

test('the solve body never carries a per-bridge override — only the global '
   + 'devices_parallel crosses the wire', () => {
  const body = controllerSolveBody(DEFAULT_FORM, []);
  assert.equal(body.devices_parallel_by_bridge, undefined);
  assert.ok(!JSON.stringify(body).includes('devices_parallel_by_bridge'));
  assert.equal(body.devices_parallel, DEFAULT_FORM.nPar);
});

test('a custom mapping is sent only for the custom topology', () => {
  const rows = [{ coil: 1, bridge: 'INV1', leg: 'A' }];
  const custom = controllerSolveBody({ ...DEFAULT_FORM, topology: 'custom' }, rows);
  assert.deepEqual(custom.mapping, rows);
  const notCustom = controllerSolveBody({ ...DEFAULT_FORM, topology: 'one_3ph' }, rows);
  assert.equal(notCustom.mapping, undefined);
});

/* ── cooling.mode payload (owner 2026-09-22 evening) ─────────────────────────
 * "надо добавить воздушное охлаждение и скорость ветра, как в
 * термосимуляции" — the same liquid / forced-air / still-air choice the
 * thermal tab already offers, now for the device heatsink/plate
 * (``ControllerCoolingSpec`` on the backend, ``inverter.losses.COOLING_MODES``).
 * ``ControllerCoolingSpec`` ignores whatever a mode does not use, but the
 * wire body only ever sends what the CHOSEN mode reads, per mode.
 */

test('cooling mode "liquid" sends only coolant/flow/inlet, never an air field', () => {
  const body = controllerSolveBody({ ...DEFAULT_FORM, coolingMode: 'liquid' }, []);
  assert.equal(body.cooling.mode, 'liquid');
  assert.equal(body.cooling.coolant, 'water_glycol_50');
  assert.equal(body.cooling.flow_lpm, 8);
  assert.equal(body.cooling.t_in_c, 65);
  for (const k of ['air_speed_mps', 't_ambient_c', 'heatsink_area_cm2_per_device',
                    'plate_area_cm2', 'fin_efficiency', 'emissivity']) {
    assert.equal(body.cooling[k], undefined, `${k} must not be sent in liquid mode`);
  }
});

test('cooling mode "air_forced" sends wind speed, ambient, area and fin '
   + 'efficiency — never the coolant/flow/inlet triple or emissivity', () => {
  const s = { ...DEFAULT_FORM, coolingMode: 'air_forced', airSpeed: 6, tAmbient: 35 };
  const body = controllerSolveBody(s, []);
  assert.equal(body.cooling.mode, 'air_forced');
  assert.equal(body.cooling.air_speed_mps, 6);
  assert.equal(body.cooling.t_ambient_c, 35);
  assert.equal(body.cooling.fin_efficiency, 0.75);
  // default area basis is "heatsink" (per device) with a blank area — the
  // backend's own 40 cm^2/device default then applies.
  assert.equal(body.cooling.heatsink_area_cm2_per_device, undefined);
  assert.equal(body.cooling.plate_area_cm2, undefined);
  for (const k of ['coolant', 'flow_lpm', 't_in_c', 'emissivity']) {
    assert.equal(body.cooling[k], undefined, `${k} must not be sent in air_forced mode`);
  }
});

test('cooling mode "air_still" sends emissivity and no air speed', () => {
  const s = { ...DEFAULT_FORM, coolingMode: 'air_still', emissivity: 0.85 };
  const body = controllerSolveBody(s, []);
  assert.equal(body.cooling.mode, 'air_still');
  assert.equal(body.cooling.emissivity, 0.85);
  assert.equal(body.cooling.air_speed_mps, undefined,
    'air_still has no fan — air_speed_mps is a forced-air-only field');
  assert.equal(body.cooling.t_ambient_c, 40);
});

test('area basis "plate" sends plate_area_cm2 instead of the per-device area', () => {
  const s = { ...DEFAULT_FORM, coolingMode: 'air_forced', areaBasis: 'plate', areaCm2: 120 };
  const body = controllerSolveBody(s, []);
  assert.equal(body.cooling.plate_area_cm2, 120);
  assert.equal(body.cooling.heatsink_area_cm2_per_device, undefined);
});

test('a saved air-cooling block round-trips through formStateFromSettings / '
   + 'settingsForSave, including the area basis it was saved with', () => {
  const block = {
    device: 'X', topology: 'one_3ph', devices_parallel: 1,
    cooling: { mode: 'air_still', t_ambient_c: 30, emissivity: 0.8,
              plate_area_cm2: 150, fin_efficiency: 0.6 },
  };
  const s = formStateFromSettings(block, DEFAULT_FORM);
  assert.equal(s.coolingMode, 'air_still');
  assert.equal(s.tAmbient, 30);
  assert.equal(s.emissivity, 0.8);
  assert.equal(s.areaBasis, 'plate');
  assert.equal(s.areaCm2, 150);
  assert.equal(s.finEff, 0.6);

  const saved = settingsForSave(s);
  assert.equal(saved.cooling.mode, 'air_still');
  assert.equal(saved.cooling.plate_area_cm2, 150);
  assert.equal(saved.cooling.heatsink_area_cm2_per_device, null);
});

test('an old configuration with no saved cooling.mode restores as "liquid" '
   + '— bit-identical with every save from before 2026-09-22', () => {
  const block = { device: 'X', topology: 'one_3ph', devices_parallel: 1,
    cooling: { coolant: 'oil', flow_lpm: 10, t_in_c: 55 } };
  const s = formStateFromSettings(block, DEFAULT_FORM);
  assert.equal(s.coolingMode, 'liquid');
  assert.equal(s.coolant, 'oil');
});

/* ── the resolved point pre-fills Carrier/DC link, never silently overrides
 * (owner 2026-09-22 evening screenshots: "надо брать эти значения из
 * электромагнитного моделирования или из батареи и рисовать значения") ────
 * The panel shows the resolved value as a PLACEHOLDER (``carrierPlaceholder``
 * / ``vdcPlaceholder`` below, verbatim copies of the JSX prop expressions —
 * see ControllerPanel.tsx's Carrier/DC-link ``<Num>`` rows) while the actual
 * form field stays at its blank-means-"the duty's own" sentinel, so
 * ``controllerSolveBody`` keeps omitting the key exactly as it already does
 * (tested above) — the prefill is cosmetic, not a hidden default that would
 * stop a real duty-side change from being picked up on the next resolve. An
 * EDITED value is a deliberate override and crosses the wire as typed,
 * whether or not it matches the point.
 */

function carrierPlaceholder(point) {
  return point && point.f_carrier_hz != null ? fmt(point.f_carrier_hz, 0) : undefined;
}
function vdcPlaceholder(point) {
  return point && point.v_dc_V != null ? fmt(point.v_dc_V, 0) : undefined;
}

test('a resolved point renders as a placeholder, formatted like every other number', () => {
  const point = { f_carrier_hz: 24000, v_dc_V: 750.4, sources: {} };
  assert.equal(carrierPlaceholder(point), '24,000');
  assert.equal(vdcPlaceholder(point), '750');
});

test('no resolved point (no duty loaded yet) leaves the placeholder undefined', () => {
  assert.equal(carrierPlaceholder(null), undefined);
  assert.equal(vdcPlaceholder({ f_carrier_hz: null, v_dc_V: null }), undefined);
});

test('a blank Carrier/DC-link field is OMITTED from the solve even with a '
   + 'point resolved and shown as a placeholder — the prefill never becomes '
   + 'a hidden value the wire sends on its own', () => {
  const point = { f_carrier_hz: 24000, v_dc_V: 750.4, sources: {} };
  assert.equal(carrierPlaceholder(point), '24,000'); // shown…
  const body = controllerSolveBody({ ...DEFAULT_FORM, fsw: '', vdc: '' }, []); // …but blank stays blank
  assert.equal(body.f_carrier_hz, undefined);
  assert.equal(body.v_dc_V, undefined);
});

test('typing over the placeholder sends the typed value as a deliberate '
   + 'override, even when it differs from the resolved point', () => {
  const body = controllerSolveBody({ ...DEFAULT_FORM, fsw: 48000, vdc: 800 }, []);
  assert.equal(body.f_carrier_hz, 48000);
  assert.equal(body.v_dc_V, 800);
  const saved = settingsForSave({ ...DEFAULT_FORM, fsw: 48000, vdc: 800 });
  assert.equal(saved.f_carrier_hz, 48000, 'the override is what gets saved with the configuration');
  assert.equal(saved.v_dc_V, 800);
});

/* ── controllerMirrorApplies (owner 2026-09-22, second round) ───────────────
 * "при сохранении мотора текущий контроллер тоже должен сохраняться" — not
 * only the Controller tab's own button. ActiveFamilyStrip's "Save to duty"
 * reads the `ctrl.settings` localStorage mirror and PATCHes it in the SAME
 * flow, but only when the tag matches the motor actually being saved — a
 * mirror left over from a different motor must never land on this one.
 */

function controllerMirrorApplies(mirrored, die, config) {
  return !!mirrored && !!mirrored.block
    && mirrored.die === die && mirrored.config === config;
}

test('a mirror tagged for the motor being saved applies', () => {
  const mirrored = { die: 'CIANO14 50 edited', config: 'L15',
    block: { device: 'IMCQ120R004M2H' } };
  assert.equal(controllerMirrorApplies(mirrored, 'CIANO14 50 edited', 'L15'), true);
});

test('a mirror from a DIFFERENT motor never applies', () => {
  const mirrored = { die: 'CIANO14 50 edited', config: 'L15',
    block: { device: 'IMCQ120R004M2H' } };
  assert.equal(controllerMirrorApplies(mirrored, 'CIANO 150_40', 'L35'), false);
  assert.equal(controllerMirrorApplies(mirrored, 'CIANO14 50 edited', 'L20'), false);
});

test('nothing mirrored yet (the Controller tab was never opened) never applies', () => {
  assert.equal(controllerMirrorApplies(null, 'CIANO14 50 edited', 'L15'), false);
  assert.equal(controllerMirrorApplies(undefined, 'CIANO14 50 edited', 'L15'), false);
  assert.equal(controllerMirrorApplies({ die: 'X', config: 'Y', block: null },
    'X', 'Y'), false);
});

/* ── readControllerMirror / saveControllerFromMirror (owner 2026-09-22, third
 * round: the Coupled panel's "inverter (Controller)" stayed DISABLED for
 * CIANO14 50 edited / L15 although the owner had set the Controller tab up —
 * `GET /api/controller/settings` answered `{}`, the api log showed GETs
 * only, never a PATCH). `readControllerMirror` is what the Drive selector's
 * gating now reads INSTEAD of (or alongside) that GET, and
 * `saveControllerFromMirror` is what fires the missing PATCH itself, on the
 * SAME `ctrl.settings` mirror `controllerMirrorApplies` above already
 * guards — so it inherits the same die/config tag rule for free.
 */

function readControllerMirror(store, die, config) {
  try {
    const raw = store['ctrl.settings'];
    const mirrored = raw ? JSON.parse(raw) : null;
    return controllerMirrorApplies(mirrored, die, config) ? mirrored.block : null;
  } catch { return null; }
}

async function saveControllerFromMirror(store, die, config, patch) {
  const block = readControllerMirror(store, die, config);
  if (!block || !block.device) return null;
  try {
    const saved = await patch(die, config, block);
    return { ok: true, block: saved ?? block };
  } catch (e) { return { ok: false, error: String(e) }; }
}

test('readControllerMirror returns the tagged block\'s device, and null on '
   + 'a stale/missing/deviceless one', () => {
  const store = { 'ctrl.settings': JSON.stringify(
    { die: 'CIANO14 50 edited', config: 'L15', block: { device: 'IMCQ120R004M2H' } }) };
  assert.deepEqual(readControllerMirror(store, 'CIANO14 50 edited', 'L15'),
    { device: 'IMCQ120R004M2H' });
  assert.equal(readControllerMirror(store, 'CIANO14 50 edited', 'L20'), null,
    'a mirror tagged for a different configuration must not leak into this one');
  assert.equal(readControllerMirror({}, 'CIANO14 50 edited', 'L15'), null);
});

test('saveControllerFromMirror is a no-op (null) when nothing is chosen on '
   + 'the Controller tab for this configuration — never an error the caller '
   + 'has to show, since the gating already keeps the option disabled then', async () => {
  assert.equal(await saveControllerFromMirror({}, 'CIANO14 50 edited', 'L15',
    async () => { throw new Error('must not be called'); }), null);
  const noDevice = { 'ctrl.settings': JSON.stringify(
    { die: 'CIANO14 50 edited', config: 'L15', block: { device: null } }) };
  assert.equal(await saveControllerFromMirror(noDevice, 'CIANO14 50 edited', 'L15',
    async () => { throw new Error('must not be called'); }), null);
});

test('saveControllerFromMirror PATCHes the live mirror and reports what the '
   + 'server saved — this is the missing write: a device chosen but never '
   + 'explicitly saved on the Controller tab now reaches the server the '
   + 'moment "inverter" is picked, with no prior Solve required', async () => {
  const store = { 'ctrl.settings': JSON.stringify(
    { die: 'CIANO14 50 edited', config: 'L15', block: { device: 'IMCQ120R004M2H',
      topology: 'one_3ph', f_carrier_hz: 24000 } }) };
  let patched = null;
  const patch = async (die, config, block) => {
    patched = { die, config, block };
    return { ...block, saved_at: '2026-09-22T21:00:00' };
  };
  const res = await saveControllerFromMirror(store, 'CIANO14 50 edited', 'L15', patch);
  assert.deepEqual(patched, { die: 'CIANO14 50 edited', config: 'L15',
    block: { device: 'IMCQ120R004M2H', topology: 'one_3ph', f_carrier_hz: 24000 } });
  assert.equal(res.ok, true);
  assert.equal(res.block.device, 'IMCQ120R004M2H');
  assert.equal(res.block.saved_at, '2026-09-22T21:00:00');
});

test('saveControllerFromMirror reports a failed PATCH (a 4xx, a network drop) '
   + 'as {ok: false, error}, never a swallowed exception', async () => {
  const store = { 'ctrl.settings': JSON.stringify(
    { die: 'CIANO14 50 edited', config: 'L15', block: { device: 'IMCQ120R004M2H' } }) };
  const res = await saveControllerFromMirror(store, 'CIANO14 50 edited', 'L15',
    async () => { throw new Error('controller.devices_parallel must be at least 1'); });
  assert.equal(res.ok, false);
  assert.match(res.error, /devices_parallel/);
});

/* ── ActiveFamilyStrip's "Save to duty" — the actual missing-PATCH root
 * cause (owner 2026-09-22, third round).  `readControllerMirror` above is
 * gating; THIS is why the write itself never fired: "Save to duty" tags the
 * PATCH's applicability check against `cfgName`, the configuration's name
 * AFTER a possible auto-rename (a die-defining geometry change moves the
 * duty to a new configuration automatically, mid-save) — but the Controller
 * tab's mirror was written under `targetConfig`, the name that was active
 * BEFORE that same save renamed it, because the rename is a RESULT of this
 * save, unknowable beforehand.  Every other post-save concern in that
 * function (the duty-op overlay, the duty-cycle overlay) already clears
 * BOTH names for exactly this reason; the controller mirror check alone
 * compared only the post-rename name, so a same-save rename silently
 * dropped the PATCH — zero network traffic, matching the owner's api log
 * (GETs only). Source-checked here (not re-implemented): ActiveFamilyStrip
 * imports import.meta.env and JSX, which `node --test` cannot load. */
test('ActiveFamilyStrip checks the controller mirror against BOTH the '
   + 'pre-rename (targetConfig) and post-rename (cfgName) configuration '
   + 'name, not cfgName alone', () => {
  const fs = readFileSync(
    join(HERE, '..', '..', 'common', 'ActiveFamilyStrip.tsx'), 'utf8');
  const start = fs.indexOf("const raw = localStorage.getItem('ctrl.settings');");
  assert.ok(start > 0, 'the controller-mirror save block must exist');
  const end = fs.indexOf('} catch { /* the controller mirror is best-effort', start);
  const block = fs.slice(start, end);
  assert.ok(block.includes('controllerMirrorApplies(mirrored, tDie, targetConfig)'),
    'must check the PRE-rename name the mirror was actually tagged with');
  assert.ok(block.includes('controllerMirrorApplies(mirrored, tDie, cfgName)'),
    'must also check the POST-rename name (an edit made after the rename)');
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
