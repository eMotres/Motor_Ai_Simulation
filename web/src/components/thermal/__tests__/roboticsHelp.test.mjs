/**
 * The cooling menu's WORDS, pinned.
 *
 * Owner, 2026-09-17: the Thermal tab's cooling rows were unreadable — `ε`,
 * `mount W/K`, `2 ends open` — and nothing said which of them is the radiation
 * parameter or what the mount conducts into.  The fix was a single text module
 * (`thermal/roboticsHelp.ts`) that the panel, the 3-D view's surface popovers
 * and the arrow tooltips all read, so the three places cannot drift.  This file
 * is what makes "cannot drift" true: it pins the CONTRACT those texts have to
 * keep — a unit in the label, the physics named in the tip, the shares quoted
 * from the real record rather than invented, and one governing parameter per
 * heat path in the collapsible note.
 *
 * Imported, not copied: this module is plain data with erasable annotations, so
 * node strips the types and loads the real file (node ≥ 23.6).  The older tests
 * beside it copy their source because they pin FUNCTIONS out of a .tsx-adjacent
 * module that pulls in React.
 *
 * The numbers are the Ø85 L13 robot joint's last heat-path record
 * (`GET /api/thermal/heat_paths/last`, robotics, 40 °C room): 195.8 W removed,
 * mount 163.2 W = 83 %, end-winding faces 18.4 W = 9.4 %, housing 5.0 W = 2.5 %
 * (radiation 2.91 W against convection 2.05 W), bore 2.0 W = 1 %.
 */
import test from 'node:test';
import assert from 'node:assert/strict';

import {
  BORE_MODE_LABEL, COOL_MODE_LABEL, END_FACE_LABEL, END_FACE_SIDES_LABEL,
  FRAME_LABEL, HOW_IT_WORKS, HOW_IT_WORKS_TITLE, L13_SHARES, PARAM_BY_SINK,
  ROBOTICS_HELP, ROBOTICS_SUBTITLE, SHAFT_SIDES_LABEL, setByLine,
} from '../roboticsHelp.ts';

const KEYS = [
  'coolMode', 'ambientT', 'emissivity', 'mountG', 'mountT',
  'endFaces', 'endFaceSides', 'boreMode', 'shaftExtMm', 'shaftExtSides',
  'frame', 'openAirSpeed',
];

/** Sentences, counted the way a reader does. */
const sentences = (s) => s.split(/(?<=[.!?])\s+/).filter((x) => x.trim().length > 1);

test('every control has a label, a short caption and a tip', () => {
  assert.deepEqual(Object.keys(ROBOTICS_HELP).sort(), [...KEYS].sort());
  for (const k of KEYS) {
    const c = ROBOTICS_HELP[k];
    assert.ok(c.label.length >= 5, `${k}: label too terse`);
    assert.ok(c.short.length >= 1, `${k}: no short caption`);
    assert.ok(c.tip.length > 120, `${k}: tip is not an explanation`);
  }
});

test('a tip is 2…5 sentences — an explanation, never a wall of text', () => {
  for (const k of KEYS) {
    const n = sentences(ROBOTICS_HELP[k].tip).length;
    assert.ok(n >= 2 && n <= 5, `${k}: ${n} sentences`);
  }
});

test('the label carries the unit, or the word that makes it obvious', () => {
  assert.equal(ROBOTICS_HELP.emissivity.label, 'ε (radiation)');
  assert.equal(ROBOTICS_HELP.mountG.label, 'mount to arm, W/K');
  assert.equal(ROBOTICS_HELP.mountT.label, 'mount °C (blank = room air)');
  assert.equal(ROBOTICS_HELP.ambientT.label, 'room air °C');
  assert.equal(ROBOTICS_HELP.shaftExtMm.label, 'shaft out of housing, mm/side');
  // no label may be one of the old internal stubs
  for (const k of KEYS) {
    assert.ok(!['ε', 'mount W/K', 'mount °C', 'shaft out, mm/side']
      .includes(ROBOTICS_HELP[k].label), `${k}: still the old terse label`);
  }
});

test('the emissivity tip says it is THE radiation parameter, with the law and the numbers', () => {
  const t = ROBOTICS_HELP.emissivity.tip;
  assert.match(t, /RADIATION PARAMETER/);
  assert.match(t, /ε·σ·A·\(T⁴ − T_room⁴\)/);
  assert.match(t, /0\.9/);            // the store's default
  assert.match(t, /anodised/);        // what 0.9 IS
  assert.match(t, /polished/);        // the other end of the range
  assert.match(t, new RegExp(String(L13_SHARES.housing_radiation_W)));
  assert.match(t, new RegExp(String(L13_SHARES.housing_convection_W)));
});

test('the mount tips say what it conducts into, the law, the default and the blank rule', () => {
  const g = ROBOTICS_HELP.mountG.tip;
  assert.match(g, /robot's own structure/);
  assert.match(g, /G·\(T_housing − T_mount\)/);
  assert.match(g, /2 W\/K is an ASSUMPTION/);
  assert.match(g, new RegExp(`${L13_SHARES.mount_pct} %`));
  const t = ROBOTICS_HELP.mountT.tip;
  assert.match(t, /BLANK means the room air temperature/);
});

test('end faces, bore and shaft tips say what the options DO', () => {
  assert.match(ROBOTICS_HELP.endFaces.tip, /end turns standing proud of the core/);
  assert.match(ROBOTICS_HELP.endFaces.tip, /h·A·n_faces/);
  assert.match(ROBOTICS_HELP.endFaceSides.tip, /flange side/);
  assert.match(ROBOTICS_HELP.boreMode.tip, /WITHOUT crossing the air gap/);
  assert.match(ROBOTICS_HELP.boreMode.tip, /adiabatic/);
  assert.match(ROBOTICS_HELP.shaftExtMm.tip, /FIN/);
  assert.match(ROBOTICS_HELP.shaftExtMm.tip, /0 turns this path off/);
});

test('every quoted share is the record\'s, not an invented one', () => {
  const quoted = Object.values(ROBOTICS_HELP).map((c) => c.tip).join(' ')
    + HOW_IT_WORKS.map((h) => h.text).join(' ');
  for (const pct of [L13_SHARES.mount_pct, L13_SHARES.end_faces_total_pct,
                     L13_SHARES.bore_pct]) {
    assert.match(quoted, new RegExp(`${pct} %`), `share ${pct} % is never quoted`);
  }
  // …and the shares must still add up to the record's own outflow
  const parts = L13_SHARES.mount_W + L13_SHARES.housing_W
    + L13_SHARES.end_faces_total_W + L13_SHARES.bore_W;
  assert.ok(Math.abs(parts - L13_SHARES.removed_W) < 1.0,
            `the quoted watts (${parts}) do not make the removed ${L13_SHARES.removed_W} W`);
  assert.ok(Math.abs(L13_SHARES.housing_convection_W + L13_SHARES.housing_radiation_W
                     - L13_SHARES.housing_W) < 0.1);
});

test('the option texts say what the option does, not what it is called', () => {
  assert.equal(COOL_MODE_LABEL.robotics, 'Robotics — still air + radiation + mount');
  assert.match(COOL_MODE_LABEL.none, /adiabatic/);
  assert.match(BORE_MODE_LABEL.still, /Open bore/);
  assert.match(BORE_MODE_LABEL.still, /radiation/);
  assert.match(BORE_MODE_LABEL.none, /adiabatic/);
  assert.match(END_FACE_LABEL.still, /open to the air/i);
  assert.match(END_FACE_LABEL.none, /closed/i);
  assert.equal(END_FACE_SIDES_LABEL['1'], '1 end open (mount side shut)');
  assert.equal(END_FACE_SIDES_LABEL['2'], 'Both ends open');
  assert.match(FRAME_LABEL.housed, /closed case/);
  assert.match(FRAME_LABEL.open, /no housing/);
  assert.equal(SHAFT_SIDES_LABEL['2'], '2 shaft ends out');
});

test('the subtitle is one short line and names the three paths', () => {
  assert.ok(ROBOTICS_SUBTITLE.length <= 70, 'the subtitle is a line, not a paragraph');
  for (const w of ['still air', 'radiation', 'mount', 'no fan']) {
    assert.match(ROBOTICS_SUBTITLE, new RegExp(w));
  }
});

test('the collapsible note is 6…10 short lines, in the order of the 3-D view', () => {
  assert.equal(HOW_IT_WORKS_TITLE, 'How this cooling model works');
  assert.ok(HOW_IT_WORKS.length >= 6 && HOW_IT_WORKS.length <= 10,
            `${HOW_IT_WORKS.length} lines`);
  for (const h of HOW_IT_WORKS) {
    assert.ok(h.path.length > 0 && h.text.length > 0);
    assert.ok(`${h.path}: ${h.text}`.length <= 220, `line too long: ${h.path}`);
  }
  const paths = HOW_IT_WORKS.map((h) => h.path);
  const at = (frag) => paths.findIndex((p) => p.includes(frag));
  // winding → stator → housing → its three sinks, then the rotor side
  assert.ok(at('winding → stator iron') === 0);
  assert.ok(at('housing → still air') < at('housing → radiation'));
  assert.ok(at('housing → radiation') < at('housing → the arm'));
  assert.ok(at('housing → the arm') < at('rotor → bore'));
  assert.ok(at('air gap') < at('rotor → bore'));
  assert.ok(at('rotor → bore') < at('rotor → shaft stubs'));
});

test('every governed line names a parameter EXACTLY as its control is labelled', () => {
  const labels = new Set(Object.values(ROBOTICS_HELP).map((c) => c.label));
  const named = HOW_IT_WORKS.filter((h) => h.param);
  assert.ok(named.length >= 5, 'the note has to name the parameters');
  for (const h of named) {
    assert.ok(labels.has(h.param),
              `"${h.param}" is not any control's label — the note and the row disagree`);
  }
  // the four paths the owner asked about are each named once
  for (const p of ['ε (radiation)', 'mount to arm, W/K', 'End faces', 'Rotor bore']) {
    assert.equal(named.filter((h) => h.param === p).length, 1, `${p} not named once`);
  }
});

test('a sink on the 3-D view can name the field that sets it', () => {
  assert.equal(setByLine('mount'), 'Set by: mount to arm, W/K.');
  assert.equal(setByLine('end_face_magnet'), 'Set by: End faces.');
  assert.equal(setByLine('bore'), 'Set by: Rotor bore.');
  assert.equal(setByLine('slot_channels'), 'Set by: Frame.');
  // a path nobody sets must produce NOTHING, so the caller can concatenate it
  assert.equal(setByLine('gap'), '');
  for (const [id, key] of Object.entries(PARAM_BY_SINK)) {
    assert.ok(key === null || key in ROBOTICS_HELP, `${id} points at no control`);
  }
});
