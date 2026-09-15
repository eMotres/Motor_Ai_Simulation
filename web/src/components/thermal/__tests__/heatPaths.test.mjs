/**
 * THE HEAT-PATH VIEW's reading layer: what a surface is COLOURED and what its
 * billboard SAYS.
 *
 * `HeatPathView3D` draws primitives; everything a user actually reads off it
 * comes out of four pure functions in `thermal/heatPaths.ts` — `heatColour`,
 * `sinkColour`, `sinkLabel` and `sinkTooltip` — plus `fmtW`.  They carry the
 * whole visual contract of the picture:
 *
 *   * a path that is OFF is grey and SAYS it is off.  "nothing sticks out of
 *     this housing" is an answer about the machine; a blank label or a
 *     near-black swatch would read as a measurement of zero;
 *   * the colour follows `intensity` — the share of the BIGGEST path — not the
 *     share of the total.  On the Ø85 joint the mount is 86 % of the outflow,
 *     and on a share scale every other path would be one flat colour;
 *   * the housing's label keeps the CONVECTION + RADIATION split, because on a
 *     small machine standing in still air radiation carries more than
 *     convection and the single number hides it;
 *   * watts are printed at a resolution that does not pretend: 0.59 W keeps its
 *     two decimals, 5644 W becomes 5.64 kW.
 *
 * The bodies below are copied VERBATIM from `thermal/heatPaths.ts` rather than
 * imported: that module is TypeScript, and `node --test` cannot load it (the
 * same reason `coolingPayload.test.mjs` and `partVocabulary.test.mjs` copy
 * theirs).  What is pinned here is therefore the BEHAVIOUR the mapping must
 * keep — changing it means changing this file too, and that is the moment
 * someone has to justify the new colours or the new wording.
 *
 * The two sinks used as fixtures are the real ones: the Ø85 robot joint's
 * mount (48.532 W, 86.4 %) and its housing (1.11 W = 0.45 conv + 0.67 rad),
 * from `CIANO28 85 20SW1200 / L13 / rated 120С wire 80C NdFeB`.
 */
import test from 'node:test';
import assert from 'node:assert/strict';

/* ── verbatim copies from thermal/heatPaths.ts ───────────────────────────── */

const SINK_OFF_COLOUR = '#4b5563';

const RAMP = [
  [0.0, [0x2f, 0x4f, 0x6f]],
  [0.5, [0xe8, 0xa2, 0x1c]],
  [1.0, [0xff, 0x2a, 0x10]],
];

const hex2 = (n) =>
  Math.max(0, Math.min(255, Math.round(n))).toString(16).padStart(2, '0');

function heatColour(intensity) {
  const t = Number.isFinite(intensity) ? Math.max(0, Math.min(1, intensity)) : 0;
  let i = 0;
  while (i < RAMP.length - 2 && t > RAMP[i + 1][0]) i++;
  const [t0, c0] = RAMP[i];
  const [t1, c1] = RAMP[i + 1];
  const u = t1 > t0 ? (t - t0) / (t1 - t0) : 0;
  return `#${c0.map((c, k) => hex2(c + (c1[k] - c) * u)).join('')}`;
}

function sinkColour(sink) {
  return sink.active ? heatColour(sink.intensity) : SINK_OFF_COLOUR;
}

function fmtW(w) {
  if (w === null || w === undefined || !Number.isFinite(w)) return '—';
  const a = Math.abs(w);
  if (a >= 1000) return `${(w / 1000).toFixed(a >= 10000 ? 0 : 2)} kW`;
  if (a >= 10) return `${w.toFixed(1)} W`;
  if (a >= 1) return `${w.toFixed(2)} W`;
  return `${w.toFixed(2)} W`;
}

function sinkLabel(sink, setting) {
  const set = setting ? ` ${setting}` : '';
  if (!sink.active) return `${sink.short}${set || ' — none'}`;
  const pct = sink.pct === null ? '' : ` · ${Math.round(sink.pct)} %`;
  const parts = sink.detail.length === 2
    ? ` (${sink.detail.map((p) => p.W.toFixed(sink.detail.some((q) => Math.abs(q.W) < 1) ? 2 : 1)).join(' + ')})`
    : '';
  return `${sink.short}${set ? `${set} ·` : ''} ${fmtW(sink.W)}${parts}${pct}`;
}

function editorFor(id) {
  switch (id) {
    case 'housing': return 'housing';
    case 'mount': return 'mount';
    case 'end_face_winding': case 'end_face_stator':
    case 'end_face_rotor': case 'end_face_magnet': return 'end_faces';
    case 'bore': return 'bore';
    case 'shaft_ends': return 'shaft_ends';
    case 'end_windings': case 'slot_channels': return 'frame';
    default: return null;
  }
}

const nz = (s, def) => (s ?? '').trim() || def;

function settingLabel(id, s) {
  if (!s) return null;
  const amb = nz(s.ambientT, '—');
  switch (editorFor(id)) {
    case 'housing':
      if (s.coolMode === 'robotics') return `ε ${nz(s.emissivity, '0.9')} @ ${amb} °C`;
      if (s.coolMode === 'air') return `${nz(s.airSpeed, '0')} m/s @ ${amb} °C`;
      if (s.coolMode === 'liquid') return `${nz(s.flowLpm, '8')} L/min @ ${nz(s.tIn, amb)} °C`;
      if (s.coolMode === 'manual') return `h ${nz(s.hConv, '—')} W/m²K`;
      return 'no cooling';
    case 'mount': {
      const g = Number(s.mountG);
      if (!(g > 0)) return 'off';
      return `${s.mountG} W/K @ ${nz(s.mountT, amb)} °C`;
    }
    case 'end_faces':
      if (s.coolMode !== 'robotics' || s.endFaces === 'none') return 'closed';
      return `${nz(s.endFaceSides, '2')} end${nz(s.endFaceSides, '2') === '1' ? '' : 's'} open`;
    case 'bore':
      if (s.boreMode === 'none') return 'closed';
      if (s.boreMode === 'still') return 'still air';
      if (s.boreMode === 'air') return `${nz(s.boreAirSpeed, '0')} m/s`;
      if (s.boreMode === 'liquid') return `${nz(s.boreFlowLpm, '0')} L/min`;
      return null;
    case 'shaft_ends': {
      const mm = Number(s.shaftExtMm);
      if (!(mm > 0)) return 'off';
      return `${s.shaftExtMm} mm × ${nz(s.shaftExtSides, '2')}`;
    }
    case 'frame':
      return s.frame === 'open' ? `open, ${nz(s.openAirSpeed, '0')} m/s` : 'housed';
    default:
      return null;
  }
}

function sinkTooltip(sink) {
  if (!sink.active) {
    return `${sink.label}. This machine has no such path (mode: ${sink.mode ?? '—'}), so nothing leaves here.`;
  }
  const bits = [`${sink.label}: ${fmtW(sink.W)}`];
  if (sink.pct !== null) bits.push(`${sink.pct} % of everything that left`);
  if (sink.detail.length === 2) {
    bits.push(sink.detail.map((p) => `${p.label} ${fmtW(p.W)}`).join(' + '));
  }
  if (sink.G_W_per_K !== null) bits.push(`G ${sink.G_W_per_K} W/K`);
  if (sink.h_W_per_m2K !== null) bits.push(`h ${sink.h_W_per_m2K} W/m²K`);
  if (sink.area_m2 !== null) bits.push(`over ${sink.area_m2} m²`);
  if (sink.t_surface_c !== null && sink.t_sink_c !== null) {
    bits.push(`${sink.t_surface_c} °C → ${sink.t_sink_c} °C`);
  }
  if (sink.note) bits.push(sink.note);
  return `${bits.join(' · ')}.`;
}

/* ── the Ø85 robot joint's own sinks ─────────────────────────────────────── */

const MOUNT = {
  id: 'mount', group: 'stator', label: 'Mount — bolted flange (conduction)',
  short: 'mount', mode: 'conduction', active: true, W: 48.532, pct: 86.4,
  intensity: 1, h_W_per_m2K: null, G_W_per_K: 2, area_m2: null,
  t_surface_c: 64.27, t_sink_c: 40, detail: [], note: 'given',
};

const HOUSING = {
  id: 'housing', group: 'stator', label: 'Housing — still air + radiation',
  short: 'housing', mode: 'robotics', active: true, W: 1.11, pct: 2,
  intensity: 0.0229, h_W_per_m2K: 11.739, G_W_per_K: null, area_m2: 0.003994,
  t_surface_c: 63.75, t_sink_c: 40,
  detail: [{ label: 'convection', W: 0.45 }, { label: 'radiation', W: 0.67 }],
  note: 'still air',
};

const SHAFT_ENDS_OFF = {
  id: 'shaft_ends', group: 'rotor', label: 'Shaft ends — fin in ambient air',
  short: 'shaft ends', mode: 'off', active: false, W: 0, pct: 0, intensity: 0,
  h_W_per_m2K: null, G_W_per_K: null, area_m2: null, t_surface_c: null,
  t_sink_c: null, detail: [], note: '',
};

const JACKET = {
  id: 'housing', group: 'stator', label: 'Housing — liquid jacket',
  short: 'housing', mode: 'liquid', active: true, W: 5644.14, pct: 96.3,
  intensity: 1, h_W_per_m2K: 100000, G_W_per_K: null, area_m2: 0.081551,
  t_surface_c: null, t_sink_c: 68.09, detail: [], note: 'turbulent',
};

/* ── the colour ramp ─────────────────────────────────────────────────────── */

test('heatColour: the ends of the ramp are exactly the stops', () => {
  assert.equal(heatColour(0), '#2f4f6f');
  assert.equal(heatColour(0.5), '#e8a21c');
  assert.equal(heatColour(1), '#ff2a10');
});

test('heatColour clamps instead of throwing on a bad intensity', () => {
  // A colour is not a place to discover a NaN: it has to render SOMETHING, and
  // the cold end is the honest default.
  assert.equal(heatColour(-3), '#2f4f6f');
  assert.equal(heatColour(NaN), '#2f4f6f');
  assert.equal(heatColour(undefined), '#2f4f6f');
  assert.equal(heatColour(17), '#ff2a10');
});

test('heatColour returns a well-formed hex for every intensity', () => {
  for (let i = 0; i <= 100; i++) {
    const c = heatColour(i / 100);
    assert.match(c, /^#[0-9a-f]{6}$/, `bad colour at ${i / 100}: ${c}`);
  }
});

test('heatColour warms monotonically: red rises, blue falls, all the way', () => {
  // This is what makes it ONE scale and not three colours.  A hot stop with
  // more blue in it than the amber inverts the ramp halfway and the picture
  // stops meaning anything — which is exactly what tailwind's #ef4444 (blue
  // 0x44 against the amber's 0x0b) did before the stops were chosen for this.
  const red = (c) => parseInt(c.slice(1, 3), 16);
  const blue = (c) => parseInt(c.slice(5, 7), 16);
  let prevR = -1; let prevB = 1e9;
  for (let i = 0; i <= 40; i++) {
    const c = heatColour(i / 40);
    assert.ok(red(c) >= prevR, `red fell at ${i / 40} (${c})`);
    assert.ok(blue(c) <= prevB, `blue rose at ${i / 40} (${c})`);
    prevR = red(c); prevB = blue(c);
  }
});

test('a small path is visibly NOT grey — that is why intensity is against the peak', () => {
  // the housing carries 2 % of the outflow but 2.3 % of the mount, and it still
  // has to be findable on the picture
  const c = sinkColour(HOUSING);
  assert.notEqual(c, SINK_OFF_COLOUR);
  assert.notEqual(c, sinkColour(MOUNT));
});

test('sinkColour: an OFF path is grey, and grey means off', () => {
  assert.equal(sinkColour(SHAFT_ENDS_OFF), SINK_OFF_COLOUR);
  // …and no intensity on the ramp may collide with it, or "off" and "almost
  // nothing" would be the same colour
  for (let i = 0; i <= 100; i++) {
    assert.notEqual(heatColour(i / 100), SINK_OFF_COLOUR);
  }
});

/* ── watts ───────────────────────────────────────────────────────────────── */

test('fmtW prints at a resolution that does not pretend', () => {
  assert.equal(fmtW(48.532), '48.5 W');       // the mount
  assert.equal(fmtW(1.11), '1.11 W');         // the housing
  assert.equal(fmtW(0.59), '0.59 W');         // the bore — two decimals kept
  assert.equal(fmtW(0), '0.00 W');
  assert.equal(fmtW(5644.14), '5.64 kW');     // the Ø200's jacket
  assert.equal(fmtW(58610), '59 kW');
  assert.equal(fmtW(-0.13), '-0.13 W');       // a gap term can be negative
});

test('fmtW: a missing number is a dash, never 0', () => {
  assert.equal(fmtW(null), '—');
  assert.equal(fmtW(undefined), '—');
  assert.equal(fmtW(NaN), '—');
  assert.equal(fmtW(Infinity), '—');
});

/* ── the billboard ───────────────────────────────────────────────────────── */

test('sinkLabel: the mount reads as the answer it is', () => {
  assert.equal(sinkLabel(MOUNT), 'mount 48.5 W · 86 %');
});

test('sinkLabel keeps the housing split — radiation carries more than convection', () => {
  assert.equal(sinkLabel(HOUSING), 'housing 1.11 W (0.45 + 0.67) · 2 %');
});

test('sinkLabel: an OFF path says so by name', () => {
  // Not blank, and not "0 W": a blank billboard reads as a measurement of zero
  // and "0 W" reads as a path that exists and does nothing.
  assert.equal(sinkLabel(SHAFT_ENDS_OFF), 'shaft ends — none');
});

test('sinkLabel: a jacket carries no split, so none is invented', () => {
  assert.equal(sinkLabel(JACKET), 'housing 5.64 kW · 96 %');
});

test('sinkLabel survives a missing share', () => {
  assert.equal(sinkLabel({ ...MOUNT, pct: null }), 'mount 48.5 W');
});

/* ── the hover behind it ─────────────────────────────────────────────────── */

test('sinkTooltip: the conductance, the temperatures and the identity behind the watts', () => {
  const t = sinkTooltip(MOUNT);
  // mount_W = G · (T_stator − T_mount) has to be checkable from the hover alone
  assert.ok(t.includes('48.5 W'), t);
  assert.ok(t.includes('86.4 % of everything that left'), t);
  assert.ok(t.includes('G 2 W/K'), t);
  assert.ok(t.includes('64.27 °C → 40 °C'), t);
});

test('sinkTooltip: the housing names both films and its area', () => {
  const t = sinkTooltip(HOUSING);
  assert.ok(t.includes('convection 0.45 W + radiation 0.67 W'), t);
  assert.ok(t.includes('h 11.739 W/m²K'), t);
  assert.ok(t.includes('over 0.003994 m²'), t);
});

test('sinkTooltip: an OFF path explains itself instead of showing an empty row', () => {
  const t = sinkTooltip(SHAFT_ENDS_OFF);
  assert.ok(t.includes('no such path'), t);
  assert.ok(t.includes('mode: off'), t);
});

/* ── the SETTING half of the billboard ───────────────────────────────────── */

/** The Ø85 joint's own cooling fields, as thermalStore holds them (strings). */
const L13_SETTINGS = {
  coolMode: 'robotics', ambientT: '40', airSpeed: '0', hConv: '',
  tIn: '', flowLpm: '8',
  boreMode: 'still', boreAirSpeed: '0', boreTIn: '', boreFlowLpm: '',
  shaftExtMm: '0', shaftExtSides: '2',
  frame: 'housed', openAirSpeed: '0',
  emissivity: '0.9', mountG: '2', mountT: '', endFaces: 'still', endFaceSides: '2',
};

test('settingLabel: the mount says its conductance AND its sink temperature', () => {
  // mount_W = G · (T_stator − T_mount): both halves of it have to be on the
  // label, or the number below cannot be checked against anything.
  assert.equal(settingLabel('mount', L13_SETTINGS), '2 W/K @ 40 °C');
});

test('settingLabel: a BLANK mount temperature reads as the ambient, not as 0', () => {
  // The panel's own rule — blank means "the room" — said out loud on the model.
  // Number('') === 0 would put a 0 °C sink on a machine in a 40 °C room.
  assert.equal(settingLabel('mount', { ...L13_SETTINGS, mountT: '' }), '2 W/K @ 40 °C');
  assert.equal(settingLabel('mount', { ...L13_SETTINGS, mountT: '25' }), '2 W/K @ 25 °C');
});

test('settingLabel: a mount bolted to nothing says off, not 0 W/K', () => {
  assert.equal(settingLabel('mount', { ...L13_SETTINGS, mountG: '0' }), 'off');
  assert.equal(settingLabel('mount', { ...L13_SETTINGS, mountG: '' }), 'off');
});

test('settingLabel: the housing follows the cooling MODE', () => {
  assert.equal(settingLabel('housing', L13_SETTINGS), 'ε 0.9 @ 40 °C');
  assert.equal(settingLabel('housing', { ...L13_SETTINGS, coolMode: 'air', airSpeed: '12' }),
               '12 m/s @ 40 °C');
  assert.equal(settingLabel('housing', { ...L13_SETTINGS, coolMode: 'liquid', flowLpm: '10', tIn: '60' }),
               '10 L/min @ 60 °C');
  assert.equal(settingLabel('housing', { ...L13_SETTINGS, coolMode: 'none' }), 'no cooling');
});

test('settingLabel: end faces, bore and shaft ends', () => {
  assert.equal(settingLabel('end_face_winding', L13_SETTINGS), '2 ends open');
  assert.equal(settingLabel('end_face_stator', { ...L13_SETTINGS, endFaceSides: '1' }),
               '1 end open');
  assert.equal(settingLabel('end_face_rotor', { ...L13_SETTINGS, endFaces: 'none' }), 'closed');
  // …and a machine that is not in the robotics mode has no end-face path at all
  assert.equal(settingLabel('end_face_magnet', { ...L13_SETTINGS, coolMode: 'liquid' }), 'closed');
  assert.equal(settingLabel('bore', L13_SETTINGS), 'still air');
  assert.equal(settingLabel('bore', { ...L13_SETTINGS, boreMode: 'none' }), 'closed');
  assert.equal(settingLabel('bore', { ...L13_SETTINGS, boreMode: 'air', boreAirSpeed: '30' }),
               '30 m/s');
  assert.equal(settingLabel('shaft_ends', L13_SETTINGS), 'off');
  assert.equal(settingLabel('shaft_ends', { ...L13_SETTINGS, shaftExtMm: '20' }), '20 mm × 2');
});

test('settingLabel: no settings in hand is null, not a guess', () => {
  for (const id of ['mount', 'housing', 'bore', 'shaft_ends', 'end_face_winding']) {
    assert.equal(settingLabel(id, null), null);
  }
});

test('editorFor: every drawn path opens the popover that owns its fields', () => {
  assert.equal(editorFor('housing'), 'housing');
  assert.equal(editorFor('mount'), 'mount');
  assert.equal(editorFor('bore'), 'bore');
  assert.equal(editorFor('shaft_ends'), 'shaft_ends');
  assert.equal(editorFor('end_windings'), 'frame');
  assert.equal(editorFor('slot_channels'), 'frame');
  // all four axial faces are ONE setting — they are switched on together
  for (const id of ['end_face_winding', 'end_face_stator', 'end_face_rotor',
                    'end_face_magnet']) {
    assert.equal(editorFor(id), 'end_faces');
  }
  assert.equal(editorFor('nonesuch'), null);
});

test('sinkLabel with a setting: what it is set to AND what that bought', () => {
  // the shape the user asked for on 2026-09-15
  assert.equal(sinkLabel(MOUNT, settingLabel('mount', L13_SETTINGS)),
               'mount 2 W/K @ 40 °C · 48.5 W · 86 %');
  assert.equal(sinkLabel(HOUSING, settingLabel('housing', L13_SETTINGS)),
               'housing ε 0.9 @ 40 °C · 1.11 W (0.45 + 0.67) · 2 %');
});

test('sinkLabel: an OFF path with a setting names the setting, not "none"', () => {
  // "shaft ends off" is a switch the user can flip here; "shaft ends — none"
  // is what it says when there are no settings to flip.
  assert.equal(sinkLabel(SHAFT_ENDS_OFF, settingLabel('shaft_ends', L13_SETTINGS)),
               'shaft ends off');
  assert.equal(sinkLabel(SHAFT_ENDS_OFF, null), 'shaft ends — none');
});

test('sinkTooltip never leaves a dangling separator', () => {
  for (const s of [MOUNT, HOUSING, JACKET, SHAFT_ENDS_OFF]) {
    const t = sinkTooltip(s);
    assert.ok(t.endsWith('.'), t);
    assert.ok(!t.includes(' · .'), t);
    assert.ok(!t.includes('undefined'), t);
    assert.ok(!t.includes('null'), t);
  }
});
