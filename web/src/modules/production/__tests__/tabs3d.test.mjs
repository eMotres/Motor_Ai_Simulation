// node --test: where the "3D" tab sits and who sees it; and that the motor model is gone from every other Production place.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { join, dirname } from 'node:path';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const read = (p) => readFileSync(join(root, p), 'utf8');
let H = null;
try { H = await import('../helpers.ts'); } catch { /* old Node */ }
const t = H ? test : test.skip;

test('TABS puts 3d right after bom', () => {
  const m = /const TABS = \[([^\]]*)\] as const/.exec(read('components/ProductionPage.tsx'));
  const tabs = m[1].split(',').map((x) => x.trim().replace(/'/g, ''));
  assert.equal(tabs[tabs.indexOf('bom') + 1], '3d');
});

t('members see 3d wherever they see BOM; a project viewer does not', () => {
  const tabs = ['bom', '3d', 'tooling', 'assembly', 'documents', 'tests', 'discussion'];
  for (const role of ['owner', 'engineer', 'oem', 'factory', null]) assert.ok(H.visibleProductionTabs(tabs, role, 0).includes('3d'), String(role));
  assert.deepEqual(H.visibleProductionTabs(tabs, 'viewer', 0), ['documents', 'tests', 'discussion']);
  assert.ok(!H.VIEWER_TABS.includes('3d'));
});

test('Documents has no motor-model row (so a viewer never had the model there)', () => {
  assert.ok(!/model3d|model\.glb|model\.step/i.test(read('components/DocumentsPanel.tsx')));
});

test('AssemblyProcess and PartCard no longer draw the motor model or download its STEP', () => {
  for (const f of ['components/AssemblyProcess.tsx', 'components/PartCard.tsx']) {
    const s = read(f);
    assert.ok(!/UnifiedViewer|viewer3d|api\/model3d|downloadModelStep|model3d/.test(s), f);
  }
  assert.ok(/optional_step/.test(read('components/PartCard.tsx')), 'the part\'s own STEP button stays');
});

test('the Tooling files keep their own 3-D view', () => {
  assert.ok(/UnifiedViewer/.test(read('components/ToolingFiles.tsx')));
});

test('the 3d tab has no address of its own yet (appLink.ts does not know it): the bar returns to the root', () => {
  assert.ok(/if \(i\.tab === '3d'\) return 'ROOT';/.test(read('pageAddress.ts')));
});

t('modelViewFamilies: step, part, all, electronics', () => {
  const bi = (en) => ({ en, zh: en, src_lang: 'en' });
  const st = (id, after, names) => ({ id, n: 1, title: bi(id), kind: 'operation', parts: [], after, note: null, parts_resolved: names.map((n, i) => ({ id: `${id}${i}`, name: bi(n) })) });
  const steps = [st('a', [], ['Coil 40_12']), st('b', ['a'], ['Magnet 40_12'])];
  const none = { electronics: false, steps, stepId: null, part: null };
  assert.equal(H.modelViewFamilies(none).only, null);
  const r = H.modelViewFamilies({ ...none, stepId: 'b' });
  assert.deepEqual(r.only, ['Magnets', 'Coils']); assert.deepEqual(r.highlight, ['Magnets']);
  assert.deepEqual(H.modelViewFamilies({ ...none, part: { name: bi('Coil 40_12') } }).only, ['Coils']);
  assert.equal(H.modelViewFamilies({ ...none, electronics: true, stepId: 'b' }).only, null);
});
