// node --test: which projects a die / configuration is linked to ("Back to project" in Configure without a remembered project).
import test from 'node:test';
import assert from 'node:assert/strict';
import { projectsOfMotor } from '../projectFocus.ts';

const projects = [
  { id: 'p1', title: 'One', linked_motors: [{ die: 'Die A', config: 'c1' }] },
  { id: 'p2', title: 'Two', linked_motors: [{ die: 'die a', config: 'C1' }, { die: 'Die B', config: 'c2' }] },
  { id: 'p3', title: 'Three', linked_motors: [{ die: 'Die A', config: 'c2' }] },
  { id: 'p4', title: 'Four' },
];

test('every project that links the die + configuration, case-insensitive, in list order', () => {
  assert.deepEqual(projectsOfMotor(projects, 'DIE A', 'c1').map((p) => p.id), ['p1', 'p2']);
  assert.deepEqual(projectsOfMotor(projects, 'Die A', 'c2').map((p) => p.id), ['p3']);
});
test('the canonical die counts as well', () => {
  assert.deepEqual(projectsOfMotor(projects, 'alias', 'c2', 'Die B').map((p) => p.id), ['p2']);
});
test('no configuration, no die or no link: nothing', () => {
  assert.deepEqual(projectsOfMotor(projects, 'Die A', ''), []);
  assert.deepEqual(projectsOfMotor(projects, '', 'c1'), []);
  assert.deepEqual(projectsOfMotor(projects, 'Die Z', 'c1'), []);
});
