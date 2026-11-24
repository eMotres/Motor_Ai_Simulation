import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { audienceBlockedReason, choiceFor, effectiveAudience, fixedAudience, isInternal, reconcileChoice, replyPayload } from '../internalMessage.ts';

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

test('the page wires the audience to the server capability, the review dialog replies INSIDE the chosen note, and strings have EN and zh-CN', () => {
  const page = read('../components/DevelopmentPage.tsx');
  assert.match(page, /canPostInternal = !!selected && selected\.can_post_internal === true/);
  assert.match(page, /choiceFor\(audienceState, selected\?\.id \?\? '', user\?\.email \?\? ''\)/);
  assert.match(page, /setAudienceState\(null\)/);                          // reset after a send
  assert.match(page, /replyPayload\(reviewParent, canPostInternal, reviewAudience\)\.reply_to/);   // the review answers inside the chosen note
  assert.match(page, /fixedAudience\(canPostInternal, reviewParent\)/);
  assert.match(page, /message\.staff_only && <Chip/);                      // the Internal badge in the review dialog
  assert.doesNotMatch(page, /NotesPanel|tabs\.notes|\{view === 'notes'/);
  for (const lang of ['en', 'zh-CN']) {
    const json = JSON.parse(read(`../../../locales/${lang}/development.json`));
    for (const key of ['internalMessage', 'internalMessageTip', 'internalSwitch', 'audience', 'audienceEveryone', 'audienceTip', 'audienceRequired']) assert.ok(json[key], `${lang}:${key}`);
    assert.equal(json.tabs.notes, undefined);
  }
});

test('a transition DROPS the stored choice: A -> B -> A and X -> Y -> X never bring it back', () => {
  let state = { project: 'A', identity: 'x', value: 'all' };
  state = reconcileChoice(state, 'A', 'x');
  assert.equal(state?.value, 'all');
  state = reconcileChoice(state, 'B', 'x');
  assert.equal(state, null);
  state = reconcileChoice(state, 'A', 'x');
  assert.equal(state, null);
  let who = { project: 'A', identity: 'x', value: 'all' };
  who = reconcileChoice(who, 'A', 'y');
  who = reconcileChoice(who, 'A', 'x');
  assert.equal(who, null);
});

test('the review answer goes to the note the user chose, not to the last note; an answer to an internal note stays internal', () => {
  const internalNote = { id: 'n1', staff_only: true };
  const publicNote = { id: 'n2' };                                        // a later customer note
  assert.deepEqual(replyPayload(internalNote, true, 'all'), { reply_to: 'n1', staff_only: true });   // a retained "Everyone" cannot make it public
  assert.deepEqual(replyPayload(internalNote, true, null), { reply_to: 'n1', staff_only: true });
  assert.deepEqual(replyPayload(publicNote, true, 'internal'), { reply_to: 'n2', staff_only: true });
  assert.deepEqual(replyPayload(publicNote, true, 'all'), { reply_to: 'n2', staff_only: false });
  assert.deepEqual(replyPayload(undefined, true, 'all'), { staff_only: false });                    // no target: a new note
  assert.deepEqual(replyPayload(internalNote, false, 'internal'), { reply_to: 'n1', staff_only: false });   // non-staff: never internal (the server hides the parent anyway)
});

test('the page binds the explicit target, drops choices on transitions and resets the audience when the target changes', () => {
  const page = read('../components/DevelopmentPage.tsx');
  assert.match(page, /reviewTargetId \? reviewThread\.find\(\(m\) => m\.id === reviewTargetId\)/);
  assert.match(page, /setAudienceState\(\(cur\) => reconcileChoice\(cur, selected\?\.id \?\? '', user\?\.email \?\? ''\)\)/);
  assert.match(page, /setReviewAudienceState\(\(cur\) => reconcileChoice\(/);
  assert.match(page, /setReviewTargetId\(message\.id === reviewTargetId \? null : message\.id!\); setReviewAudienceState\(null\)/);
  assert.match(page, /replyPayload\(reviewParent, canPostInternal, reviewAudience\)\.staff_only/);
});
