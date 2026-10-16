/** Pure helpers of the Communication preferences page (`commPrefsLogic.ts` has no runtime imports). */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  COMM_TOPICS, emptyTopics, normalizeTopics, changedTopics, tickedTopics, anyTicked, isPreferencesPath, readLink, maskEmail,
  ndaCta, ndaRequestTicket,
} from '../commPrefsLogic.ts';

const LOCALES = join(dirname(fileURLToPath(import.meta.url)), '..', '..', 'locales');
const common = (lng) => JSON.parse(readFileSync(join(LOCALES, lng, 'common.json'), 'utf8'));

test('four topics, every box unticked by default', () => {
  assert.deepEqual([...COMM_TOPICS], ['product_updates', 'new_motors', 'engineering_content', 'co_development']);
  const e = emptyTopics();
  assert.equal(Object.keys(e).length, 4);
  assert.ok(Object.values(e).every((v) => v === false));
  assert.equal(anyTicked(e), false);
});

test('only a literal true counts as ticked', () => {
  const n = normalizeTopics({ product_updates: true, new_motors: 'true', engineering_content: 1, co_development: null, extra: true });
  assert.deepEqual(n, { product_updates: true, new_motors: false, engineering_content: false, co_development: false });
  assert.deepEqual(normalizeTopics(undefined), emptyTopics());
  assert.deepEqual(normalizeTopics('x'), emptyTopics());
});

test('save sends only what changed; sign-up sends only what was ticked', () => {
  const saved = { ...emptyTopics(), new_motors: true };
  const draft = { ...saved, new_motors: false, co_development: true };
  assert.deepEqual(changedTopics(saved, draft), { new_motors: false, co_development: true });
  assert.deepEqual(changedTopics(saved, saved), {});
  assert.deepEqual(tickedTopics(draft), { co_development: true });
  assert.deepEqual(tickedTopics(emptyTopics()), {});
  assert.equal(anyTicked(draft), true);
});

test('the preferences path, English and Chinese', () => {
  for (const p of ['/preferences', '/preferences/', '/zh-cn/preferences', '/ZH-CN/preferences/']) assert.equal(isPreferencesPath(p), true, p);
  for (const p of ['/', '/agent-consent', '/preferences/x', '/zh-cn/', '/api/preferences', null, undefined]) assert.equal(isPreferencesPath(p), false, String(p));
});

test('the mailed link: token and the unsubscribe action', () => {
  assert.deepEqual(readLink('?t=abc.def'), { token: 'abc.def', action: '' });
  assert.deepEqual(readLink('?t=abc.def&action=unsubscribe'), { token: 'abc.def', action: 'unsubscribe' });
  assert.deepEqual(readLink('?action=unsubscribe'), { token: '', action: '' });       // an action without a token does nothing
  assert.deepEqual(readLink('?t=abc&action=delete_everything'), { token: 'abc', action: '' });
  assert.deepEqual(readLink(''), { token: '', action: '' });
  assert.deepEqual(readLink(undefined), { token: '', action: '' });
});

test('the masked address matches the server', () => {
  assert.equal(maskEmail('vadim@motresres.com'), 'v***@motresres.com');
  assert.equal(maskEmail('nope'), '***');
});

test('NDA call to action: sign online when signing is open, request it when not, sign in first when signed out', () => {
  assert.equal(ndaCta({ signed: true, signable: true }, true), 'signed');
  assert.equal(ndaCta({ signed: true, signable: false }, false), 'signed');
  assert.equal(ndaCta({ signed: false, signable: true }, true), 'sign');
  assert.equal(ndaCta({ signed: false, signable: false }, true), 'request');
  assert.equal(ndaCta({ signed: false, signable: true }, false), 'signin_sign');
  assert.equal(ndaCta({ signed: false, signable: false }, false), 'signin_request');
});

test('the NDA request is a support ticket of type account', () => {
  const t = ndaRequestTicket('zh-CN');
  assert.equal(t.type, 'account');
  assert.ok(t.title.length > 0 && t.title.length <= 120 && t.description.length <= 4000);
  assert.match(t.description, /zh-CN/);
  assert.match(ndaRequestTicket('fr').description, /\ben\b/);
});

test('both locales word every topic and every page string', () => {
  const en = common('en').commPrefs;
  const zh = common('zh-CN').commPrefs;
  assert.deepEqual(Object.keys(en.topics), [...COMM_TOPICS]);
  assert.deepEqual(Object.keys(zh.topics), [...COMM_TOPICS]);
  const flat = (o, p = '') => Object.entries(o).flatMap(([k, v]) => (v && typeof v === 'object' ? flat(v, `${p}${k}.`) : [`${p}${k}`]));
  assert.deepEqual(flat(zh).sort(), flat(en).sort());
  // nothing is pre-agreed in the wording: it invites a choice
  assert.match(en.introHelp, /Nothing is ticked/);
});
