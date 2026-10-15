/** Pure helpers of the "confirm your e-mail" banner (`unverified.ts` has no runtime imports). */
import test from 'node:test';
import assert from 'node:assert/strict';
import { unverifiedRefusal, resendWait, canChangeAddress, UNVERIFIED_EVENT, RESEND_COOLDOWN_S, GATES } from '../unverified.ts';

test('the server refusal is recognised by its code, not by the status alone', () => {
  const body = { detail: 'x', code: 'email_unverified', params: { email: 'a@b.com', gate: 'compute' } };
  assert.deepEqual(unverifiedRefusal(403, body), { email: 'a@b.com', gate: 'compute' });
  assert.deepEqual(unverifiedRefusal(409, { code: 'email_unverified', params: { email: 'a@b.com', gate: 'role' } }), { email: 'a@b.com', gate: 'role' });
  assert.equal(unverifiedRefusal(403, { detail: 'This feature requires an admin account.', required_role: 'admin' }), null);
  assert.equal(unverifiedRefusal(200, body), null);
  assert.equal(unverifiedRefusal(403, null), null);
  assert.equal(unverifiedRefusal(403, 'nope'), null);
  assert.deepEqual(unverifiedRefusal(403, { code: 'email_unverified' }), { email: '', gate: '' });
});

test('every gate the server names is known', () => {
  assert.deepEqual([...GATES], ['nda', 'step', 'compute', 'mailing', 'tokens', 'role']);
  assert.equal(UNVERIFIED_EVENT, 'email-unverified');
});

test('resend waits one cooldown after a send', () => {
  assert.equal(resendWait(null, 1000), 0);
  assert.equal(resendWait(1000, 1000), RESEND_COOLDOWN_S);
  assert.equal(resendWait(1000, 1000 + 30_000), RESEND_COOLDOWN_S - 30);
  assert.equal(resendWait(1000, 1000 + 61_000), 0);
});

test('the change-address form needs a different good address and a password', () => {
  assert.equal(canChangeAddress('d.xu@movigor.tech', 'd.xu@movigor.com', 'pw'), true);
  assert.equal(canChangeAddress('d.xu@movigor.tech', 'D.Xu@Movigor.Tech', 'pw'), false);   // the same address
  assert.equal(canChangeAddress('a@b.com', 'not an address', 'pw'), false);
  assert.equal(canChangeAddress('a@b.com', 'c@d.com', ''), false);
});
