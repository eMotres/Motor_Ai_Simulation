// node --test: the catalog filters (owner 2026-11-17), RENDERED with the real EN and zh-CN locale files: the controls of the
// title line (admin and member), and the table applying the shared filter store (stage, running, "N of M", the state).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { join, dirname, resolve } from 'node:path';
import { tmpdir } from 'node:os';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';

const here = dirname(fileURLToPath(import.meta.url));
const src = resolve(here, '..', '..', '..');
const tmp = mkdtempSync(join(tmpdir(), 'filters-test-'));
const out = join(tmp, 'bundle.mjs');
const rel = (p) => resolve(src, p).split('\\').join('/');
const mem = new Map();
globalThis.localStorage = { getItem: (k) => (mem.has(k) ? mem.get(k) : null), setItem: (k, v) => mem.set(k, String(v)), removeItem: (k) => mem.delete(k) };

writeFileSync(join(tmp, 'entry.jsx'), `
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { I18nextProvider } from 'react-i18next';
import { createI18n } from '${rel('i18n/core.ts')}';
import CatalogFilters from '${rel('components/catalog/CatalogFilters.tsx')}';
import CatalogByDie from '${rel('components/catalog/CatalogByDie.tsx')}';
export { projectStateItems } from '${rel('components/catalog/CatalogByDie.tsx')}';
export { useCatalogFilterStore, catalogFilterApi } from '${rel('stores/catalogFilterStore.ts')}';
import enCommon from '${rel('locales/en/common.json')}';
import zhCommon from '${rel('locales/zh-CN/common.json')}';
import enMotors from '${rel('locales/en/motors.json')}';
import zhMotors from '${rel('locales/zh-CN/motors.json')}';
import enDev from '${rel('locales/en/development.json')}';
import zhDev from '${rel('locales/zh-CN/development.json')}';
export const makeI18n = (lng) => createI18n({ lng, resources: { en: { common: enCommon, motors: enMotors, development: enDev },
  'zh-CN': { common: zhCommon, motors: zhMotors, development: zhDev } } });
const wrap = (i18n, el) => renderToStaticMarkup(React.createElement(I18nextProvider, { i18n }, el));
export const filters = (i18n, isAdmin, highlightTitle = null) => wrap(i18n, React.createElement(CatalogFilters, { isAdmin, highlightTitle }));
const noop = () => {};
export const table = (i18n, props) => wrap(i18n, React.createElement(CatalogByDie, { thumbs: () => null, rated: () => null, active: null,
  canWrite: true, standardUser: false, busy: false, actions: new Proxy({}, { get: () => noop }), ...props }));
`);
await build({
  entryPoints: [join(tmp, 'entry.jsx')], bundle: true, platform: 'node', format: 'esm',
  outfile: out, jsx: 'automatic', logLevel: 'error', nodePaths: [resolve(src, '..', 'node_modules')],
  define: { 'import.meta.env': '{"DEV":false,"VITE_API_URL":"http://api.test"}' },
  banner: { js: "import { createRequire } from 'module'; const require = createRequire(import.meta.url);" },
});
const m = await import(pathToFileURL(out).href);
process.on('exit', () => { try { rmSync(tmp, { recursive: true, force: true }); } catch { /* */ } });
const ready = (i) => new Promise((r) => (i.isInitialized ? r() : i.on('initialized', () => r())));
const decode = (s) => s.replace(/&#x27;|&#39;/g, "'").replace(/&quot;/g, '"').replace(/&amp;/g, '&');
const S = m.useCatalogFilterStore;
// a server render reads the store's server snapshot, which zustand takes from the INITIAL state: point it at the current one
m.catalogFilterApi.getInitialState = m.catalogFilterApi.getState;

const cfg = (name, d) => ({ name, designation: d, designation_complete: true, family: d.split('_')[0], beside: {} });
const dies = [{ name: 'D', slots: 12, poles: 14, stator_diameter: 40, configs: [cfg('A', 'CIANO14 40_10 7F2SY'), cfg('B', 'CIANO14 40_11 7F2SY'), cfg('C', 'CIANO14 40_12 7F2SY')] }];
const overview = { items: { 'D/A': { projects: [{ id: 'p1', link_status: 'current' }], productions: ['D/A'], stage: 'in_production' },
    'D/B': { projects: [{ id: 'p1', link_status: 'current' }], productions: [], computed: true } },
  projects: { p1: { id: 'p1', title: 'Pump', status: 'design', stage: 'design' } },
  productions: { 'D/A': { die: 'D', config: 'A', status: 'active', current_revision: '1' } } };
const jobs = { servers: [], motors: { 'D/C': [{ tag: 'j', server: 'us', state: 'running', kind: 'single', die: 'D', configuration: 'C' }] }, dies: {}, unknown: [], unnamed: [] };
const motorsShown = (html) => [...decode(html).matchAll(/>(CIANO14 40_1\d 7F2SY)</g)].map((x) => x[1]);

for (const [lng, active, running, sets] of [['en', 'Active', 'Running', 'My filters'], ['zh-CN', '进行中', '运行中', '我的筛选']]) {
  test(`${lng}: an admin's title line holds the state choice, the running toggle and the saved sets; a member only the sets`, async () => {
    const i18n = m.makeI18n(lng); await ready(i18n);
    S.setState({ options: { stages: ['simulation', 'in_production'], projects: [{ id: 'p1', title: 'Pump' }],
      counts: { active: 3, parked: 0, archived: 0, all: 3 }, shown: 3, total: 3 } });
    const admin = decode(m.filters(i18n, true));
    for (const id of ['filter-stage', 'filter-project', 'filter-running', 'filter-sets']) assert.ok(admin.includes(`data-testid="${id}"`), id);
    assert.ok(admin.includes(active) && admin.includes(running) && admin.includes(sets), admin);
    const member = decode(m.filters(i18n, false, 'Pump'));
    for (const id of ['filter-stage', 'filter-project', 'filter-running']) assert.ok(!member.includes(`data-testid="${id}"`), id);
    assert.ok(member.includes('data-testid="filter-highlighted"') && member.includes('Pump') && member.includes(sets), member);
    assert.ok(!/byDie\.|motors:/.test(admin + member));
  });
}

test('the table applies the store: stage, running; the count "N of M" and Clear appear only while a filter is on', async () => {
  const i18n = m.makeI18n('en'); await ready(i18n);
  S.getState().bindAccount('admin@example.test');
  S.getState().clear();
  assert.deepEqual(motorsShown(m.table(i18n, { dies, isAdmin: true, overview, jobs })), ['CIANO14 40_10 7F2SY', 'CIANO14 40_11 7F2SY', 'CIANO14 40_12 7F2SY']);
  S.getState().set({ stages: ['in_production'] });
  assert.deepEqual(motorsShown(m.table(i18n, { dies, isAdmin: true, overview, jobs })), ['CIANO14 40_10 7F2SY']);
  S.getState().set({ stages: [], running: true });
  assert.deepEqual(motorsShown(m.table(i18n, { dies, isAdmin: true, overview, jobs })), ['CIANO14 40_12 7F2SY']);
  // a member: the admin parts of a remembered filter do nothing (no Overview, no jobs for them)
  assert.equal(motorsShown(m.table(i18n, { dies, isAdmin: false, overview: null, jobs: null })).length, 3);
  S.setState({ options: { ...S.getState().options, shown: 1, total: 3 } });
  const line = decode(m.filters(i18n, true));
  assert.ok(line.includes('1 of 3 motors') && line.includes('data-testid="filter-clear"'), line);
  S.getState().clear();
  assert.ok(!decode(m.filters(i18n, true)).includes('filter-count'));
});

// ── owner 2026-11-25: Active shows no old motors by default; archive / park findable on the row ──
const oldDies = [{ name: 'D', slots: 12, poles: 14, stator_diameter: 40, layer: 'shared',
  configs: [cfg('A', 'CIANO14 40_10 7F2SY'), cfg('B', 'CIANO14 40_10 7F2SY'), cfg('C', 'CIANO14 40_11 7F2SY')] }];
const oldOverview = { items: { 'D/A': { projects: [{ id: 'p1', link_status: 'current' }], productions: [] } },
  projects: { p1: { id: 'p1', title: 'Pump', status: 'design', stage: 'design' } }, productions: {} };
const rowsOf = (html) => [...decode(html).matchAll(/data-testid="by-die-motor"[^>]*>[\s\S]*?>(CIANO14 40_1\d 7F2SY)</g)].length;

for (const [lng, showOld] of [['en', 'Show old (1)'], ['zh-CN', '显示旧版本（1）']]) {
  test(`${lng}: Active hides the old copy, "Show old (n)" brings it back with its Archive arrow`, async () => {
    const i18n = m.makeI18n(lng); await ready(i18n);
    S.getState().bindAccount(`old-${lng}@example.test`);
    S.getState().set({ state: 'active', showOld: false });
    const hidden = decode(m.table(i18n, { dies: oldDies, isAdmin: true, overview: oldOverview, jobs: null }));
    assert.equal(rowsOf(hidden), 2);
    assert.ok(!hidden.includes('data-old="true"'), 'the old copy is not in the Active view');
    // (a server render runs no effects: the options the table reports are set here; the counts rule is in catalogByDie.test.mjs)
    S.setState({ options: { stages: [], projects: [], counts: { active: 2, parked: 0, archived: 0, all: 3 }, shown: 2, total: 3, old: 1 } });
    const line = decode(m.filters(i18n, true));
    assert.ok(line.includes('data-testid="filter-show-old"') && line.includes(showOld), line);
    assert.ok(!decode(m.filters(i18n, false)).includes('filter-show-old'), 'a member has no state view and no switch');
    S.getState().set({ showOld: true });
    const shown = decode(m.table(i18n, { dies: oldDies, isAdmin: true, overview: oldOverview, jobs: null }));
    assert.equal(rowsOf(shown), 3);
    assert.equal((shown.match(/data-old="true"/g) || []).length, 1);
    assert.ok(shown.includes('data-testid="by-die-old-open"'), 'an admin opens Archive from the "old" chip');
    S.getState().clear();
    assert.equal(S.getState().filter.showOld, true, 'Clear keeps the view switch');
    // a member's catalog is unchanged: no state filter, the old copy stays (muted), no archive arrow
    const member = decode(m.table(i18n, { dies: oldDies, isAdmin: false, canWrite: false, overview: null, jobs: null }));
    assert.ok(!member.includes('by-die-old-open'));
    S.getState().set({ showOld: false });
    assert.ok(!/byDie\.|motors:/.test(hidden + line));
  });
}

test('the Projects column stands before Stage and Running (header and row); the motor menu parks / archives its projects', async () => {
  const i18n = m.makeI18n('en'); await ready(i18n);
  S.getState().set({ state: 'active', showOld: true });
  const html = decode(m.table(i18n, { dies: oldDies, isAdmin: true, overview: oldOverview, jobs: null }));
  const hdr = html.slice(html.indexOf('data-testid="by-die-header"'));
  assert.ok(hdr.indexOf('>Projects<') < hdr.indexOf('>Stage<') && hdr.indexOf('>Stage<') < hdr.indexOf('>Running<'), 'header order');
  const row = html.slice(html.indexOf('data-testid="by-die-motor"'));
  assert.ok(row.indexOf('by-die-projects') < row.indexOf('by-die-stage') && row.indexOf('by-die-stage') < row.indexOf('by-die-jobs'), 'row order');
  const calls = [];
  const t = (k, o) => (k === 'byDie.projectMenu.item' ? `${o.project}: ${o.action}` : k.split('.').pop());
  const items = m.projectStateItems({ projects: [{ id: 'p1', title: 'Pump', state: 'active' }] }, t, (id, st) => calls.push([id, st]));
  assert.deepEqual(items.map((i) => i.label), ['Pump: parked', 'Pump: archived']);
  items.forEach((i) => i.run());
  assert.deepEqual(calls, [['p1', 'parked'], ['p1', 'archived']]);
  assert.equal(i18n.t('motors:byDie.projectMenu.item', { project: 'Pump', action: i18n.t('motors:byDie.projectMenu.parked') }), 'Pump: Park');
  S.getState().set({ showOld: false });
});
