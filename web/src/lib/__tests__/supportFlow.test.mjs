// node --test — the life of a TICKET DRAFT in the help widget (lib/supportFlow.ts).
// Imports the module itself (no runtime imports); needs a Node that strips
// TypeScript types (>= 22.18 / 23.6 / 24), else the suite is skipped.
//
// Owner 2026-10-05: every question and bug goes through the assistant, which
// drafts a ticket and shows the user a summary with a Send button; the user may
// edit it; nothing is filed without that press.  What it pins:
//   * a draft arrives with a reply and opens ONE card; a reply with no draft leaves none;
//   * edit: type / title / description change, bounded; a bad type is ignored; a sent card is frozen;
//   * confirm: needs a title; posts the EDITED draft with the conversation and the context;
//     goes sending -> sent (with the id) or sending -> error (and can be retried);
//   * a second press while sending posts nothing; dismiss removes the card;
//   * the payload carries no identity.
import test from 'node:test';
import assert from 'node:assert/strict';

let F = null;
try { F = await import('../supportFlow.ts'); } catch { /* old Node */ }
const t = F ? test : test.skip;

const DRAFT = { type: 'bug', title: 'Efficiency reads 140 %', description: 'PWM, 8000 rpm, 30 A' };
const reply = (over = {}) => ({ reply: 'I prepared a ticket.', source: 'gemini', ticketDraft: DRAFT, ...over });

const talk = () => {
  let s = F.initialFlow();
  s = F.addUserMessage(s, 'Efficiency shows 140 % in PWM');
  return F.receiveReply(s, reply());
};

t('a reply with a draft opens one card, a plain reply opens none', () => {
  const s = talk();
  assert.deepEqual(s.draft.draft, DRAFT);
  assert.equal(s.draft.status, 'editing');
  assert.deepEqual(s.msgs.map((m) => m.role), ['user', 'assistant']);
  const plain = F.receiveReply(F.addUserMessage(F.initialFlow(), 'hi'), reply({ ticketDraft: null, reply: 'Hello' }));
  assert.equal(plain.draft, null);
});

t('a reply that is only the draft adds no empty message and flags the intro', () => {
  const s = F.receiveReply(F.addUserMessage(F.initialFlow(), 'it is broken'), reply({ reply: '' }));
  assert.deepEqual(s.msgs.map((m) => m.role), ['user']);
  assert.equal(s.draft.intro, true);
});

t('a newer draft replaces the card (one card at a time)', () => {
  let s = talk();
  s = F.receiveReply(s, reply({ reply: 'Another one', ticketDraft: { ...DRAFT, title: 'Second' } }));
  assert.equal(s.draft.draft.title, 'Second');
  assert.equal(s.draft.status, 'editing');
});

t('edit: fields change, are bounded, and a bad type is ignored', () => {
  let s = talk();
  s = F.editDraft(s, { title: 'New title', description: 'More words', type: 'feature' });
  assert.deepEqual(s.draft.draft, { type: 'feature', title: 'New title', description: 'More words' });
  s = F.editDraft(s, { type: 'rant' });
  assert.equal(s.draft.draft.type, 'feature');
  s = F.editDraft(s, { title: 'x'.repeat(500), description: 'y'.repeat(9000) });
  assert.equal(s.draft.draft.title.length, F.MAX_TITLE);
  assert.equal(s.draft.draft.description.length, F.MAX_DESCRIPTION);
});

t('Send needs a title', () => {
  let s = talk();
  assert.equal(F.canSend(s), true);
  s = F.editDraft(s, { title: '   ' });
  assert.equal(F.canSend(s), false);
  assert.equal(F.buildTicketPayload(s, {}), null);
});

t('confirm posts the EDITED draft with the conversation and the context, then marks it sent', async () => {
  let s = talk();
  s = F.editDraft(s, { title: 'Edited by the user', type: 'question' });
  const ctx = { tab: 'compare', configure: { preset: 'L12' } };
  const posted = [];
  const seen = [];
  const out = await F.runConfirm(s, async (p) => { posted.push(p); return { id: 't_abc' }; }, ctx, (x) => seen.push(x.draft.status));
  assert.deepEqual(seen, ['sending', 'sent']);
  assert.equal(out.draft.status, 'sent');
  assert.equal(out.draft.ticketId, 't_abc');
  assert.equal(posted.length, 1);
  const p = posted[0];
  assert.equal(p.title, 'Edited by the user');
  assert.equal(p.type, 'question');
  assert.deepEqual(p.context, ctx);
  assert.deepEqual(p.conversation.map((m) => m.role), ['user', 'assistant']);
  assert.deepEqual(Object.keys(p).sort(), ['context', 'conversation', 'description', 'title', 'type'],
    'no identity field is sent: the server reads the caller from the token');
});

t('a refusal marks the card as an error, keeps the edits and can be retried', async () => {
  let s = talk();
  s = F.editDraft(s, { title: 'Keep my edit' });
  const failed = await F.runConfirm(s, async () => { throw new Error('ticket limit reached (20 per 24 h)'); }, {}, () => {});
  assert.equal(failed.draft.status, 'error');
  assert.match(failed.draft.error, /limit/);
  assert.equal(failed.draft.draft.title, 'Keep my edit');
  assert.equal(F.canSend(failed), true);
  const retry = await F.runConfirm(failed, async () => ({ id: 't_2' }), {}, () => {});
  assert.equal(retry.draft.status, 'sent');
  assert.equal(retry.draft.error, undefined);
});

t('pressing Send while sending, or after it was sent, posts nothing', async () => {
  const s = talk();
  let n = 0;
  const submit = async () => { n++; return { id: 't_1' }; };
  const sent = await F.runConfirm(s, submit, {}, () => {});
  assert.equal(n, 1);
  assert.equal(F.canSend(sent), false);
  await F.runConfirm(sent, submit, {}, () => {});
  assert.equal(n, 1, 'a sent card is not sent twice');
  const sending = { ...s, draft: { ...s.draft, status: 'sending' } };
  await F.runConfirm(sending, submit, {}, () => {});
  assert.equal(n, 1);
});

t('a sent card cannot be edited; dismiss removes the card', async () => {
  const sent = await F.runConfirm(talk(), async () => ({ id: 't_1' }), {}, () => {});
  assert.equal(F.editDraft(sent, { title: 'x' }), sent);
  assert.equal(F.dismissDraft(talk()).draft, null);
});

t('the conversation sent with a ticket is the last 30 turns', () => {
  let s = F.initialFlow();
  for (let i = 0; i < 40; i++) s = F.addUserMessage(s, `m${i}`);
  s = F.receiveReply(s, reply());
  const p = F.buildTicketPayload(s, {});
  assert.equal(p.conversation.length, 30);
  assert.equal(p.conversation[29].role, 'assistant');
});

t('normalizeDraft: a title is required, an unknown type becomes a question, text is bounded', () => {
  assert.equal(F.normalizeDraft({ type: 'bug', title: '', description: 'x' }), null);
  assert.equal(F.normalizeDraft(null), null);
  assert.equal(F.normalizeDraft('x'), null);
  assert.equal(F.normalizeDraft({ type: 'zzz', title: ' t ' }).type, 'question');
  assert.equal(F.normalizeDraft({ type: 'account', title: 't' }).description, '');
  assert.equal(F.normalizeDraft({ type: 'bug', title: 'x'.repeat(300) }).title.length, F.MAX_TITLE);
});

t('chatReplyFrom: a draft-only reply is an answer; a 429 never carries a draft', () => {
  const only = F.chatReplyFrom(200, { reply: '', source: 'gemini', ticketDraft: DRAFT });
  assert.equal(only.reply, '');
  assert.deepEqual(only.ticketDraft, DRAFT);
  assert.equal(F.chatReplyFrom(200, { reply: '', ticketDraft: { type: 'bug' } }), null, 'a draft with no title is no draft');
  const limited = F.chatReplyFrom(429, { reply: 'Slow down', ticketDraft: DRAFT });
  assert.equal(limited.ticketDraft, null);
});
