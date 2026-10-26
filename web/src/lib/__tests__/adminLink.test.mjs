// node --test - Admin deep links used by the owner's daily report (links to Admin -> Digests for a draft).
import test from 'node:test';
import assert from 'node:assert/strict';

let L = null;
try { L = await import('../adminLink.ts'); } catch { /* old Node */ }
const t = L ? test : test.skip;

t('an admin link names a section and optionally a digest draft', () => {
  assert.deepEqual(L.parseAdminLink('?tab=admin&section=digests&draft=d-0123456789abcdef'), { section: 'digests', draft: 'd-0123456789abcdef' });
  assert.deepEqual(L.parseAdminLink('?tab=admin&section=users'), { section: 'users' });
});

t('anything else is not an admin link', () => {
  for (const bad of ['', '?tab=configure&section=digests', '?tab=admin', '?tab=admin&section=../x', '?tab=admin&section=digests&draft=nope',
    '?tab=admin&section=digests&draft=d-0123456789abcdeg', '?tab=admin&section=' + 'a'.repeat(40)]) {
    assert.equal(L.parseAdminLink(bad), null, bad);
  }
});
