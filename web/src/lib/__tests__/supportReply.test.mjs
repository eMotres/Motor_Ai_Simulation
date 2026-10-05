// node --test — the rule of lib/supportFlow.ts `chatReplyFrom`: what one
// /api/support/chat response MEANS.  Imports the module itself (it has no runtime
// imports); needs a Node that strips TypeScript types (>= 22.18 / 23.6 / 24),
// else the suite is skipped.
//
// The case that made it exist: on 2026-09-17 the chat opened to signed-out
// visitors, and with it came a refusal that is NOT an error — over the
// anonymous rate limit the backend answers 429 with a written, polite notice
// ("…try again in a few minutes… access is by invitation, write to …").  The
// widget used to `throw new Error('HTTP 429')` on any non-2xx and print its own
// "Sorry — I couldn't answer just now", which threw away the one sentence that
// tells a visitor what to do next.  So: any response that CARRIES a reply is
// the assistant's message; only a response without one is a failure.  Since
// 2026-10-05 a reply that is only a ticket draft is an answer too: the card is it.
import test from 'node:test';
import assert from 'node:assert/strict';

let F = null;
try { F = await import('../supportFlow.ts'); } catch { /* old Node */ }
const t = F ? test : test.skip;

t('a normal answer comes through with its source', () => {
  const out = F.chatReplyFrom(200, { reply: 'Access is by invitation.', source: 'gemini' });
  assert.deepEqual(out, { reply: 'Access is by invitation.', source: 'gemini', ticketDraft: null });
});

t('the 429 limit notice is an assistant message, not an error', () => {
  const body = {
    reply: "I've answered as many questions as I can from this connection for the moment.",
    source: 'rate_limited',
    limit: 'ip_burst',
  };
  const out = F.chatReplyFrom(429, body);
  assert.ok(out, 'a 429 that carries a reply must NOT be treated as a failure');
  assert.equal(out.source, 'rate_limited');
  assert.match(out.reply, /connection/);
});

t('a response without a reply is an error, whatever the status', () => {
  assert.equal(F.chatReplyFrom(200, {}), null);
  assert.equal(F.chatReplyFrom(200, { reply: '   ' }), null);
  assert.equal(F.chatReplyFrom(500, { detail: 'boom' }), null);
  assert.equal(F.chatReplyFrom(200, null), null);
});

t('a 5xx with a reply is still an error', () => {
  assert.equal(F.chatReplyFrom(503, { reply: 'text', source: 'x' }), null);
});
