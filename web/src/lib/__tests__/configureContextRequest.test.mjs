import test from 'node:test';
import assert from 'node:assert/strict';

const G = await import('../configureContextRequest.ts');

test('a delayed response for the previous motor cannot replace the selected motor context', async () => {
  const guard = new G.ConfigureContextRequestGuard();
  const scope14 = { motorId: 'CIANO14-40', identity: 'owner@example.test' };
  const scope85 = { motorId: 'CIANO28-85', identity: 'owner@example.test' };
  const oldRequest = guard.begin(scope14);
  let resolveOld;
  const oldResponse = new Promise((resolve) => { resolveOld = resolve; });

  const newRequest = guard.begin(scope85);
  assert.equal(guard.accepts(newRequest, scope85, scope85.identity, 'CIANO28-85'), true);
  resolveOld({ motor_id: 'CIANO14-40', presets: [{ config: 'L12' }, { config: 'L20' }] });
  const delayed = await oldResponse;

  assert.equal(guard.accepts(oldRequest, scope85, scope85.identity, delayed.motor_id), false);
  assert.equal(guard.accepts(newRequest, scope85, scope85.identity, delayed.motor_id), false);
});

test('a superseded refresh for the same motor cannot overwrite the newer response', () => {
  const guard = new G.ConfigureContextRequestGuard();
  const scope = { motorId: 'CIANO28-85', identity: 'owner@example.test' };
  const older = guard.begin(scope);
  const latest = guard.begin(scope);

  assert.equal(guard.accepts(latest, scope, scope.identity, scope.motorId), true);
  assert.equal(guard.accepts(older, scope, scope.identity, scope.motorId), false);
});

test('account changes, response identity mismatch, and unmount invalidation discard the response', () => {
  const guard = new G.ConfigureContextRequestGuard();
  const scope = { motorId: 'CIANO28-85', identity: 'first@example.test' };
  const request = guard.begin(scope);

  assert.equal(guard.accepts(request, scope, 'second@example.test', scope.motorId), false);
  assert.equal(guard.accepts(request, { ...scope, identity: 'second@example.test' }, 'second@example.test', scope.motorId), false);
  guard.invalidate();
  assert.equal(guard.accepts(request, scope, scope.identity, scope.motorId), false);
});

test('an unexpected response motor is never admitted into the selected context', () => {
  const guard = new G.ConfigureContextRequestGuard();
  const scope = { motorId: 'CIANO28-85', identity: 'owner@example.test' };
  const request = guard.begin(scope);
  assert.equal(guard.accepts(request, scope, scope.identity, 'CIANO14-40'), false);
});

test('stored context for the prior account is hidden immediately even when motor id is unchanged', () => {
  const stored = { motor_id: 'CIANO28-85', presets: [{ config: 'L12' }, { config: 'L20' }] };
  assert.equal(G.contextForIdentity('first@example.test', 'second@example.test', stored), null);
  assert.deepEqual(G.contextForIdentity('second@example.test', 'second@example.test', stored), stored);
});
