import { test } from 'node:test';
import assert from 'node:assert/strict';
import { parseProjectLimit, projectLimitChanged, projectLimitLabel, projectLimitText } from '../projectLimit.ts';

test('typed text becomes a stored limit: empty = default, a whole number, or unlimited', () => {
  assert.deepEqual(parseProjectLimit(''), { ok: true, value: null });
  assert.deepEqual(parseProjectLimit('  '), { ok: true, value: null });
  assert.deepEqual(parseProjectLimit('0'), { ok: true, value: 0 });
  assert.deepEqual(parseProjectLimit(' 7 '), { ok: true, value: 7 });
  assert.deepEqual(parseProjectLimit('Unlimited'), { ok: true, value: 'unlimited' });
  assert.deepEqual(parseProjectLimit('∞'), { ok: true, value: 'unlimited' });
  for (const bad of ['-1', '1.5', 'many', '1e3', '1001', '2 projects']) assert.equal(parseProjectLimit(bad).ok, false, bad);
});

test('labels and field text', () => {
  assert.equal(projectLimitLabel(null, 2), 'default (2)');
  assert.equal(projectLimitLabel(undefined, 2), 'default (2)');
  assert.equal(projectLimitLabel(1, 2), '1 project');
  assert.equal(projectLimitLabel(5, 2), '5 projects');
  assert.equal(projectLimitLabel('unlimited', 2), 'unlimited');
  assert.equal(projectLimitText(null), '');
  assert.equal(projectLimitText(0), '0');
  assert.equal(projectLimitText('unlimited'), 'unlimited');
});

test('a save is offered only when the text parses and differs from the stored limit', () => {
  assert.equal(projectLimitChanged('3', null), true);
  assert.equal(projectLimitChanged('3', 3), false);
  assert.equal(projectLimitChanged('', null), false);
  assert.equal(projectLimitChanged('', 3), true);            // clearing goes back to the default
  assert.equal(projectLimitChanged('unlimited', 'unlimited'), false);
  assert.equal(projectLimitChanged('abc', 3), false);        // not a limit: nothing is sent
  assert.equal(projectLimitChanged('0', null), true);
});
