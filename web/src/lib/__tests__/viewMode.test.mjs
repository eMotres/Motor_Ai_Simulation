// node --test: the remembered List / Cards choice of the overview pages (owner 2026-10-28). Imports viewMode.ts itself (Node >= 22.18 / 24).
import test from 'node:test';
import assert from 'node:assert/strict';
import { VIEW_MODE_KEYS, parseViewMode, readViewMode, writeViewMode } from '../viewMode.ts';

const memory = (init = {}) => {
  const data = { ...init };
  return { data, getItem: (k) => (k in data ? data[k] : null), setItem: (k, v) => { data[k] = String(v); } };
};
const broken = { getItem() { throw new Error('blocked'); }, setItem() { throw new Error('full'); } };

test('only "list" and "cards" are modes', () => {
  assert.equal(parseViewMode('list'), 'list');
  assert.equal(parseViewMode('cards'), 'cards');
  for (const bad of ['', 'grid', 'LIST', null, undefined, 1, {}]) assert.equal(parseViewMode(bad), null);
});

test('the page keys are per page', () => {
  assert.equal(VIEW_MODE_KEYS.development, 'aerostator.view.development');
  assert.equal(VIEW_MODE_KEYS.production, 'aerostator.view.production');
});

test('nothing stored: the page default', () => {
  const s = memory();
  assert.equal(readViewMode(VIEW_MODE_KEYS.development, 'cards', s), 'cards');
  assert.equal(readViewMode(VIEW_MODE_KEYS.production, 'list', s), 'list');
});

test('a stored mode round-trips, per key', () => {
  const s = memory();
  writeViewMode(VIEW_MODE_KEYS.development, 'list', s);
  writeViewMode(VIEW_MODE_KEYS.production, 'cards', s);
  assert.equal(readViewMode(VIEW_MODE_KEYS.development, 'cards', s), 'list');
  assert.equal(readViewMode(VIEW_MODE_KEYS.production, 'list', s), 'cards');
  assert.equal(s.data['aerostator.view.development'], 'list');
});

test('a stale or garbage value falls back to the default', () => {
  const s = memory({ [VIEW_MODE_KEYS.development]: 'tiles' });
  assert.equal(readViewMode(VIEW_MODE_KEYS.development, 'cards', s), 'cards');
});

test('storage that throws (or is missing) never breaks the page', () => {
  assert.equal(readViewMode('k', 'list', broken), 'list');
  assert.doesNotThrow(() => writeViewMode('k', 'cards', broken));
  assert.equal(readViewMode('k', 'cards', null), 'cards');
  assert.doesNotThrow(() => writeViewMode('k', 'list', null));
});
