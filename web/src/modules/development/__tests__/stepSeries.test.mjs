// The optimization-steps view: short step labels, the "how a value changed over the steps" series and the Configure tile names.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { SERIES, availableSeries, dieShortNames, pickSeries, stepLabels, stepSeries } from '../stepSeries.ts';
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

test('the series is the chosen value of the selected mode at every step; a missing value is a gap, never 0', () => {
  const torque = SERIES.find((s) => s.id === 'torque');
  assert.deepEqual(stepSeries(variants, 'cruise', torque).map((p) => p.value), [800, 810, 818.84]);
  const eff = SERIES.find((s) => s.id === 'efficiency');
  assert.deepEqual(stepSeries(variants, 'cruise', eff).map((p) => p.value), [90, null, 91]);          // step 2 stores none: a gap
  assert.deepEqual(stepSeries(variants, 'peak', torque).map((p) => p.value), [null, null, 1500]);     // the mode exists only at step 3
  const cur = SERIES.find((s) => s.id === 'current');
  assert.deepEqual(stepSeries(variants, 'cruise', cur).map((p) => p.value), [60, 62, 64]);
  assert.equal(stepSeries(variants, null, torque).every((p) => p.value === null), true);
});

test('the switch offers only values stored for at least two steps of the selected mode', () => {
  const ids = (duty) => availableSeries(variants, duty).map((s) => s.id);
  assert.deepEqual(ids('cruise'), ['torque', 'efficiency', 'current', 'mass']);
  assert.deepEqual(ids('peak'), []);                                  // one step only: no chart for it
  assert.equal(pickSeries(availableSeries(variants, 'cruise'), 'mass').id, 'mass');
  assert.equal(pickSeries(availableSeries(variants, 'cruise'), 'ripple').id, 'torque');   // remembered value not offered: the first one
  assert.equal(pickSeries([], 'torque'), null);
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

test('the coupled loop torque is its own thermal tile, never merged with the EM run tiles', () => {
  const t = { duty_temps: { kind: 'coupled', winding_c: 134, magnet_c: 70, em: { torque_nm: 865 } } };
  const byId = Object.fromEntries(thermalTiles(t).map((x) => [x.id, x.value]));
  assert.equal(byId.emTorque, 865);
  assert.equal(byId.winding, 134);
});

test('wiring: chart right of the tiles on wide screens, stacked on narrow ones; no compare control, no step note, one source chip', () => {
  const src = readFileSync(new URL('../../../components/compare/PointsConfigure.tsx', import.meta.url), 'utf8');
  assert.match(src, /gridTemplateColumns: \{ xs: 'minmax\(0,1fr\)', lg: showChart && showTiles \?/);
  assert.match(src, /<StepsChart /);
  assert.match(src, /data-testid="em-source-chip"/);
  assert.doesNotMatch(src, /compareWith|baseVariantPoint|points-note|steps\[stepIndex\]\?\.note/);
  assert.match(src, /spec\.group !== 'thermal'/);                    // temperatures sit in the thermal block with their own source
  const chart = readFileSync(new URL('../../../components/compare/StepsChart.tsx', import.meta.url), 'utf8');
  assert.match(chart, /from 'recharts'/);
  assert.match(chart, /connectNulls=\{false\}/);
});

test('the current series needs a real result at that step: a duty that only has its input current is a gap, not a value', () => {
  const variants = [
    { step: 1, die: 'D1', canonical_die: 'D1', config: 'a', points: [{ duty: 'peak', operating_point: { rpm: 1000, current_arms: 336 }, comparison: { torque_nm: 800 } }] },
    { step: 2, die: 'D2', canonical_die: 'D2', config: 'a', points: [{ duty: 'peak', operating_point: { rpm: 1000, current_arms: 300 }, comparison: {} }] },
    { step: 3, die: 'D3', canonical_die: 'D3', config: 'a', points: [{ duty: 'peak', operating_point: { rpm: 1000, current_arms: 280 }, comparison: { torque_nm: 760 } }] },
  ];
  const current = SERIES.find((s) => s.id === 'current');
  assert.deepEqual(stepSeries(variants, 'peak', current).map((p) => p.value), [336, null, 280]);
});
