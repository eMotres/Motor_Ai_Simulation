// node --test: the Motors catalog by die (owner 2026-11-17) - grouping and the current / old rule (lib/catalogByDie.ts).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { buildByDie, familyOf, segmentationText } from '../catalogByDie.ts';

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
     ['100 mm mid', ['100 mm mid']]]);
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
  const m = buildByDie(dies, null).find((x) => x.family === '100 mm mid').blocks[0].motors[0];
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
