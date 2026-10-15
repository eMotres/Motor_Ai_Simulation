// node --test - the pure helpers of the applicant verification (lib/verification.ts): the status line the person sees instead of "Sign NDA",
// the extra fields of the NDA form, the admin queue order and actions.  Imports the .ts itself (Node >= 22.18 / 24; else skipped).
import test from 'node:test';
import assert from 'node:assert/strict';

let V = null;
try { V = await import('../verification.ts'); } catch { /* old Node */ }
const t = V ? test : test.skip;

t('parseAccess: a server object is read, a missing or damaged one falls back to can_download alone', () => {
  const a = V.parseAccess({ state: 'need_info', nda_signed: true, case: 'v1', reply_url: 'https://x.y/r/t', requests: ['website', 7, 'corporate_email'] }, false);
  assert.deepEqual([a.state, a.nda_signed, a.case, a.reply_url, a.requests], ['need_info', true, 'v1', 'https://x.y/r/t', ['website', 'corporate_email']]);
  assert.deepEqual(V.parseAccess(undefined, true), { state: 'ok', nda_signed: true });
  assert.deepEqual(V.parseAccess({ state: 'weird' }, false), { state: 'nda', nda_signed: false });
  assert.deepEqual(V.parseAccess('x', false), { state: 'nda', nda_signed: false });
  assert.equal(V.parseAccess({ state: 'in_progress' }, false).nda_signed, true);       // any state past 'nda' means the NDA is signed
});

t('lockedKind: no NDA -> sign it; NDA signed -> the verification is what is missing', () => {
  assert.equal(V.lockedKind({ state: 'nda', nda_signed: false }), 'nda');
  assert.equal(V.lockedKind({ state: 'in_progress', nda_signed: true }), 'verification');
  assert.equal(V.lockedKind({ state: 'need_info', nda_signed: true }), 'verification');
});

t('statusLine: in progress / need one more thing (with the link) / denied (no link); nothing when open or no NDA', () => {
  assert.deepEqual(V.statusLine({ state: 'in_progress', nda_signed: true, reply_url: 'https://a.b/r/1' }), { key: 'inProgress', link: 'https://a.b/r/1' });
  assert.deepEqual(V.statusLine({ state: 'need_info', nda_signed: true, reply_url: 'https://a.b/r/1' }), { key: 'needInfo', link: 'https://a.b/r/1' });
  assert.deepEqual(V.statusLine({ state: 'denied', nda_signed: true, reply_url: 'https://a.b/r/1' }), { key: 'denied', link: null });
  assert.equal(V.statusLine({ state: 'ok', nda_signed: true }), null);
  assert.equal(V.statusLine({ state: 'nda', nda_signed: false }), null);
});

t('a link is built only from a web address', () => {
  assert.equal(V.safeReplyUrl('javascript:alert(1)'), null);
  assert.equal(V.safeReplyUrl('data:text/html,x'), null);
  assert.equal(V.safeReplyUrl(''), null);
  assert.equal(V.safeReplyUrl(undefined), null);
  assert.equal(V.safeReplyUrl('http://localhost:5173/api/verification/reply/abc'), 'http://localhost:5173/api/verification/reply/abc');
  assert.deepEqual(V.statusLine({ state: 'need_info', nda_signed: true, reply_url: 'javascript:alert(1)' }), { key: 'needInfo', link: null });
});

t('every request code the server can send has a label key', () => {
  for (const c of ['corporate_email', 'person_page', 'website', 'registration_number', 'vat_number', 'domain_mismatch', 'role_clarification', 'business_licence', 'verify_account_email', 'free_text']) {
    assert.ok(V.isRequestCode(c), c);
  }
  assert.equal(V.isRequestCode('nope'), false);
});

t('normalizeWebsite mirrors the server rule', () => {
  assert.equal(V.normalizeWebsite('siemens.com/about'), 'https://siemens.com/about');
  assert.equal(V.normalizeWebsite(' HTTP://WWW.Siemens.com '), 'http://www.siemens.com');
  for (const bad of ['', 'intranet', 'http://10.0.0.1/', 'ftp://x.com', 'a b.com', 'javascript:alert(1)', 'http://user@x.com', 'x.local']) {
    assert.equal(V.normalizeWebsite(bad), null, bad);
  }
});

t('intakeErrors: website and purpose are asked for, the work e-mail only when given', () => {
  assert.deepEqual(V.intakeErrors(V.emptyIntake()), { website: 'required', purpose: 'required' });
  assert.deepEqual(V.intakeErrors({ website: 'nope', purpose: 'x', vat_no: '', work_email: '' }), { website: 'invalid' });
  assert.deepEqual(V.intakeErrors({ website: 'acme.com', purpose: 'x', vat_no: '', work_email: 'bad' }), { work_email: 'invalid' });
  assert.deepEqual(V.intakeErrors({ website: 'acme.com', purpose: 'x', vat_no: 'DE123', work_email: 'a@acme.com' }), {});
});

t('intakeBody trims and collapses the purpose', () => {
  assert.deepEqual(V.intakeBody({ website: ' acme.com ', purpose: ' a   b\n c ', vat_no: ' DE1 ', work_email: ' a@b.co ' }),
    { website: 'acme.com', purpose: 'a b c', vat_no: 'DE1', work_email: 'a@b.co' });
});

t('the admin queue lists what needs a person first, newest first inside a status', () => {
  const rows = [
    { status: 'VERIFIED', updated_iso: '2026-10-15T12:00:00Z', id: 'a' }, { status: 'MORE_INFORMATION_REQUIRED', updated_iso: '2026-10-15T09:00:00Z', id: 'b' },
    { status: 'MANUAL_REVIEW', updated_iso: '2026-10-14T09:00:00Z', id: 'c' }, { status: 'MANUAL_REVIEW', updated_iso: '2026-10-15T09:00:00Z', id: 'd' },
    { status: 'DENIED', updated_iso: '2026-10-15T13:00:00Z', id: 'e' }, { status: 'PENDING', updated_iso: '2026-10-15T10:00:00Z', id: 'f' },
  ];
  assert.deepEqual(V.sortQueue(rows).map((r) => r.id), ['d', 'c', 'b', 'f', 'a', 'e']);
  assert.deepEqual(rows.map((r) => r.id), ['a', 'b', 'c', 'd', 'e', 'f']);          // the input is not mutated
});

t('admin actions per status and the reason rule', () => {
  assert.deepEqual(V.adminActions('MANUAL_REVIEW'), ['approve', 'requestInfo', 'deny', 'rerun']);
  assert.deepEqual(V.adminActions('DENIED'), ['approve', 'rerun']);
  assert.deepEqual(V.adminActions('VERIFIED'), ['requestInfo', 'deny', 'rerun']);
  assert.equal(V.reasonOk(' '), false);
  assert.equal(V.reasonOk('ok'), false);
  assert.equal(V.reasonOk('met at the fair'), true);
  assert.deepEqual(['VERIFIED', 'DENIED', 'MANUAL_REVIEW', 'PENDING'].map(V.statusTone), ['ok', 'bad', 'warn', 'info']);
});
