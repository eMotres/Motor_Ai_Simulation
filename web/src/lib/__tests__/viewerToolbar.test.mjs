// node --test - the top toolbar rules of the model viewers (lib/viewerToolbar.ts): who gets which items, what is open by default at which width.
import test from 'node:test';
import assert from 'node:assert/strict';

let V = null;
try { V = await import('../viewerToolbar.ts'); } catch { /* old Node */ }
const t = V ? test : test.skip;

const ctx = { canDownload: true, models: 2, preparing: false, stepAvailable: true, hasPdf: false };
const ids = (c) => V.model3dToolbar(c).map((e) => e.id);

t('with the NDA the toolbar has the motor picker, Fit, exploded view, section, measure, the STEP download and the info icon', () => {
  assert.deepEqual(ids(ctx), ['motor', 'fit', 'explode', 'section', 'measure', 'download', 'info']);
  assert.deepEqual(ids({ ...ctx, hasPdf: true }), ['motor', 'fit', 'explode', 'section', 'measure', 'download', 'yourNda', 'info']);
});

t('without the NDA there is no tool: picker only when there is a choice, Fit, the "after NDA" note and Sign', () => {
  const no = { ...ctx, canDownload: false };
  assert.deepEqual(ids({ ...no, models: 1 }), ['fit', 'ndaNote', 'sign']);
  assert.deepEqual(ids({ ...no, models: 2 }), ['motor', 'fit', 'ndaNote', 'sign']);
  assert.deepEqual(ids({ ...no, models: 1, preparing: true }), ['motor', 'fit', 'ndaNote', 'sign']);
  for (const tool of ['explode', 'section', 'measure', 'download', 'yourNda', 'info']) assert.ok(!ids(no).includes(tool), tool);
  // the signed-NDA link never shows without the NDA, even with a PDF
  assert.ok(!ids({ ...no, hasPdf: true }).includes('yourNda'));
});

t('with the NDA signed but the verification open, the status line replaces the Sign button and there is still no tool', () => {
  const v = { ...ctx, canDownload: false, ndaSigned: true };
  assert.deepEqual(ids({ ...v, models: 1 }), ['fit', 'verify']);
  assert.deepEqual(ids({ ...v, models: 2 }), ['motor', 'fit', 'verify']);
  for (const x of ['sign', 'ndaNote', 'explode', 'section', 'measure', 'download', 'yourNda']) assert.ok(!ids(v).includes(x), x);
  // verified: ndaSigned no longer matters, the tools are back
  assert.ok(ids({ ...ctx, ndaSigned: true }).includes('download'));
});

t('the STEP download is disabled when the model has no STEP; the picker is disabled with no model', () => {
  const by = (c) => Object.fromEntries(V.model3dToolbar(c).map((e) => [e.id, e.enabled]));
  assert.equal(by({ ...ctx, stepAvailable: false }).download, false);
  assert.equal(by(ctx).download, true);
  assert.equal(by({ ...ctx, models: 0 }).motor, false);
});

t('the toolbar items are the same tools as the old side panel (the permission rule is lib/model3d panelParts)', async () => {
  const M = await import('../model3d.ts');
  const tools = (c) => ids(c).filter((i) => ['explode', 'section', 'measure', 'download'].includes(i));
  for (const can of [true, false]) {
    const parts = M.panelParts(can);
    assert.equal(tools({ ...ctx, canDownload: can }).length > 0, parts.includes('download'));
    assert.equal(tools({ ...ctx, canDownload: can }).includes('explode'), parts.includes('explode'));
    assert.equal(tools({ ...ctx, canDownload: can }).includes('section'), parts.includes('section'));
    assert.equal(tools({ ...ctx, canDownload: can }).includes('measure'), parts.includes('measure'));
  }
});

t('narrow screens: the tree starts collapsed under 900 px, the drawer is closed, a phone gets the whole width', () => {
  assert.equal(V.NARROW_PX, 900);
  assert.equal(V.isNarrow(899), true);
  assert.equal(V.isNarrow(900), false);
  assert.equal(V.treeStartsCollapsed(390), true);
  assert.equal(V.treeStartsCollapsed(1400), false);
  assert.equal(V.drawerStartsOpen(null), false);
  assert.equal(V.drawerStartsOpen('0'), false);
  assert.equal(V.drawerStartsOpen('1'), true);
  assert.equal(V.drawerWidth(1400), 420);
  assert.equal(V.drawerWidth(390), 374);
  assert.equal(V.drawerWidth(200), 260);
});

t('Geometry > Full toolbar: the drawer toggle always; badge and Rebuild now only for a model that is not current', () => {
  const base = { kit: false, hasModel: true, badge: 'none', canWrite: true };
  assert.deepEqual(V.fullToolbar(base), ['params', 'fit']);
  assert.deepEqual(V.fullToolbar({ ...base, kit: true }), ['params', 'kit', 'fit']);
  assert.deepEqual(V.fullToolbar({ ...base, badge: 'rebuilding' }), ['params', 'badge', 'fit']);
  assert.deepEqual(V.fullToolbar({ ...base, badge: 'queued' }), ['params', 'badge', 'fit']);
  assert.deepEqual(V.fullToolbar({ ...base, badge: 'stale' }), ['params', 'badge', 'rebuildNow', 'fit']);
  assert.deepEqual(V.fullToolbar({ ...base, badge: 'failed' }), ['params', 'badge', 'rebuildNow', 'fit']);
  assert.deepEqual(V.fullToolbar({ ...base, badge: 'failed', canWrite: false }), ['params', 'badge', 'fit']);   // read-only: no rebuild button
  assert.deepEqual(V.fullToolbar({ ...base, hasModel: false, badge: 'failed' }), ['params']);
});

t('the simplified-STEP button is in the toolbar for EVERY signed-in account, with and without the NDA (owner 2026-10-15)', () => {
  const by2 = (c) => Object.fromEntries(V.model3dToolbar(c).map((e) => [e.id, e.enabled]));
  const no = { ...ctx, canDownload: false, models: 1 };
  assert.deepEqual(ids({ ...no, simplified: true }), ['fit', 'simplified', 'ndaNote', 'sign']);
  assert.deepEqual(ids({ ...no, simplified: true, ndaSigned: true }), ['fit', 'simplified', 'verify']);
  assert.deepEqual(ids({ ...ctx, simplified: true }), ['motor', 'fit', 'explode', 'section', 'measure', 'download', 'simplified', 'info']);
  assert.equal(by2({ ...no, simplified: true }).simplified, true);
  assert.equal(by2({ ...no, simplified: false }).simplified, false);                 // shown, disabled while the files are not built
  assert.ok(!ids(no).includes('simplified'));                                       // not offered when the flag is not given
});
