import test from 'node:test';
import assert from 'node:assert/strict';
import { controlsFor, treeStartsCollapsed } from '../internal/toolbarSpec.ts';

test('all four hosts use the same toolbar controls by role', () => {
  const expected = ['navigation', 'cube', 'fit', 'home', 'projection'];
  for (const host of ['geometryActive', 'geometryFull', 'materialsFull', 'model3d']) {
    assert.deepEqual(controlsFor('simplified'), expected, `${host} simplified`);
    assert.deepEqual(controlsFor('full'), [...expected, 'tree', 'section', 'explode', 'measure'], `${host} full`);
  }
});

test('shared tree begins collapsed below the viewer breakpoint', () => {
  assert.equal(treeStartsCollapsed(899), true);
  assert.equal(treeStartsCollapsed(900), false);
});
