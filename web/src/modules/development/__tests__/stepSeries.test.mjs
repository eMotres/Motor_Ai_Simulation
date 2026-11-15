// The optimization-steps view: short step labels, the "how a value changed over the steps" series and the Configure tile names.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dieShortNames, stepLabels } from '../stepSeries.ts';
import { configureTileKey, massKind, thermalTiles } from '../pointsLimited.ts';

const point = (duty, cmp, op = {}) => ({ duty, has_result: true, comparison: cmp, operating_point: op });
const variant = (step, die, config, points) => ({ step, die, canonical_die: die, config, status: 'current', note: 'n', points });

const variants = [
  variant(1, 'Erium 85 opt1', 'L80-2x3ph', [point('cruise', { torque_nm: 800, efficiency_pct: 90, mass_kg: 30 }, { current_arms: 60 })]),
  variant(2, 'Erium 85 opt2', 'L80-2x3ph', [point('cruise', { torque_nm: 810, mass_kg: 29.5 }, { current_arms: 62 })]),
  variant(3, 'Erium 85 opt3', 'L80-2x3ph', [point('cruise', { torque_nm: 818.84, efficiency_pct: 91, mass_kg: 29.14 }, { current_arms: 64 }), point('peak', { torque_nm: 1500 })]),
];

test('step chips: the shortest die token that no other die has, then the configuration', () => {
  assert.deepEqual(stepLabels(variants), ['opt1 · L80-2x3ph', 'opt2 · L80-2x3ph', 'opt3 · L80-2x3ph']);
  // one die for every step: no unique token, so the last token of the name
  assert.deepEqual(stepLabels([variant(1, 'Erium 85 opt3', 'a', []), variant(2, 'Erium 85 opt3', 'b', [])]), ['opt3 · a', 'opt3 · b']);
  assert.equal(dieShortNames(['A', 'B']).get('A'), 'A');
  // the canonical die decides when the alias differs
  assert.equal(stepLabels([{ die: 'alias', canonical_die: 'Erium opt9', config: 'c' }])[0], 'opt9 · c');
});

test('tile names are the ordinary Configure ones; the mass names follow the mass stamp (active unless stamped full)', () => {
  const active = { comparison: { mass_partial: true } };
  const full = { comparison: { mass_partial: false } };
  assert.equal(massKind(active), 'active');
  assert.equal(massKind({ comparison: {} }), 'active');             // no stamp: never claim a full mass
  assert.equal(massKind(full), 'full');
  assert.equal(configureTileKey('mass', active), 'configure.massPartial');
  assert.equal(configureTileKey('mass', full), 'configure.mass');
  assert.equal(configureTileKey('torqueMass', active), 'configure.tPerMassActive');
  assert.equal(configureTileKey('torqueMass', full), 'configure.tPerMass');
  assert.equal(configureTileKey('losses'), 'configure.totalLoss');
  assert.equal(configureTileKey('vLine'), 'configure.vLinePeak');
  assert.equal(configureTileKey('pSolid'), null);                   // not a Configure tile: the caller keeps its own wording
});

test('the thermal block carries no temperature or torque tile of the panel: one place for them', () => {
  const t = { duty_temps: { kind: 'coupled', winding_c: 134, magnet_c: 70, em: { torque_nm: 865 } } };
  assert.deepEqual(thermalTiles(t), []);
});

test('wiring: chart right of the tiles on wide screens, stacked on narrow ones; no compare control, no step note, one source chip', () => {
  const src = readFileSync(new URL('../../../components/compare/PointsConfigure.tsx', import.meta.url), 'utf8');
  assert.doesNotMatch(src, /StepsChart|showChart/);                    // the "change over the steps" chart is gone (owner 2026-11-13)
  assert.match(src, /<ResultsPanel panel=\{panel\}/);                 // the one panel carries the one source chip (components/common/ResultsPanel)
  assert.match(readFileSync(new URL('../../../components/common/ResultsPanel.tsx', import.meta.url), 'utf8'), /data-testid="em-source-chip"/);
  assert.doesNotMatch(src, /compareWith|baseVariantPoint|points-note|steps\[stepIndex\]\?\.note/);
  assert.match(src, /spec\.group !== 'thermal'/);                    // temperatures sit in the thermal block with their own source
});
