// node --test: the one mechanism every page uses to remember where the user was (lib/viewMemory.ts).
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import * as V from '../viewMemory.ts';
import { DEVELOPMENT_VIEWS, developmentViewScope, parseDevelopmentView, restorableView } from '../../modules/development/viewState.ts';
import { readFocus, writeFocus } from '../../modules/development/projectFocus.ts';

const memory = () => { const map = new Map(); return { map, getItem: (k) => map.get(k) ?? null, setItem: (k, v) => { map.set(k, String(v)); }, removeItem: (k) => { map.delete(k); } }; };
const blocked = { getItem() { throw new Error('blocked'); }, setItem() { throw new Error('blocked'); }, removeItem() { throw new Error('blocked'); } };

test('values live under the view. namespace and survive a round trip', () => {
  const s = memory();
  V.writeView('admin.section', 'users', s);
  assert.deepEqual([...s.map.keys()], ['view.admin.section']);
  assert.equal(V.readView('admin.section', V.oneOf(['overview', 'users']), s), 'users');
  V.clearView('admin.section', s);
  assert.equal(V.readView('admin.section', V.oneOf(['overview', 'users']), s), null);
});

test('empty, blocked or missing storage never throws and gives nothing', () => {
  assert.equal(V.readView('x.y', V.oneOf(['a']), memory()), null);
  assert.equal(V.readView('x.y', V.oneOf(['a']), blocked), null);
  assert.equal(V.readView('x.y', V.oneOf(['a']), null), null);
  assert.doesNotThrow(() => V.writeView('x.y', 'a', blocked));
  assert.doesNotThrow(() => V.writeView('x.y', 'a', null));
  assert.doesNotThrow(() => V.clearView('x.y', blocked));
});

test('a stale or damaged value falls back (the validator decides), a legacy bare string is still read', () => {
  const s = memory();
  s.map.set('view.admin.section', 'users');                 // written by an older build as a bare string
  assert.equal(V.readView('admin.section', V.oneOf(['overview', 'users']), s), 'users');
  s.map.set('view.admin.section', '"renamed-section"');
  assert.equal(V.readView('admin.section', V.oneOf(['overview', 'users']), s), null);
  s.map.set('view.x', '{broken');
  assert.equal(V.readView('x', V.shape({ a: V.text }), s), null);
});

test('no secrets: scopes that look like credentials are refused', () => {
  for (const scope of ['auth.token', 'user.password', 'api_key.last', 'session-id', 'x.secret']) {
    assert.throws(() => V.viewKey(scope), scope);
    const s = memory();
    V.writeView(scope, 'x', s);
    assert.equal(s.map.size, 0, scope);
  }
});

test('shape keeps only the declared fields and rejects wrong types', () => {
  const parse = V.shape({ project: V.text, step: V.smallInt, showAll: V.bool, duty: V.nullableText });
  assert.deepEqual(parse({ project: 'p', step: 2, showAll: true, duty: null, extra: 'dropped' }), { project: 'p', step: 2, showAll: true, duty: null });
  assert.equal(parse({ project: 'p', step: -1, showAll: true, duty: null }), null);
  assert.equal(parse({ project: '', step: 1, showAll: true, duty: null }), null);
  assert.equal(parse([]), null);
});

test('Development remembers project, tab and card per account; a stale card or tab is corrected', () => {
  const s = memory();
  const scope = developmentViewScope('Customer@Example.test');
  assert.equal(scope, developmentViewScope(' customer@example.test '));
  assert.notEqual(scope, developmentViewScope('other@example.test'));
  assert.ok(!scope.includes('example'), 'the address is hashed, not stored');
  V.writeView(scope, { project: 'alpha', view: 'messages', step: 2 }, s);
  assert.deepEqual(V.readView(scope, parseDevelopmentView, s), { project: 'alpha', view: 'messages', step: 2 });
  V.writeView(scope, { project: 'alpha', view: 'nonsense', step: 2 }, s);
  assert.equal(V.readView(scope, parseDevelopmentView, s), null);
  assert.deepEqual(restorableView({ project: 'a', view: 'specification', step: 9 }, 4, true), { view: 'specification', step: 3 });
  assert.deepEqual(restorableView({ project: 'a', view: 'notes', step: 0 }, 4, false), { view: 'specification', step: 0 });
  assert.deepEqual(restorableView({ project: 'a', view: 'notes', step: 0 }, 4, true), { view: 'notes', step: 0 });
  assert.ok(DEVELOPMENT_VIEWS.includes('motors'));
});

test('the project the Motors tab highlights is remembered per account and validated', () => {
  const s = memory();
  assert.equal(readFocus('a@example.test', s), null);
  writeFocus('a@example.test', { id: 'alpha', title: 'Drone arm' }, s);
  assert.deepEqual(readFocus('A@example.test', s), { id: 'alpha', title: 'Drone arm' });
  assert.equal(readFocus('b@example.test', s), null);                         // another account does not inherit it
  writeFocus('a@example.test', { id: 'alpha', title: null }, s);
  assert.deepEqual(readFocus('a@example.test', s), { id: 'alpha', title: null });
  V.writeView('projects.focus.a@example.test', { title: 'no id' }, s);
  assert.equal(readFocus('a@example.test', s), null);
});

test('pages use the one mechanism: no page keeps its own tab memory next to it', () => {
  const read = (p) => readFileSync(new URL(p, import.meta.url), 'utf8');
  assert.match(read('../../modules/development/projectFocus.ts'), /writeView\(focusScope/);
  assert.match(read('../../modules/development/components/DevelopmentPage.tsx'), /readView\(developmentViewScope/);
  assert.match(read('../../components/admin/AdminNav.tsx'), /useRememberedView|readView/);
});
