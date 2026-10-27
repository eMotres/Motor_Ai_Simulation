// node --test — the pure parts of the digest settings block and the Admin list (owner 2026-10-28: auto / approve / off; approval to send by hand).
import test from 'node:test';
import assert from 'node:assert/strict';

let M = null;
try { M = await import('../digestModel.ts'); } catch { /* old Node */ }
const t = M ? test : test.skip;

const view = { mode: 'auto', frequency: 'daily', recipients: 'all', chosen: [] };

t('three modes exist: auto (default), approve and off - nothing else can be built', () => {
  assert.deepEqual([...M.DIGEST_MODES], ['auto', 'approve', 'off']);
  assert.deepEqual(M.settingsChange(view, { mode: 'auto' }), { mode: 'auto' });
  assert.deepEqual(M.settingsChange(view, { mode: 'approve' }), { mode: 'approve' });
  assert.equal(M.settingsChange(view, { mode: 'automatic' }), null);
  assert.deepEqual(M.settingsChange(view, { mode: 'off' }), { mode: 'off' });
  assert.deepEqual(M.settingsChange(view, { mode: 'approve', frequency: 'weekly' }), { mode: 'approve', frequency: 'weekly' });
  assert.equal(M.settingsChange(view, { frequency: 'hourly' }), null);
  assert.equal(M.settingsChange(view, {}), null);
});

t('recipients: all or a chosen list, normalised', () => {
  assert.deepEqual(M.settingsChange(view, { recipients: 'chosen', chosen: [' Anna@Example.com '] }), { recipients: 'chosen', chosen: ['anna@example.com'] });
  assert.equal(M.settingsChange(view, { recipients: 'everyone' }), null);
  assert.deepEqual(M.toggleChosen(['b@x.com'], 'A@x.com', true), ['a@x.com', 'b@x.com']);
  assert.deepEqual(M.toggleChosen(['a@x.com', 'b@x.com'], 'a@x.com', false), ['b@x.com']);
  assert.ok(M.isListed({ recipients: 'all', chosen: [] }, 'z@x.com'));
  assert.ok(!M.isListed({ recipients: 'chosen', chosen: ['a@x.com'] }, 'z@x.com'));
});

t('settings paths are encoded', () => {
  assert.equal(M.settingsPath({ kind: 'project', id: 'erium-x1' }), '/api/digest/settings/project/erium-x1');
  assert.equal(M.settingsPath({ kind: 'production', die: 'CIANO14 40 new', config: 'L12' }), '/api/digest/settings/production/CIANO14%2040%20new/L12');
});

t('the Admin list: pending first, newest on top; the send body is the explicit approval', () => {
  const rows = [
    { id: 'a', status: 'sent', updated_at: '2026-10-25T10:00:00Z' }, { id: 'b', status: 'pending', updated_at: '2026-10-26T06:00:00Z' },
    { id: 'c', status: 'pending', updated_at: '2026-10-26T07:00:00Z' }, { id: 'd', status: 'discarded', updated_at: '2026-10-26T08:00:00Z' },
  ];
  const { pending, history } = M.splitDrafts(rows);
  assert.deepEqual(pending.map((r) => r.id), ['c', 'b']);
  assert.deepEqual(history.map((r) => r.id), ['d', 'a']);
  const h = 'a'.repeat(64);
  assert.deepEqual(M.approveBody(h), { approve: true, content_hash: h });
});

t('partly sent drafts stay in the waiting group; superseded ones go to the history', () => {
  const rows = [
    { id: 'p', status: 'partial', updated_at: '2026-10-26T09:00:00Z' }, { id: 's', status: 'superseded', updated_at: '2026-10-26T10:00:00Z' },
    { id: 'w', status: 'pending', updated_at: '2026-10-26T08:00:00Z' },
  ];
  const { pending, history } = M.splitDrafts(rows);
  assert.deepEqual(pending.map((r) => r.id), ['p', 'w']);
  assert.deepEqual(history.map((r) => r.id), ['s']);
});

t('there is no approval without the hash of the previewed text', () => {
  for (const bad of [undefined, null, '', 'abc', 'g'.repeat(64), 'A'.repeat(64), 'a'.repeat(63)]) assert.throws(() => M.approveBody(bad), /content hash/);
});
