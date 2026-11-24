import test from 'node:test';
import assert from 'node:assert/strict';
import { componentName, hasFrameless, isFramelessPart, modelTree, notFrameless } from '../projectModelTree.ts';

const g = (name, label) => ({ name, family: name, explode: 0, ...(label ? { label } : {}) });
const assembly = [g('Part_1', 'Core:1'), g('Part_2', 'Rotor:1'), g('Part_3', 'Magnet:1'), g('Part_4', 'Magnet:43'), g('Part_5', 'Coil_01'), g('Part_6', 'Ring:2'),
  g('Part_7', 'Shaft1:1'), g('Part_8', 'Stator_holder:1'), g('Part_9', 'Stator_holder_back:1'), g('Part_10', 'w_61707-2rs1:2'),
  g('Part_11', 'Hexagon Socket Head Cap Screw DIN 912 - M2.5 x 0.45 x 20 Steel 4.6 Plain:3'), g('Part_12', 'Sealing_ring:1')];

test('the instance number is dropped, the name kept', () => {
  assert.equal(componentName('Magnet:43'), 'Magnet');
  assert.equal(componentName('Coil_07'), 'Coil');
  assert.equal(componentName('Stator_holder:1'), 'Stator holder');
  assert.equal(componentName('12'), '12');
});

test('frameless keeps stator, coils, rotor, magnets and rings only', () => {
  assert.deepEqual(assembly.filter(isFramelessPart).map((x) => x.name), ['Part_1', 'Part_2', 'Part_3', 'Part_4', 'Part_5', 'Part_6']);
  assert.deepEqual([...notFrameless(assembly)], ['Part_7', 'Part_8', 'Part_9', 'Part_10', 'Part_11', 'Part_12']);
});

test('the button is offered only when it changes something', () => {
  assert.equal(hasFrameless(assembly), true);
  assert.equal(hasFrameless(assembly.slice(0, 6)), false);       // already frameless
  assert.equal(hasFrameless(assembly.slice(6)), false);          // no active part: nothing would be left
  assert.equal(hasFrameless([g('Part_1'), g('Part_2')]), false); // a file without names
});

test('tree: one row per component, instances nested, the other parts removed in the frameless view', () => {
  const full = modelTree(assembly, false);
  assert.equal(full.find((r) => r.label === 'Magnet (2)').children.length, 2);
  assert.ok(full.some((r) => r.label === 'Stator holder'));
  assert.deepEqual(modelTree(assembly, true).map((r) => r.label), ['Core', 'Rotor', 'Magnet (2)', 'Coil', 'Ring']);
  assert.equal(modelTree([g('Part_1'), g('Part_2')], false), null);
});
