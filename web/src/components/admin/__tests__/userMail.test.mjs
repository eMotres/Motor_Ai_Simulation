/** Pure helpers of Admin · Users for an unconfirmed account (`userMail.ts` has no runtime imports). */
import test from 'node:test';
import assert from 'node:assert/strict';
import { lastMailText, canEditEmail } from '../userMail.ts';

test('no mail recorded', () => {
  assert.match(lastMailText(null), /No mail is recorded/);
  assert.match(lastMailText(undefined), /No mail is recorded/);
});

test('a sent mail says when, and that a bounce is not tracked', () => {
  const t = lastMailText({ kind: 'verify', ts: '2026-10-15T14:02:11', ok: true });
  assert.match(t, /confirmation link sent 2026-10-15 14:02/);
  assert.match(t, /bounce is not tracked/);
  assert.match(lastMailText({ kind: 'reset', ts: '2026-10-15T14:02:11', ok: true }), /password-reset link sent/);
});

test('a refused mail is flagged', () => {
  const t = lastMailText({ kind: 'verify', ts: '2026-10-15T14:02:11', ok: false, error: 'SMTPAuthenticationError' });
  assert.match(t, /FAILED 2026-10-15 14:02 \(SMTPAuthenticationError\)/);
});

test('only an unconfirmed account can have its address edited', () => {
  assert.equal(canEditEmail({ email_verified: false }), true);
  assert.equal(canEditEmail({ email_verified: true }), false);
  assert.equal(canEditEmail({}), false);            // an older record without the flag counts as confirmed
  assert.equal(canEditEmail(null), false);
});
