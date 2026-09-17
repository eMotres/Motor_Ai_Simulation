// node --test — the pure rule of lib/support.ts `chatReplyFrom`, restated here
// verbatim (the repo's convention for node tests: `node --test` cannot load the
// TS modules, so the pure function under test is re-stated and kept in sync).
//
// The case that made it exist: on 2026-09-17 the chat opened to signed-out
// visitors, and with it came a refusal that is NOT an error — over the
// anonymous rate limit the backend answers 429 with a written, polite notice
// ("…try again in a few minutes… access is by invitation, write to …").  The
// widget used to `throw new Error('HTTP 429')` on any non-2xx and print its own
// "Sorry — I couldn't answer just now", which threw away the one sentence that
// tells a visitor what to do next.  So: any response that CARRIES a reply is
// the assistant's message; only a response without one is a failure.
import test from 'node:test';
import assert from 'node:assert/strict';

function chatReplyFrom(status, body) {
  const b = body;
  const reply = typeof b?.reply === 'string' ? b.reply.trim() : '';
  if (!reply) return null;
  if (status >= 200 && status < 300) return { reply, source: String(b?.source ?? '') };
  if (status === 429) return { reply, source: String(b?.source ?? 'rate_limited') };
  return null;
}

test('a normal answer comes through with its source', () => {
  const out = chatReplyFrom(200, { reply: 'Access is by invitation.', source: 'gemini' });
  assert.deepEqual(out, { reply: 'Access is by invitation.', source: 'gemini' });
});

test('the 429 limit notice is an assistant message, not an error', () => {
  const body = {
    reply: "I've answered as many questions as I can from this connection for the moment.",
    source: 'rate_limited',
    limit: 'ip_burst',
  };
  const out = chatReplyFrom(429, body);
  assert.ok(out, 'a 429 that carries a reply must NOT be treated as a failure');
  assert.equal(out.source, 'rate_limited');
  assert.match(out.reply, /connection/);
});

test('a 429 without a body is still a failure', () => {
  assert.equal(chatReplyFrom(429, null), null);
  assert.equal(chatReplyFrom(429, {}), null);
  assert.equal(chatReplyFrom(429, { reply: '   ' }), null);
});

test('every other non-2xx is a failure, reply or not', () => {
  for (const s of [401, 403, 500, 502, 503]) {
    assert.equal(chatReplyFrom(s, { reply: 'whatever' }), null, `HTTP ${s}`);
  }
});

test('a 2xx with no reply is a failure (an empty answer is not an answer)', () => {
  assert.equal(chatReplyFrom(200, { source: 'gemini' }), null);
  assert.equal(chatReplyFrom(200, null), null);
});

test('the mock/demo flag still rides on `source`', () => {
  // the widget shows its "demo mode" line on source === 'mock' and must not
  // show it for a rate-limited answer
  assert.equal(chatReplyFrom(200, { reply: 'x', source: 'mock' }).source, 'mock');
  assert.notEqual(chatReplyFrom(429, { reply: 'x', source: 'rate_limited' }).source, 'mock');
});
