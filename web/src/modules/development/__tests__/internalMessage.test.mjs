import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { composeInternal, defaultInternal } from '../internalMessage.ts';

const here = dirname(fileURLToPath(import.meta.url));
const read = (path) => readFileSync(join(here, path), 'utf8');
const customer = { id: 'a', created_at: 't', text: 'q' };
const internal = { id: 'b', created_at: 't', text: 'n', staff_only: true };

test('the switch is OFF by default when the last message in view is a customer message or the thread is empty', () => {
  assert.equal(defaultInternal(true, []), false);
  assert.equal(defaultInternal(true, [internal, customer]), false);
});

test('the switch is ON by default when the last message in view is internal', () => {
  assert.equal(defaultInternal(true, [customer, internal]), true);
});

test('a caller without the server capability never composes an internal message, whatever the state', () => {
  assert.equal(defaultInternal(false, [internal]), false);
  assert.equal(composeInternal(false, true, [internal]), false);
});

test('a touched switch wins over the default; an untouched one follows it', () => {
  assert.equal(composeInternal(true, false, [customer, internal]), false);
  assert.equal(composeInternal(true, true, [customer]), true);
  assert.equal(composeInternal(true, null, [customer, internal]), true);
  assert.equal(composeInternal(true, null, [internal, customer]), false);
});

test('the page shows the switch by the server capability only, the Internal notes block is gone, and strings have EN and zh-CN', () => {
  const page = read('../components/DevelopmentPage.tsx');
  assert.match(page, /canPostInternal = !!selected && selected\.can_post_internal === true/);
  assert.match(page, /\{canPostInternal && <Tooltip title=\{tr\('internalSwitchTip'\)\}>/);
  assert.doesNotMatch(page, /NotesPanel|tabs\.notes|\{view === 'notes'/);
  for (const lang of ['en', 'zh-CN']) {
    const json = JSON.parse(read(`../../../locales/${lang}/development.json`));
    for (const key of ['internalMessage', 'internalMessageTip', 'internalSwitch', 'internalSwitchTip']) assert.ok(json[key], `${lang}:${key}`);
    assert.equal(json.tabs.notes, undefined);
    assert.equal(json.internalNotesTip, undefined);
  }
});
