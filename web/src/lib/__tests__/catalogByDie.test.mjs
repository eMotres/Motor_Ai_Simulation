// node --test: the Motors catalog by die (owner 2026-11-17) - grouping and the current / old rule (lib/catalogByDie.ts).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { buildByDie, familyOf, ratedPoint, segmentationText } from '../catalogByDie.ts';

const cfg = (name, designation, extra = {}) => ({ name, designation, designation_complete: !String(designation ?? '?').includes('?'),
  family: designation ? designation.split('_')[0] : undefined, beside: { steel: 'NO20', magnet: 'N52' }, ...extra });
const dies = [
  { name: 'CILN28', created: '2026-08-01', slots: 24, poles: 28, stator_diameter: 200, configs: [cfg('G2-L40', 'CILN28 200_40 24F4SY')] },
  { name: 'CILN28 vadim', created: '2026-11-01', slots: 24, poles: 28, stator_diameter: 200,
    configs: [cfg('G2-L65-17t', 'CILN28 200_65 17F4SY'), cfg('G2-L40 copy', 'CILN28 200_40 24F4SY')] },
  { name: '100 mm mid', created: '2026-08-19', slots: 24, poles: 28, stator_diameter: 100, configs: [cfg('L15', 'CI????28 100_15 10F?Y', { designation_unknown: ['steel'] })] },
  { name: 'NOLINK A', stator_diameter: 40, configs: [cfg('L1', 'CIANO14 40_12 7F2SY')] },
  { name: 'NOLINK B', stator_diameter: 40, configs: [cfg('L1', 'CIANO14 40_12 7F2SY')] },
  { name: 'BIG', stator_diameter: 400, configs: [cfg('L90', 'CIANO56 400_90 30F4SY')] },
];
const overview = {
  items: { 'CILN28/G2-L40': { projects: [{ id: 'p1', link_status: 'current' }], productions: [], computed: true } },
  projects: { p1: { id: 'p1', title: 'Pump', status: 'design', stage: 'computed' } }, productions: {},
};

test('two laminations of one family sit under ONE family header; families by OD; the unnamed die last', () => {
  const g = buildByDie(dies, overview);
  assert.deepEqual(g.map((x) => [x.family, x.blocks.map((b) => b.die.name)]),
    [['CIANO14 40', ['NOLINK A', 'NOLINK B']], ['CILN28 200', ['CILN28', 'CILN28 vadim']], ['CIANO56 400', ['BIG']],
     ['', ['100 mm mid']]]);
  assert.equal(familyOf(dies[2]).named, false);
});

test('the linked motor is current, a copy with the same designation is old and sorted last', () => {
  const g = buildByDie(dies, overview).find((x) => x.family === 'CILN28 200');
  const vadim = g.blocks[1].motors;
  assert.deepEqual(vadim.map((m) => [m.label, m.old]), [['CILN28 200_65 17F4SY', false], ['CILN28 200_40 24F4SY', true]]);
  assert.equal(g.blocks[0].motors[0].linked, true);
  assert.equal(g.blocks[0].motors[0].projects[0].title, 'Pump');
});

test('without any link among the same names nothing is old; without the Overview (non-admin) nothing is linked', () => {
  const a = buildByDie(dies, overview).find((x) => x.family === 'CIANO14 40');
  assert.ok(a.blocks.every((b) => b.motors.every((m) => !m.old)));
  const plain = buildByDie(dies, null);
  assert.ok(plain.every((g) => g.blocks.every((b) => b.motors.every((m) => !m.old && !m.linked && !m.projects.length))));
});

test('an incomplete or missing name shows the stored name, marked', () => {
  const m = buildByDie(dies, null).find((x) => x.family === '').blocks[0].motors[0];
  assert.equal(m.complete, false);
  const none = buildByDie([{ name: 'D', configs: [{ name: 'L9', designation: null }] }], null)[0].blocks[0].motors[0];
  assert.deepEqual([none.label, none.named], ['L9', false]);
});

test('segmentation in one value', () => {
  const w = { solid: 'solid', slice: (mm) => `${mm} mm` };
  assert.equal(segmentationText(null, w), 'solid');
  assert.equal(segmentationText(0, w), 'solid');
  assert.equal(segmentationText(5, w), '5 mm');
});

// ── coordinator's browser findings on #731 ─────────────────────────────────────────────────────────────────────────
test('no link on any motor of one designation: the newest LOCKED one is current, the rest old; none locked: none', () => {
  const twin = (name, created, locked) => ({ name, created, stator_diameter: 30, configs: [cfg('L10', 'CIANO14 30_10 6F2SY', { locked })] });
  const rows = (ds) => buildByDie(ds, overview)[0].blocks.flatMap((b) => b.motors.map((m) => [m.die, m.old, m.newestLocked]));
  assert.deepEqual(rows([twin('A', '2026-08-19', true), twin('B', '2026-09-01', false), twin('C', '2026-08-20', true)]),
    [['C', false, true], ['B', true, false], ['A', true, false]]);            // C: newest locked, its block first; the old ones newest first
  assert.deepEqual(rows([twin('A', '2026-08-19', false), twin('B', '2026-09-01', false)]).map((r) => r[1]), [false, false]);
  // without the Overview (non-admin) the links are unknown: no fallback, nothing old
  assert.ok(buildByDie([twin('A', '2026-08-19', true), twin('C', '2026-08-20', true)], null)[0].blocks.every((b) => b.motors.every((m) => !m.old && !m.newestLocked)));
});

test('every die the generator cannot name sits in ONE group at the end; a server family names a die without motors', () => {
  const g = buildByDie([{ name: 'X1', configs: [] }, { name: 'X2', configs: [cfg('L', 'CI????28 1_1 1F?Y')] },
    { name: 'CIANO10 12_40 Ø12 12s10p', family: 'CIANO10 12', stator_diameter: 12, configs: [] }], null);
  assert.deepEqual(g.map((x) => [x.family, x.named, x.blocks.length]), [['CIANO10 12', true, 1], ['', false, 2]]);
  assert.deepEqual(familyOf({ name: 'D', family: null, configs: [] }), { family: '', named: false });
});

test('the row quotes the rated point, else the largest continuous point - never the first water point', () => {
  const d = (name, power_kw, extra = {}) => ({ name, power_kw, torque_nm: power_kw * 2, ...extra });
  assert.equal(ratedPoint({ name: 'c', duties: [d('water 1', 0.01), d('rated', 1.2), d('peak', 3)] }).name, 'rated');
  assert.equal(ratedPoint({ name: 'c', duties: [d('water 1', 0.01), d('rated 120C', 1.1)] }).name, 'rated 120C');
  assert.equal(ratedPoint({ name: 'c', duties: [d('water 1', 0.01), d('water 2', 0.9), d('boost', 5, { duty_class: 'short_time' })] }).name, 'water 2');
  assert.equal(ratedPoint({ name: 'c', duties: [] }), null);
});

test('row values: the rated point, battery nominal (or cells x nominal cell voltage), ripple below the preset flagged', async () => {
  const { rowValues, RIPPLE_PRESET } = await import('../catalogByDie.ts');
  assert.deepEqual(RIPPLE_PRESET, { steps: 96, gapLayers: 2 });
  const d = { name: 'rated', power_kw: 1.11, torque_nm: 0.82, rpm: 13000, current_arms: 37,
    result: { efficiency_pct: 92.8, ripple_pct: 5, mass_kg: 0.6 }, runs: [{ primary: true, steps: 96, gap_layers: 2 }] };
  const v = rowValues({ name: 'L20', battery: { cells: 12, v_cell_nom: 3.7, v_min: 36, v_max: 50.4 } }, d);
  assert.equal(v.voltage.kind, 'battery'); assert.ok(Math.abs(v.voltage.v - 44.4) < 1e-9);
  assert.equal(v.ripple_low, null);
  assert.deepEqual(rowValues({ name: 'L' }, { ...d, runs: [{ primary: true, steps: 48, gap_layers: 2 }] }).ripple_low, { steps: 48, gap_layers: 2 });
  assert.deepEqual(rowValues({ name: 'L' }, { ...d, runs: [{ primary: true, steps: 96, gap_layers: 1 }] }).ripple_low, { steps: 96, gap_layers: 1 });
  assert.equal(rowValues({ name: 'L' }, { ...d, runs: [] }).ripple_low, null);            // unknown fidelity: no mark, no guess
  assert.deepEqual(rowValues({ name: 'L' }, { ...d, result: { v_ll_peak_v: 48.7 } }).voltage, { kind: 'vll_peak', v: 48.7 });
  assert.equal(rowValues({ name: 'L' }, null), null);
});

test('table columns: values per column, header-click sorting inside a die block, missing values last', async () => {
  const { COLS, colValue, nextSort, sortMotors } = await import('../catalogByDie.ts');
  assert.deepEqual(COLS.map((c) => c.key), ['power', 'torque', 'speed', 'eff', 'ripple', 'volt', 'current', 'cogging', 'mass']);
  const row = (p, cog) => ({ id: p, cog, values: { power_kw: p, torque_nm: 1, rpm: 2, efficiency_pct: 3, ripple_pct: 4, voltage: { kind: 'battery', v: 5 },
    current_arms: 6, mass_kg: 7 } });
  const r = row(1.5);
  assert.deepEqual(['power', 'torque', 'speed', 'eff', 'ripple', 'volt', 'current', 'mass'].map((k) => colValue(k, r)), [1.5, 1, 2, 3, 4, 5, 6, 7]);
  assert.equal(colValue('cogging', r, 0.07), 0.07); assert.equal(colValue('power', { values: null }), null);
  const ms = [row(1), row(3), { id: 'none', values: null }, row(2)];
  assert.deepEqual(sortMotors(ms, 'power', 'desc').map((x) => x.id), [3, 2, 1, 'none']);
  assert.deepEqual(sortMotors(ms, 'power', 'asc').map((x) => x.id), [1, 2, 3, 'none']);
  assert.deepEqual(sortMotors([row(1, 0.2), row(2, null), row(3, 0.1)], 'cogging', 'asc', (x) => x.cog).map((x) => x.id), [3, 1, 2]);
  assert.deepEqual(nextSort(null, 'power'), { key: 'power', dir: 'desc' });
  assert.deepEqual(nextSort({ key: 'power', dir: 'desc' }, 'power'), { key: 'power', dir: 'asc' });
  assert.equal(nextSort({ key: 'power', dir: 'asc' }, 'power'), null);
  assert.deepEqual(nextSort({ key: 'power', dir: 'asc' }, 'mass'), { key: 'mass', dir: 'desc' });
});
