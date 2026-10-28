// node --test: which motor picture an overview tile shows (owner 2026-10-28). Imports cardMotor.ts itself (Node >= 22.18 / 24).
import test from 'node:test';
import assert from 'node:assert/strict';
import { dieKey, indexDies, productionCardMotor, projectCardMotor } from '../cardMotor.ts';

const tree = [
  { name: 'CILN28', slots: 24, poles: 20, thumb_svg: '<svg/>' },
  { name: 'Ciano14_40', slots: 12, poles: 14, thumb_svg: null },
  { name: 'Broken', slots: 0, poles: 'x' },
  { slots: 12, poles: 10 },
  null,
];
const index = indexDies(tree);

test('the index keeps dies with usable slot and pole counts, matched case-insensitively', () => {
  assert.equal(index.size, 2);
  assert.equal(dieKey('  CILN28 '), 'ciln28');
  assert.deepEqual(index.get('ciln28'), { name: 'CILN28', slots: 24, poles: 20, thumbSvg: '<svg/>' });
  assert.equal(index.get('ciano14_40').thumbSvg, undefined);
  assert.equal(indexDies(null).size, 0);
  assert.equal(indexDies(undefined).size, 0);
});

test('a production shows the picture of its die; the label is die / config', () => {
  const m = productionCardMotor({ die: 'ciln28', config: 'G2-L40' }, index);
  assert.equal(m.label, 'ciln28 / G2-L40');
  assert.equal(m.slots, 24);
});

test('controllers, unknown dies and a tree that is not read give no picture', () => {
  assert.equal(productionCardMotor({ die: 'CILN28', config: 'G2-L40', product_type: 'electronics' }, index), null);
  assert.equal(productionCardMotor({ die: 'Controller 60-80V', config: 'R43' }, index), null);
  assert.equal(productionCardMotor({ die: 'CILN28', config: 'G2-L40' }, null), null);
});

test('a project shows its newest linked variant that the catalog knows', () => {
  const linked = [{ die: 'Ciano14_40', config: 'A' }, { die: 'CILN28', config: 'G1' }, { die: 'CILN28', config: 'G2-L40' }];
  assert.equal(projectCardMotor(linked, index).label, 'CILN28 / G2-L40');
  // the newest one is not in the catalog (no access): fall back to the next older
  assert.equal(projectCardMotor([{ die: 'Ciano14_40', config: 'A' }, { die: 'Secret', config: 'X' }], index).label, 'Ciano14_40 / A');
});

test('a project with nothing linked (or nothing known) has no picture', () => {
  assert.equal(projectCardMotor([], index), null);
  assert.equal(projectCardMotor(undefined, index), null);
  assert.equal(projectCardMotor([{ die: 'Secret', config: 'X' }], index), null);
  assert.equal(projectCardMotor([{ die: 'CILN28', config: 'G1' }], null), null);
});
