// node --test - the top toolbar rules of the model viewers (lib/viewerToolbar.ts): who gets which items, what is open by default at which width.
import test from 'node:test';
import assert from 'node:assert/strict';

let V = null;
try { V = await import('../viewerToolbar.ts'); } catch { /* old Node */ }
const t = V ? test : test.skip;

const ctx = { canDownload: true, models: 2, preparing: false, stepAvailable: true, hasPdf: false };
const ids = (c) => V.model3dToolbar(c).map((e) => e.id);

t('with the NDA the host bar keeps the motor picker, STEP download and info icon', () => {
  assert.deepEqual(ids(ctx), ['motor', 'download', 'info']);
  assert.deepEqual(ids({ ...ctx, hasPdf: true }), ['motor', 'download', 'yourNda', 'info']);
});

t('without the NDA the host bar keeps the picker, "after NDA" note and Sign', () => {
  const no = { ...ctx, canDownload: false };
  assert.deepEqual(ids({ ...no, models: 1 }), ['ndaNote', 'sign']);
  assert.deepEqual(ids({ ...no, models: 2 }), ['motor', 'ndaNote', 'sign']);
  assert.deepEqual(ids({ ...no, models: 1, preparing: true }), ['motor', 'ndaNote', 'sign']);
  for (const tool of ['explode', 'section', 'measure', 'download', 'yourNda', 'info']) assert.ok(!ids(no).includes(tool), tool);
  // the signed-NDA link never shows without the NDA, even with a PDF
  assert.ok(!ids({ ...no, hasPdf: true }).includes('yourNda'));
});

t('with the NDA signed but verification open, the host bar shows its status line', () => {
  const v = { ...ctx, canDownload: false, ndaSigned: true };
  assert.deepEqual(ids({ ...v, models: 1 }), ['verify']);
  assert.deepEqual(ids({ ...v, models: 2 }), ['motor', 'verify']);
  for (const x of ['sign', 'ndaNote', 'download', 'yourNda']) assert.ok(!ids(v).includes(x), x);
  // verified: ndaSigned no longer matters, the tools are back
  assert.ok(ids({ ...ctx, ndaSigned: true }).includes('download'));
});

t('the STEP download is disabled when the model has no STEP; the picker is disabled with no model', () => {
  const by = (c) => Object.fromEntries(V.model3dToolbar(c).map((e) => [e.id, e.enabled]));
  assert.equal(by({ ...ctx, stepAvailable: false }).download, false);
  assert.equal(by(ctx).download, true);
  assert.equal(by({ ...ctx, models: 0 }).motor, false);
});

t('narrow screens: the tree starts collapsed under 900 px, the drawer is closed, a phone gets the whole width', () => {
  assert.equal(V.NARROW_PX, 900);
  assert.equal(V.isNarrow(899), true);
  assert.equal(V.isNarrow(900), false);
  assert.equal(V.drawerStartsOpen(null), false);
  assert.equal(V.drawerStartsOpen('0'), false);
  assert.equal(V.drawerStartsOpen('1'), true);
  assert.equal(V.drawerWidth(1400), 420);
  assert.equal(V.drawerWidth(390), 374);
  assert.equal(V.drawerWidth(200), 260);
});

t('Geometry > Full toolbar: the drawer toggle always; badge and Rebuild now only for a model that is not current', () => {
  const base = { kit: false, hasModel: true, badge: 'none', canWrite: true };
  assert.deepEqual(V.fullToolbar(base), ['params']);
  assert.deepEqual(V.fullToolbar({ ...base, kit: true }), ['params', 'kit']);
  assert.deepEqual(V.fullToolbar({ ...base, badge: 'rebuilding' }), ['params', 'badge']);
  assert.deepEqual(V.fullToolbar({ ...base, badge: 'queued' }), ['params', 'badge']);
  assert.deepEqual(V.fullToolbar({ ...base, badge: 'stale' }), ['params', 'badge', 'rebuildNow']);
  assert.deepEqual(V.fullToolbar({ ...base, badge: 'failed' }), ['params', 'badge', 'rebuildNow']);
  assert.deepEqual(V.fullToolbar({ ...base, badge: 'failed', canWrite: false }), ['params', 'badge']);   // read-only: no rebuild button
  assert.deepEqual(V.fullToolbar({ ...base, hasModel: false, badge: 'failed' }), ['params']);
});

t('the simplified-STEP button is in the toolbar for EVERY signed-in account, with and without the NDA (owner 2026-10-15)', () => {
  const by2 = (c) => Object.fromEntries(V.model3dToolbar(c).map((e) => [e.id, e.enabled]));
  const no = { ...ctx, canDownload: false, models: 1 };
  assert.deepEqual(ids({ ...no, simplified: true }), ['simplified', 'ndaNote', 'sign']);
  assert.deepEqual(ids({ ...no, simplified: true, ndaSigned: true }), ['simplified', 'verify']);
  assert.deepEqual(ids({ ...ctx, simplified: true }), ['motor', 'download', 'simplified', 'info']);
  assert.equal(by2({ ...no, simplified: true }).simplified, true);
  assert.equal(by2({ ...no, simplified: false }).simplified, false);                 // shown, disabled while the files are not built
  assert.ok(!ids(no).includes('simplified'));                                       // not offered when the flag is not given
});
