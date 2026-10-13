// node --test - a motor load runs in the BACKGROUND behind an already-open tab (owner
// 2026-10-13: the load takes seconds, the click must open the view at once).  Imports
// lib/familyLoad.ts and lib/catalogRows.ts (Node >= 22.18 / 24; else skipped).
import test from 'node:test';
import assert from 'node:assert/strict';

let F = null; let C = null;
try { F = await import('../familyLoad.ts'); C = await import('../catalogRows.ts'); } catch { /* old Node */ }
const t = F && C ? test : test.skip;

t('the view opens by role: Geometry for the owner, Configure for a standard user', () => {
  assert.equal(C.loadTargetTab(true, false), 'geometry');     // owner / admin
  assert.equal(C.loadTargetTab(true, true), 'compare');       // role "user": Motors + Configure only
  assert.equal(C.loadTargetTab(false, true), 'compare');
  assert.equal(C.loadTargetTab(false, false), 'compare');     // no write access: no Geometry tab to edit
});

t('a load is "loading" at once, names the motor, and clears when it ends well', () => {
  const seen = [];
  const off = F.subscribeFamilyLoad(() => seen.push(F.getFamilyLoad().status));
  const tok = F.beginFamilyLoad('CIANO10 200 opt', 'L155 motor', 'rated 1x9 mm');
  assert.equal(F.getFamilyLoad().status, 'loading');
  assert.equal(F.familyLoadName(F.getFamilyLoad()), 'CIANO10 200 opt / L155 motor / rated 1x9 mm');
  F.finishFamilyLoad(tok);
  assert.equal(F.getFamilyLoad().status, 'idle');
  assert.deepEqual(seen, ['loading', 'idle']);
  off();
});

t('a failed load stays on screen as one short line until dismissed', () => {
  const tok = F.beginFamilyLoad('D', 'L155 motor', 'rated');
  F.failFamilyLoad(tok, 'cannot activate D / L155 motor: die is locked\nsecond line of detail');
  const s = F.getFamilyLoad();
  assert.equal(s.status, 'error');
  assert.equal(s.message, 'cannot activate D / L155 motor: die is locked');   // one line
  F.dismissFamilyLoad();
  assert.equal(F.getFamilyLoad().status, 'idle');
});

t('a running load cannot be dismissed; an empty reason is still spoken', () => {
  const tok = F.beginFamilyLoad('D', 'C', 'x');
  F.dismissFamilyLoad();
  assert.equal(F.getFamilyLoad().status, 'loading');
  F.failFamilyLoad(tok, '');
  assert.equal(F.getFamilyLoad().status, 'error');
  assert.notEqual(F.getFamilyLoad().message, '');
  F.dismissFamilyLoad();
});

t('the end of an older load never clears or fails the line of a newer one', () => {
  const a = F.beginFamilyLoad('D', 'A', 'x');
  const b = F.beginFamilyLoad('D', 'B', 'x');
  F.finishFamilyLoad(a);
  assert.equal(F.getFamilyLoad().status, 'loading');
  F.failFamilyLoad(a, 'late failure of A');
  assert.equal(F.getFamilyLoad().status, 'loading');
  assert.equal(F.getFamilyLoad().config, 'B');
  F.finishFamilyLoad(b);
  assert.equal(F.getFamilyLoad().status, 'idle');
});
