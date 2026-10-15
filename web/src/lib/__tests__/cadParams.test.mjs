// node --test - the editable CAD-only table: drafts, validation, what is sent, the rebuild badge (lib/cadParams.ts).
import test from 'node:test';
import assert from 'node:assert/strict';

let C = null;
try { C = await import('../cadParams.ts'); } catch { /* old Node */ }
const t = C ? test : test.skip;

const row = (name, over = {}) => ({ name, group: 'housing', unit: 'mm', kind: 'float', min: 0.1, max: 5, tip: { en: 'x', zh: 'y' },
  cad_only: true, default: 0.8, built: 0.8, value: 0.8, overridden: false, ...over });
const ROWS = [row('ring_t'), row('holder_flange_t', { default: 1.2, built: 1.2, value: 1.2, max: 10 }),
  row('bearing_ball_count', { unit: '', kind: 'int', min: 3, max: 60, default: 10, built: 10, value: 10 })];

t('typed values: decimal comma, junk is not a number', () => {
  assert.equal(C.parseDraft('1,5'), 1.5);
  assert.equal(C.parseDraft(' 2.25 '), 2.25);
  assert.equal(C.parseDraft('1e-2'), 0.01);
  for (const bad of ['', 'abc', '1.2.3', '--1', '1,2,3', 'NaN', 'Infinity']) assert.equal(C.parseDraft(bad), null, bad);
});

t('range and integer errors are caught before the request', () => {
  assert.equal(C.draftError(ROWS[0], '0.9'), null);
  assert.equal(C.draftError(ROWS[0], '50'), 'range');
  assert.equal(C.draftError(ROWS[0], '0.05'), 'range');
  assert.equal(C.draftError(ROWS[0], 'x'), 'nan');
  assert.equal(C.draftError(ROWS[2], '7.5'), 'int');
  assert.equal(C.draftError(ROWS[2], '12'), null);
});

t('only changed rows are sent; the reference value is a reset (null); untouched and invalid rows are left out', () => {
  const drafts = { ring_t: '0.9', holder_flange_t: '1.2', bearing_ball_count: 'oops' };
  assert.deepEqual(C.changesOf(ROWS, drafts), { ring_t: 0.9 });
  const saved = [row('ring_t', { value: 0.9, overridden: true }), ROWS[1]];
  assert.deepEqual(C.changesOf(saved, { ring_t: '0,8' }), { ring_t: null });           // back to the default: remove the override
  assert.deepEqual(C.changesOf(saved, { ring_t: '0.9' }), {});                         // equal to the saved one: nothing to send
  assert.deepEqual(C.invalidDrafts(ROWS, drafts), ['bearing_ball_count']);
  assert.deepEqual(C.invalidDrafts(ROWS, { ring_t: '60' }), ['ring_t']);
});

t('the model is shown with a badge while it is stale, never blocked', () => {
  const s = (state, requested = false) => ({ state, stale: state !== 'current' && state !== 'none', requested });
  assert.equal(C.modelBadge(s('current')), 'none');
  assert.equal(C.modelBadge(s('none')), 'none');
  assert.equal(C.modelBadge(null), 'none');
  assert.equal(C.modelBadge(s('building', true)), 'rebuilding');
  assert.equal(C.modelBadge(s('pending', true)), 'queued');
  assert.equal(C.modelBadge(s('pending', false)), 'stale');
  assert.equal(C.modelBadge(s('failed')), 'failed');
  assert.equal(C.shouldPoll(s('building', true)), true);
  assert.equal(C.shouldPoll(s('pending', true)), true);
  assert.equal(C.shouldPoll(s('pending', false)), false);
  assert.equal(C.shouldPoll(s('failed')), false);
  assert.equal(C.shouldPoll(s('current')), false);
});

t('a refusal shows the template sentences, else the error, else the text', () => {
  assert.deepEqual(C.refusalLines({ error: 'would not build', violations: ['a', 'b'] }), ['a', 'b']);
  assert.deepEqual(C.refusalLines({ error: 'would not build', violations: [] }), ['would not build']);
  assert.deepEqual(C.refusalLines('plain'), ['plain']);
  assert.deepEqual(C.refusalLines(null), []);
  assert.equal(C.formatValue(1.23456789), '1.2346');
  assert.equal(C.formatValue(10), '10');
  assert.deepEqual(C.rowsOfGroup(ROWS, 'housing').map((r) => r.name), ['ring_t', 'holder_flange_t', 'bearing_ball_count']);
});
