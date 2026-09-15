/**
 * The Part tree's vocabulary: name → swatch, name → heading.
 *
 * User 2026-09-07, on a thermal map whose slot was white around the wire bars:
 * *"надо рисовать изоляцию и покрытие провода, а то пустое место, и воздух тоже
 * показывать — он же входит в расчёт, и в дереве отображать их тоже нужно"*.
 * The last clause is what this file tests.  The backend now sends five more
 * domains (insulation, wire enamel, wire coating, air gap, pocket air) and the tree
 * turns each of them into a row — which means two pure string functions decide
 * whether the feature works: `partColour` and `partGroup` in
 * `common/fieldOutput.ts`, plus the `NOT_A_PART` filter beside them.
 *
 * They are the whole visual contract and they are ORDER-SENSITIVE — "wire
 * enamel" must not fall into the copper branch on the word "wire", "pocket air"
 * must not fall into the rotor branch, "air gap" must no longer be filtered out
 * as anonymous air — so a regex reordered by accident is exactly the change this
 * catches.
 *
 * The bodies below are copied VERBATIM from the shipped modules rather than
 * imported: those are TypeScript and pull in `three` and `import.meta.env`,
 * neither of which `node --test` can load.  What is pinned here is therefore the
 * BEHAVIOUR the mapping must keep; changing it means changing this file too,
 * and that is the moment someone has to justify the new colours.
 */
import test from 'node:test';
import assert from 'node:assert/strict';

/* ── verbatim copy of lib/partColors.ts ──────────────────────────────────── */

const PART_COLORS = {
  statorIron: '#42526b',
  rotorIron: '#394860',
  shaft: '#2b3648',
  sleeve: '#1f2937',
  magnetN: '#e02718',
  magnetS: '#2e86ff',
  copper: '#e0821a',
  copperPhases: ['#e0821a', '#d2491a', '#b8860b'],
  slotLiner: '#16a34a',
  enamel: '#d97706',
  inBand: '#22c55e',
  outBand: '#a855f7',
  slotFill: '#efe4c4',
  gapAir: '#a5e8ef',
  pocketAir: '#cbd5e1',
};

/* ── verbatim copies of common/fieldOutput.ts ────────────────────────────── */

const NOT_A_PART = new Set(['air', 'gap', 'band', 'outer']);

function partGroup(name) {
  const s = name.toLowerCase();
  if (/^air gap$/.test(s)) return 'Other';
  if (/liner|enamel|insulation|coating/.test(s)) return 'Stator';
  if (/pocket/.test(s)) return 'Rotor';
  if (/rotor|magnet|sleeve|shaft/.test(s)) return 'Rotor';
  if (/stator|coil|copper|winding/.test(s)) return 'Stator';
  return 'Other';
}

function partColour(name) {
  const s = name.toLowerCase();
  if (/wire coating|impregnation/.test(s)) return PART_COLORS.slotFill;
  if (/pocket air/.test(s))            return PART_COLORS.pocketAir;
  if (/air gap|gap air/.test(s))       return PART_COLORS.gapAir;
  if (/enamel/.test(s))                return PART_COLORS.enamel;
  if (/liner|insulation/.test(s))      return PART_COLORS.slotLiner;
  if (/sleeve|bandage|retain/.test(s)) return PART_COLORS.sleeve;
  if (/shaft/.test(s))                 return PART_COLORS.shaft;
  if (/magnet/.test(s))                return PART_COLORS.magnetN;
  if (/coil|copper|wind/.test(s))      return PART_COLORS.copper;
  if (/stator/.test(s))                return PART_COLORS.statorIron;
  if (/rotor/.test(s))                 return PART_COLORS.rotorIron;
  return PART_COLORS.statorIron;
}

/* The names the backend actually sends (routes/thermal.py PART_NAMES). */
const THERMAL_NAMES = ['insulation', 'wire enamel', 'wire coating', 'air gap',
                       'pocket air'];

/* ── the five new domains are PARTS ──────────────────────────────────────── */

test('every named thermal domain survives the "air is not a part" filter', () => {
  // This is the feature: they are solved domains with their own conductivity
  // and their own temperature, so each has to become a row with an eye toggle.
  for (const n of THERMAL_NAMES) {
    assert.ok(!NOT_A_PART.has(n), `${n} was filtered out of the tree`);
  }
});

test('anonymous air is still not a part', () => {
  // The EM views paint the whole outside of the machine as one "air" class with
  // no field worth reading; offering it as a row would be a toggle for nothing.
  for (const n of ['air', 'gap', 'band', 'outer']) {
    assert.ok(NOT_A_PART.has(n), n);
  }
});

/* ── colours ─────────────────────────────────────────────────────────────── */

test('each thermal domain gets its own swatch', () => {
  const seen = THERMAL_NAMES.map(partColour);
  assert.equal(new Set(seen).size, THERMAL_NAMES.length,
    `two domains share a colour: ${JSON.stringify(seen)}`);
});

test('the liner and the enamel keep the colours the 3-D tree gives them', () => {
  // User 2026-09-06: the field viewer's tree IS the 3-D component tree.  One
  // part in two colours in two trees is the bug that rule exists to prevent —
  // `viewer3d/ComponentTree` already lists "Insulation" and "Wire enamel".
  assert.equal(partColour('insulation'), PART_COLORS.slotLiner);
  assert.equal(partColour('wire enamel'), PART_COLORS.enamel);
});

test('the air domains are the pale ones', () => {
  assert.equal(partColour('wire coating'), PART_COLORS.slotFill);
  assert.equal(partColour('air gap'), PART_COLORS.gapAir);
  assert.equal(partColour('pocket air'), PART_COLORS.pocketAir);
});

test('the specific names win over the generic branches they contain', () => {
  // Each of these would land somewhere else if the order were shuffled:
  //   "wire enamel"  → copper   (the /wind/ branch matches "wire"? no — but
  //                              /coil|copper|wind/ is close enough that the
  //                              enamel test has to come first anyway)
  //   "pocket air"   → nothing, then the statorIron default
  //   "air gap"      → the statorIron default
  //   "wire coating"    → the statorIron default
  assert.notEqual(partColour('wire enamel'), PART_COLORS.copper);
  assert.notEqual(partColour('pocket air'), PART_COLORS.rotorIron);
  assert.notEqual(partColour('air gap'), PART_COLORS.statorIron);
  assert.notEqual(partColour('wire coating'), PART_COLORS.statorIron);
});

test('the metals did not change colour when the air arrived', () => {
  // The regression that matters most: this function serves the EM and
  // mechanical trees too, and five new branches were prepended to it.
  assert.equal(partColour('stator iron'), PART_COLORS.statorIron);
  assert.equal(partColour('rotor iron'), PART_COLORS.rotorIron);
  assert.equal(partColour('copper'), PART_COLORS.copper);
  assert.equal(partColour('magnet'), PART_COLORS.magnetN);
  assert.equal(partColour('shaft'), PART_COLORS.shaft);
  assert.equal(partColour('sleeve'), PART_COLORS.sleeve);
  assert.equal(partColour('retaining sleeve'), PART_COLORS.sleeve);
  assert.equal(partColour('coil'), PART_COLORS.copper);
  assert.equal(partColour('rotor'), PART_COLORS.rotorIron);
  assert.equal(partColour('stator'), PART_COLORS.statorIron);
});

/* ── headings ────────────────────────────────────────────────────────────── */

test('the slot insulation is listed with the stator it insulates', () => {
  assert.equal(partGroup('insulation'), 'Stator');
  assert.equal(partGroup('wire enamel'), 'Stator');
  assert.equal(partGroup('wire coating'), 'Stator');
});

test('the pocket air is listed with the rotor it is trapped in', () => {
  assert.equal(partGroup('pocket air'), 'Rotor');
});

test('the air gap belongs to neither side — that is what a gap is', () => {
  assert.equal(partGroup('air gap'), 'Other');
});

test('the metals keep their headings', () => {
  assert.equal(partGroup('stator iron'), 'Stator');
  assert.equal(partGroup('copper'), 'Stator');
  assert.equal(partGroup('rotor iron'), 'Rotor');
  assert.equal(partGroup('magnet'), 'Rotor');
  assert.equal(partGroup('sleeve'), 'Rotor');
  assert.equal(partGroup('shaft'), 'Rotor');
});
