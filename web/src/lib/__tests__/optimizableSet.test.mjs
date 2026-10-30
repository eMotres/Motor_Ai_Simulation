// node --test — the optimizable switch's pure part (owner 2026-10-30).  Imports lib/optimizableSet.ts itself (Node >= 22.18 / 24; else skipped).
import test from 'node:test';
import assert from 'node:assert/strict';

let O = null;
try { O = await import('../optimizableSet.ts'); } catch { /* old Node */ }
const t = O ? test : test.skip;

const SCHEMA = [
  { name: 'tooth_width', type: 'float', optimizable: true },
  { name: 'slot_height', type: 'float', optimizable: false },
  { name: 'motor_length', type: 'float', optimizable: false },
  { name: 'mode', type: 'string', optimizable: true },
];

t('the schema follows the server set, and "all allowed" switches every row on', () => {
  const s = O.withOptimizable(SCHEMA, ['motor_length'], false);
  assert.deepEqual(s.map((r) => r.optimizable), [false, false, true, false]);
  const all = O.withOptimizable(SCHEMA, [], true);
  assert.ok(all.every((r) => r.optimizable === true));
  assert.equal(SCHEMA[0].optimizable, true, 'the input is not mutated');
});

t('an unchanged row keeps its identity (no needless re-render)', () => {
  const s = O.withOptimizable(SCHEMA, ['tooth_width'], false);
  assert.equal(s[0], SCHEMA[0]);
});

t('the last switch that is on cannot be turned off (an empty list would mean everything)', () => {
  const one = O.withOptimizable(SCHEMA, ['tooth_width'], false);
  assert.equal(O.isLastEnabled(one, 'tooth_width', false), true);
  assert.equal(O.isLastEnabled(one, 'slot_height', false), false, 'a row that is off is not "the last"');
  assert.equal(O.isLastEnabled(O.withOptimizable(SCHEMA, ['tooth_width', 'slot_height'], false), 'tooth_width', false), false);
  assert.equal(O.isLastEnabled(one, 'tooth_width', true), false, 'with nothing restricted there is no last one');
  assert.equal(O.enabledCount(SCHEMA), 1, 'a word-valued knob never counts');
});

t('one switch is a delta, not the whole list', () => {
  assert.deepEqual(O.toggleBody('motor_length', true), { enable: ['motor_length'] });
  assert.deepEqual(O.toggleBody('tooth_width', false), { disable: ['tooth_width'] });
});

t('the optimistic set after a switch', () => {
  assert.deepEqual(O.afterToggle(SCHEMA, 'motor_length', true), ['tooth_width', 'motor_length']);
  assert.deepEqual(O.afterToggle(SCHEMA, 'tooth_width', false), []);
});

t('a refusal is one short line', () => {
  assert.equal(O.refusalText('Sign in to use this feature.', 'x'), 'Sign in to use this feature.');
  assert.equal(O.refusalText({ error: 'e', invalid_parameters: [{ field: 'a', message: 'not a geometry parameter: a' }] }, 'x'),
    'not a geometry parameter: a');
  assert.equal(O.refusalText(undefined, 'HTTP 500'), 'HTTP 500');
});
