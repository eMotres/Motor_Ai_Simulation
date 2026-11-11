// The /projects?new=spec link of the launch mail (owner 2026-11-11): parsing, survival of a sign-in, consumed once.
import test from 'node:test';
import assert from 'node:assert/strict';
import * as L from '../appLink.ts';

test('the /projects?new=spec link is an app link, in both languages', () => {
  assert.deepEqual(L.parseAppLink('/projects', '?new=spec'), { kind: 'new', zh: false });
  assert.deepEqual(L.parseAppLink('/zh-cn/projects/', '?new=spec&signin=1'), { kind: 'new', zh: true });
  assert.equal(L.parseAppLink('/projects', ''), null);
  assert.equal(L.parseAppLink('/projects', '?new=other'), null);
  assert.equal(L.appLinkPath({ kind: 'new', zh: false }), '/projects?new=spec');
  assert.equal(L.appLinkPath({ kind: 'new', zh: true }), '/zh-cn/projects?new=spec');
  assert.ok(L.isAppLinkPath('/projects', '?new=spec') && L.isAppLinkPath('/zh-cn/projects', '?new=spec'));
  assert.ok(!L.isAppLinkPath('/projects') && !L.isAppLinkPath('/projects', '?new=x'));
});

test('it survives a sign-in and never becomes an open redirect', () => {
  assert.equal(L.appLinkReturnPath('/zh-cn/projects', '?new=spec&utm=x'), '/zh-cn/projects?new=spec');
  assert.equal(L.addressAfterSignIn('/', '', '/projects?new=spec'), '/projects?new=spec');
  assert.equal(L.addressAfterSignIn('/projects', '?new=spec', '/projects?new=spec'), null);
  assert.equal(L.safeReturnTarget('//evil.test/projects?new=spec'), null);
});

test('newProjectIntent: dialog for everybody, specification + assistant after Create for customers only, link consumed', () => {
  const link = L.parseAppLink('/projects', '?new=spec');
  assert.deepEqual(L.newProjectIntent(link, false), { openDialog: true, openSpecAfterCreate: true, consumeTo: '/' });
  assert.deepEqual(L.newProjectIntent(link, true), { openDialog: true, openSpecAfterCreate: false, consumeTo: '/' });
  assert.equal(L.newProjectIntent(L.parseAppLink('/projects/erium-x1', ''), false), null);
  assert.equal(L.newProjectIntent(null, false), null);
});
