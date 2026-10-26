// The magnetic-pull control → query parameters (2026-10-26): `none` adds
// nothing to the request, `em` names the source, `uniform` needs a valid p.
import test from 'node:test';
import assert from 'node:assert/strict';
import { pullMpaError, pullParams } from '../mechPull.ts';

test('pull none sends nothing — the request stays the one the app always made', () => {
  assert.deepEqual(pullParams('none', ''), {});
  assert.deepEqual(pullParams('none', '0.7'), {});
});

test('pull em names the source and nothing else', () => {
  assert.deepEqual(pullParams('em', '0.7'), { pull: 'em' });
});

test('pull uniform sends the pressure and refuses an empty or out-of-range box', () => {
  assert.deepEqual(pullParams('uniform', ' 0.70 '), { pull: 'uniform', pull_mpa: 0.7 });
  assert.equal(pullMpaError('0.7'), null);
  assert.equal(pullMpaError(''), 'empty');
  assert.equal(pullMpaError('0'), 'range');
  assert.equal(pullMpaError('-1'), 'range');
  assert.equal(pullMpaError('6'), 'range');
  assert.equal(pullMpaError('abc'), 'range');
  assert.throws(() => pullParams('uniform', ''), /MPa/);
  assert.throws(() => pullParams('uniform', '9'), /MPa/);
});
