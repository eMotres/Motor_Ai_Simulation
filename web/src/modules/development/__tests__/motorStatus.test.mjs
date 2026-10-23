import { test } from 'node:test';
import assert from 'node:assert/strict';
import { acceptedProjectStatus, cancelProjectStatus, pendingStatusForProject, proposeProjectStatus, visibleProjectVersions } from '../motorStatus.ts';

test('current-only hides trials and history; show-all includes both', () => {
  const motors = [{ die: 'A', config: 'current' }, { die: 'A', config: 'trial', status: 'trial' }];
  const snapshots = [{ id: 'snap-1', kind: 'history', die: 'A', config: 'old' }];
  const currentOnly = visibleProjectVersions(motors, snapshots, false);
  assert.deepEqual(currentOnly.map((motor) => motor.config), ['current']);
  const all = visibleProjectVersions(motors, snapshots, true);
  assert.deepEqual(all.map((motor) => motor.config), ['current', 'trial', 'old']);
  assert.equal(all[2].kind, 'history');
});

test('project status remains pending until explicitly accepted and only for matching project state', () => {
  const pending = proposeProjectStatus('p1', 'design', 'simulation');
  assert.deepEqual(pending, { projectId: 'p1', current: 'design', proposed: 'simulation' });
  assert.equal(acceptedProjectStatus(pending, 'p1', 'design'), 'simulation');
  assert.equal(acceptedProjectStatus(pending, 'p2', 'design'), null);
  assert.equal(acceptedProjectStatus(pending, 'p1', 'closed'), null);
  assert.equal(pendingStatusForProject(pending, 'p2'), null);
  const cancelled = cancelProjectStatus(pending);
  assert.equal(cancelled, null);
  assert.equal(pendingStatusForProject(cancelled, 'p1'), null);
  assert.equal(proposeProjectStatus('p1', 'design', 'design'), null);
});
