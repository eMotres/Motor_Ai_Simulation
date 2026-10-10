import test from 'node:test';
import assert from 'node:assert/strict';

globalThis.localStorage = new Map();
const store = globalThis.localStorage;
globalThis.localStorage = {
  getItem: (key) => store.get(key) ?? null,
  setItem: (key, value) => store.set(key, String(value)),
};
const C = await import('../../components/compare/configureChoice.ts');

test('Configure reference choice is account-scoped, with no global legacy migration', () => {
  C.writeConfigureRefId('A@example.com', 'cat:loaded-a');
  C.writeConfigureRefId('B@example.com', 'cat:loaded-b');
  assert.equal(C.readConfigureRefId('a@example.com'), 'cat:loaded-a');
  assert.equal(C.readConfigureRefId('b@example.com'), 'cat:loaded-b');
  assert.notEqual(C.configureRefIdKey('a@example.com'), C.configureRefIdKey('b@example.com'));
  assert.equal(C.readConfigureRefId('new@example.com'), null);
  assert.equal(C.hasOwnConfigureChoice('new@example.com'), false);
});
