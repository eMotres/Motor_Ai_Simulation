import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { audienceBlockedReason, choiceFor, effectiveAudience, fixedAudience, isInternal } from '../internalMessage.ts';

const here = dirname(fileURLToPath(import.meta.url));
const read = (path) => readFileSync(join(here, path), 'utf8');
const internal = { staff_only: true };
const open = {};

test('staff have NO default audience: sending is blocked with a reason until one is chosen', () => {
  assert.equal(effectiveAudience(true, null), null);
  assert.equal(audienceBlockedReason(true, null), 'audienceRequired');
  assert.equal(audienceBlockedReason(true, null, open), 'audienceRequired');
  assert.equal(audienceBlockedReason(true, 'all'), null);
  assert.equal(audienceBlockedReason(true, 'internal'), null);
});

test('a chosen audience never changes by itself: it does not depend on the thread', () => {
  assert.equal(isInternal(true, 'all'), false);
  assert.equal(isInternal(true, 'internal'), true);
  assert.equal(isInternal(true, 'all', open), false);                   // a public parent does not flip it
  assert.equal(isInternal(true, 'internal', open), true);
});

test('a reply inside an internal message is fixed to Internal whatever was chosen', () => {
  assert.equal(fixedAudience(true, internal), 'internal');
  assert.equal(effectiveAudience(true, 'all', internal), 'internal');
  assert.equal(effectiveAudience(true, null, internal), 'internal');
  assert.equal(audienceBlockedReason(true, null, internal), null);
  assert.equal(fixedAudience(true, open), null);
});

test('non-staff (no server capability) have no choice: always everyone, never internal', () => {
  assert.equal(effectiveAudience(false, null), 'all');
  assert.equal(effectiveAudience(false, 'internal', internal), 'all');
  assert.equal(isInternal(false, 'internal', internal), false);
  assert.equal(audienceBlockedReason(false, null), null);
  assert.equal(fixedAudience(false, internal), null);
});

test('the choice is dropped when the project or the signed-in identity changes', () => {
  const state = { project: 'a', identity: 'x@e', value: 'internal' };
  assert.equal(choiceFor(state, 'a', 'x@e'), 'internal');
  assert.equal(choiceFor(state, 'b', 'x@e'), null);
  assert.equal(choiceFor(state, 'a', 'y@e'), null);
  assert.equal(choiceFor(null, 'a', 'x@e'), null);
});

test('the page wires the audience to the server capability, the review dialog replies INSIDE the last note, and strings have EN and zh-CN', () => {
  const page = read('../components/DevelopmentPage.tsx');
  assert.match(page, /canPostInternal = !!selected && selected\.can_post_internal === true/);
  assert.match(page, /choiceFor\(audienceState, selected\?\.id \?\? '', user\?\.email \?\? ''\)/);
  assert.match(page, /setAudienceState\(null\)/);                          // reset after a send
  assert.match(page, /reply_to: reviewParent\?\.id/);                      // the section / field review answers inside the last note
  assert.match(page, /fixedAudience\(canPostInternal, reviewParent\)/);
  assert.match(page, /message\.staff_only && <Chip/);                      // the Internal badge in the review dialog
  assert.doesNotMatch(page, /NotesPanel|tabs\.notes|\{view === 'notes'/);
  for (const lang of ['en', 'zh-CN']) {
    const json = JSON.parse(read(`../../../locales/${lang}/development.json`));
    for (const key of ['internalMessage', 'internalMessageTip', 'internalSwitch', 'audience', 'audienceEveryone', 'audienceTip', 'audienceRequired']) assert.ok(json[key], `${lang}:${key}`);
    assert.equal(json.tabs.notes, undefined);
  }
});
