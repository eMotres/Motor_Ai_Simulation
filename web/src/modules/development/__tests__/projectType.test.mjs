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
