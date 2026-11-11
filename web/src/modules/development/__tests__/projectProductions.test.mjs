// node --test: the Production view of a project (owner 2026-11-02) - one block per linked motor with a production, the counts the
// server let the caller see, the derived read-only viewer in the Production module.  Synthetic data only.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { hasProductionView, productionBlocks } from '../projectProductions.ts';

let H = null;
try { H = await import('../../production/helpers.ts'); } catch { /* old Node */ }

const read = (rel) => readFileSync(new URL(rel, import.meta.url), 'utf8');
const get = (obj, path) => path.split('.').reduce((o, k) => o?.[k], obj);
const entry = (config, over = {}) => ({
  die: 'DieA', config, title: { en: `Motor ${config}`, zh: `电机 ${config}` }, revision: '1.0', status: 'active',
  access: 'project', role: 'viewer', documents: 1, discussions: 0, ...over,
});

test('several linked motors (M1, G1, G2) give several blocks, in the project order, duplicates dropped', () => {
  const blocks = productionBlocks([entry('M1'), entry('G1'), entry('G2'), entry('m1')]);
  assert.deepEqual(blocks.map((b) => b.config), ['M1', 'G1', 'G2']);
  assert.ok(blocks.every((b) => b.canOpen));
});

test('a block shows only the counts the server sent; tests summary in the Tests sub-tab order', () => {
  const [noTests] = productionBlocks([entry('M1')]);
  assert.deepEqual(noTests.counts.map((c) => c.key), ['documents', 'discussions']);     // a customer without the tests flag
  const [withTests] = productionBlocks([entry('M1', { tests: { count: 5, by_kind: { load: 3, parameters: 2 } } })]);
  assert.deepEqual(withTests.counts, [{ key: 'tests', value: 5 }, { key: 'documents', value: 1 }, { key: 'discussions', value: 0 }]);
  assert.deepEqual(withTests.testKinds, [{ kind: 'parameters', count: 2 }, { kind: 'load', count: 3 }]);
});

test('title in the UI language, revision, readiness only with parts', () => {
  const [zh] = productionBlocks([entry('G1', { readiness: { parts_total_count: 10, parts_received_count: 4, received_pct: 40.4, late_count: 1, ready_for_assembly: false } })], 'zh-CN');
  assert.equal(zh.title, '电机 G1');
  assert.deepEqual(zh.readiness, { received: 4, total: 10, pct: 40, late: 1, ready: false });
  const [en] = productionBlocks([entry('G1', { title: { en: '', zh: '电机' }, readiness: { parts_total_count: 0 } })]);
  assert.equal(en.title, '电机');
  assert.equal(en.readiness, null);
});

test('access none (a global engineer outside the project) names the production but offers no Production view', () => {
  const rows = [entry('M1', { access: 'none', role: null, documents: undefined, discussions: undefined })];
  assert.equal(productionBlocks(rows)[0].canOpen, false);
  assert.deepEqual(productionBlocks(rows)[0].counts, []);
  assert.equal(hasProductionView(rows), false);
  assert.equal(hasProductionView([]), false);
  assert.equal(hasProductionView([...rows, entry('G1', { access: 'member', role: 'admin' })]), true);
});

test('the Production module gives a project viewer documents, tests and discussion only', { skip: !H }, () => {
  const tabs = ['bom', 'tooling', 'assembly', 'timeline', 'documents', 'tests', 'discussion', 'proposals', 'changes'];
  const viewerProd = { members: [], access: 'project' };
  assert.equal(H.productionRole(viewerProd, 'c@x.test', false), 'viewer');
  assert.equal(H.productionRole(viewerProd, 'a@x.test', true), 'viewer');            // the server decided
  assert.equal(H.productionRole({ members: [{ email: 'f@x.test', role: 'oem' }] }, 'F@x.test', false), 'oem');
  assert.deepEqual(H.visibleProductionTabs(tabs, 'viewer', 0), ['documents', 'tests', 'discussion']);
  assert.deepEqual(H.visibleProductionTabs(tabs, 'oem', 0), tabs.filter((t) => t !== 'tooling'));
  assert.deepEqual(H.visibleProductionTabs(tabs, 'engineer', 0), tabs);
  assert.equal(H.canEditRole('viewer'), false);
  assert.equal(H.canProposeRole('viewer'), false);
  assert.equal(H.canDownloadProductionDocuments('viewer'), false);                 // no ZIP of everything
  // a viewer picks among the threads it received, named by the part name the server attached
  const threads = [{ part_id: null, messages: [] }, { part_id: 'p1', part_name: { en: 'Stator', zh: '定子' }, messages: [] }];
  assert.deepEqual(H.threadChoices(threads, [], true), [{ key: 'p1', label: 'Stator' }]);
  assert.deepEqual(H.threadChoices(threads, [{ id: 'p2', name: { en: 'Rotor', zh: '' } }], false), [{ key: 'p2', label: 'Rotor' }]);
});

test('the project page wires the Production view; every label exists in en and zh-CN', () => {
  const page = read('../components/DevelopmentPage.tsx');
  assert.match(page, /hasProductionView\(productions\)/);
  assert.match(page, /view === 'production' && <ProjectProductions/);
  const comp = read('../components/ProjectProductions.tsx');
  // 'development': the production's back arrow and the browser's Back return to this project (mobile audit 2026-11)
  assert.match(comp, /openProduction\(b\.die, b\.config, 'development'\)/);
  const keys = ['tabs.production', 'productionView.none', 'productionView.tip', 'productionView.revision', 'productionView.open',
    'productionView.openTip', 'productionView.readiness', 'productionView.readinessTip',
    ...['active', 'paused', 'archived'].map((s) => `productionView.status.${s}`),
    ...['tests', 'documents', 'discussions'].flatMap((k) => [`productionView.count.${k}`, `productionView.count.${k}Tip`]),
    ...['parameters', 'free_run', 'load'].map((k) => `productionView.testKind.${k}`)];
  for (const lang of ['en', 'zh-CN']) {
    const dev = JSON.parse(read(`../../../locales/${lang}/development.json`));
    for (const k of keys) assert.equal(typeof get(dev, k), 'string', `${lang} ${k}`);
    const prod = JSON.parse(read(`../../../locales/${lang}/production.json`));
    for (const k of ['role.viewer', 'discussion.viewerTip', 'discussion.customerVisible', 'discussion.factoryOnly', 'discussion.customerVisibleTip']) {
      assert.equal(typeof get(prod, k), 'string', `${lang} ${k}`);
    }
  }
});
