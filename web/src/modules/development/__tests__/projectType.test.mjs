import { test } from 'node:test';
import assert from 'node:assert/strict';
import { projectTypeOf, projectTypePayload, PROJECT_TYPES } from '../projectType.ts';

test('a project without the key is a motor project; only "controller" makes a controller', () => {
  assert.equal(projectTypeOf({}), 'motor');
  assert.equal(projectTypeOf(null), 'motor');
  assert.equal(projectTypeOf({ project_type: 'controller' }), 'controller');
  assert.equal(projectTypeOf({ project_type: 'Controller' }), 'motor');
  assert.deepEqual([...PROJECT_TYPES], ['motor', 'controller']);
});

test('the payload carries the type only for a controller chosen by staff', () => {
  assert.deepEqual(projectTypePayload(true, 'controller'), { project_type: 'controller' });
  assert.deepEqual(projectTypePayload(true, 'motor'), {});
  assert.deepEqual(projectTypePayload(false, 'controller'), {});
});

test('the list groups Motors then Controllers, keeps the order inside a group and leaves an empty group out', async () => {
  const { groupByType } = await import('../projectType.ts');
  const rows = [{ id: 'c1', project_type: 'controller' }, { id: 'm1' }, { id: 'm2', project_type: 'motor' }, { id: 'c2', project_type: 'controller' }];
  assert.deepEqual(groupByType(rows).map((g) => [g.type, g.projects.map((p) => p.id)]), [['motor', ['m1', 'm2']], ['controller', ['c1', 'c2']]]);
  assert.deepEqual(groupByType([{ id: 'm' }]).map((g) => g.type), ['motor']);
  assert.deepEqual(groupByType([]), []);
});
