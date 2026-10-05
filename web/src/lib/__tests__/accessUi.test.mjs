// node --test — what a standard user sees (owner 2026-10-05): Motors + Configure only,
// the default motor's reference, refusal reasons, the card summary.
// Imports lib/accessUi.ts itself (Node >= 22.18 / 24; else skipped).
import test from 'node:test';
import assert from 'node:assert/strict';

let A = null;
try { A = await import('../accessUi.ts'); } catch { /* old Node */ }
const t = A ? test : test.skip;

t('a standard user gets exactly Motors and Configure', () => {
  const all = ['motors', 'geometry', 'materials', 'mesh', 'simulation', 'static3d', 'mechanical',
    'thermal', 'controller', 'sweep', 'comparePoints', 'cost', 'compare', 'admin'];
  assert.deepEqual(all.filter((id) => A.tabAllowed(id, true)), ['motors', 'compare']);
  assert.deepEqual(all.filter((id) => A.tabAllowed(id, false)), all);
});

t('who is a standard user', () => {
  assert.equal(A.isStandardUser(true, false, 'user'), true);
  assert.equal(A.isStandardUser(true, true, 'admin'), false);
  assert.equal(A.isStandardUser(false, true, 'admin'), false);   // local dev: everything
  assert.equal(A.isStandardUser(true, false, 'anon'), false);    // the landing, not the app
});

t('landing: Configure when something is selected there, else Motors', () => {
  assert.equal(A.landingTabForUser(true), 'compare');
  assert.equal(A.landingTabForUser(false), 'motors');
});

t('the UI offers writes only to a session that may write the shared config', () => {
  assert.equal(A.uiCanWrite(true, true), true);
  assert.equal(A.uiCanWrite(true, false), false);    // a regular account: server says true (own workspace)
  assert.equal(A.uiCanWrite(false, true), false);
  assert.equal(A.uiCanWrite(undefined, true), false);
});

t('only a gate refusal has a reason key', () => {
  const gate = { detail: 'This feature requires an admin account.', required_role: 'admin', your_role: 'user' };
  assert.equal(A.refusalReasonKey(403, gate), 'refused.admin');
  assert.equal(A.refusalReasonKey(401, { ...gate, your_role: 'anon' }), 'refused.signIn');
  assert.equal(A.refusalReasonKey(403, { detail: 'locked' }), null);
  assert.equal(A.refusalReasonKey(404, gate), null);
  assert.equal(A.refusalReasonKey(403, null), null);
});

const refs = [
  { id: 'cat:1', name: 'CIANO14 40 new L12', card: { die: 'CIANO14 40 new', config: 'L12' } },
  { id: 'cat:2', name: 'CIANO14 40 new L20', card: null },
  { id: 'cat:3', name: 'CILN28', card: null },
];

t('the default motor finds its reference: by card, by "<die> <config>", by die alone', () => {
  assert.equal(A.referenceOfDefault(refs, { die: 'CIANO14 40 new', config: 'L12' }).id, 'cat:1');
  assert.equal(A.referenceOfDefault(refs, { die: 'CIANO14 40 new', config: 'L20' }).id, 'cat:2');
  assert.equal(A.referenceOfDefault(refs, { die: 'CILN28', config: 'L40' }).id, 'cat:3');
  assert.equal(A.referenceOfDefault(refs, { die: 'NOT GRANTED', config: 'L1' }), null);
  assert.equal(A.referenceOfDefault(refs, null), null);
});

t('card summary: none / partial / all', () => {
  assert.deepEqual(A.cardSummary(0, 2), { text: '0/2', state: 'none' });
  assert.deepEqual(A.cardSummary(1, 2), { text: '1/2', state: 'partial' });
  assert.deepEqual(A.cardSummary(2, 2), { text: '2/2', state: 'all' });
});
