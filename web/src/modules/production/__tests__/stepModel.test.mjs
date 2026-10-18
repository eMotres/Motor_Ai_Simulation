// node --test — which model groups an assembly step shows in its 3-D view (helpers.ts: partFamilies, stepModelFamilies).
// Imports helpers.ts itself (Node >= 22.18 / 24; else skipped). No network.
import test from 'node:test';
import assert from 'node:assert/strict';

let H = null;
try { H = await import('../helpers.ts'); } catch { /* old Node */ }
const t = H ? test : test.skip;

const part = (id, en) => ({ id, name: { en, zh: en } });
const step = (id, after, names) => ({ id, n: 0, title: { en: id, zh: id }, kind: 'operation', parts: [], after, note: null,
  parts_resolved: names.map((n, i) => part(`${id}_${i}`, n)) });

const steps = [
  step('s1', [], ['Coil 40_12']),
  step('s2', ['s1'], []),                               // a check without parts
  step('s5', [], ['Magnet 40_12', 'Rotor core 40_12']),
  step('s7', [], ['Shaft 40_12']),
  step('s9', ['s2', 's5', 's7'], ['Front holder 40', 'Bearing 618/8-2Z C3', 'Bearing screw', 'Lead wire']),
  step('s10', ['s9'], ['Phase board 40 rev C']),
];

t('part names map to model families', () => {
  assert.deepEqual(H.partFamilies('Coil 40_12'), ['Coils']);
  assert.deepEqual(H.partFamilies('Coil-fixing ring 40'), ['Ring_front', 'Ring_back']);
  assert.deepEqual(H.partFamilies('Bearing 618/8-2Z C3'), ['Bearings_front', 'Bearings_back']);
  assert.deepEqual(H.partFamilies('Phase board 40 rev C'), ['PhaseBoard']);
  assert.deepEqual(H.partFamilies('Lead wire'), []);
});

t('a step shows its own families and, ghosted, those of every earlier step', () => {
  const r = H.stepModelFamilies(steps, 's9');
  assert.deepEqual(r.own, ['Holder_front', 'Bearings_front', 'Bearings_back']);
  assert.deepEqual([...r.context].sort(), ['Coils', 'Magnets', 'Rotor', 'Shaft']);
});

t('a step without parts shows the cumulative assembly up to it', () => {
  assert.deepEqual(H.stepModelFamilies(steps, 's2'), { own: [], context: ['Coils'] });
});

t('ancestors are transitive and a family the step adds is not repeated as context', () => {
  const r = H.stepModelFamilies(steps, 's10');
  assert.deepEqual(r.own, ['PhaseBoard']);
  assert.ok(r.context.includes('Coils') && r.context.includes('Holder_front') && !r.context.includes('PhaseBoard'));
});

t('an unknown step or a cycle gives a finite answer', () => {
  assert.deepEqual(H.stepModelFamilies(steps, 'nope'), { own: [], context: [] });
  const loop = [step('a', ['b'], ['Coil 1']), step('b', ['a'], ['Shaft 1'])];
  assert.deepEqual(H.stepModelFamilies(loop, 'a'), { own: ['Coils'], context: ['Shaft'] });
});
