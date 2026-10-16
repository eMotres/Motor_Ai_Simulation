import test from 'node:test';
import assert from 'node:assert/strict';
import { hiddenKeysByDefault, hiddenSceneNames, selectedKeyForScene, selectedSceneNames } from '../internal/treeState.ts';

const rows = [
  { key: 'coils', sceneNames: ['g:winding'], children: [
    { key: 'coil_0', sceneNames: ['coil:0'] }, { key: 'coil_1', sceneNames: ['coil:1'], defaultVisible: false },
  ] },
  { key: 'shaft', sceneNames: ['Shaft'] },
];

test('default hidden state walks all nested rows', () => {
  assert.deepEqual([...hiddenKeysByDefault(rows)], ['coil_1']);
});

test('a hidden ancestor hides descendants even when their own keys are visible', () => {
  const hidden = new Set(['coils']);
  assert.deepEqual([...hiddenSceneNames(rows, hidden)].sort(), ['coil:0', 'coil:1', 'g:winding'].sort());
});

test('scene selection prefers the deepest matching row for host part assignment', () => {
  assert.equal(selectedKeyForScene(rows, 'coil:0'), 'coil_0');
  assert.deepEqual([...selectedSceneNames(rows, 'coils')].sort(), ['coil:0', 'coil:1', 'g:winding'].sort());
  assert.deepEqual([...selectedSceneNames(rows, 'coil_0')], ['coil:0']);
  assert.equal(selectedKeyForScene(rows, 'Shaft'), 'shaft');
});
