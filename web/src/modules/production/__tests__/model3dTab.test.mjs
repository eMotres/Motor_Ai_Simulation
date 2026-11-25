// node --test: the Production "3D" tab (owner 2026-11-25), server-rendered with the real EN / zh-CN locale files.  The viewer itself
// (three.js) is replaced by a stub that prints the props the tab hands it, so the test checks WHAT is shown without a GL context.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, writeFileSync, readFileSync } from 'node:fs';
import { join, dirname, resolve } from 'node:path';
import { tmpdir } from 'node:os';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';

const here = dirname(fileURLToPath(import.meta.url));
const src = resolve(here, '..', '..', '..');
const tmp = mkdtempSync(join(tmpdir(), 'model3d-tab-'));
const out = join(tmp, 'bundle.mjs');
const rel = (p) => resolve(src, p).split('\\').join('/');
writeFileSync(join(tmp, 'viewerStub.jsx'), `
import React from 'react';
export const UnifiedViewer = (p) => React.createElement('div', { 'data-viewer': '1', 'data-glb': p.glbUrl, 'data-only': p.onlyFamilies ? p.onlyFamilies.join(',') : 'ALL',
  'data-highlight': p.highlightFamilies ? p.highlightFamilies.join(',') : '', 'data-explode': String(p.defaultExplode ?? 0) });
`);
writeFileSync(join(tmp, 'entry.jsx'), `
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { I18nextProvider } from 'react-i18next';
import { createI18n } from '${rel('i18n/core.ts')}';
import { Model3DView } from '${rel('modules/production/components/Model3DTab.tsx')}';
import enP from '${rel('locales/en/production.json')}';
import zhP from '${rel('locales/zh-CN/production.json')}';
export const makeI18n = (lng) => createI18n({ lng, resources: { en: { production: enP }, 'zh-CN': { production: zhP } } });
export const view = (i18n, props) => renderToStaticMarkup(React.createElement(I18nextProvider, { i18n }, React.createElement(Model3DView, props)));
`);
const stubPlugin = { name: 'viewer-stub', setup(b) {
  b.onResolve({ filter: /viewer3d$/ }, () => ({ path: join(tmp, 'viewerStub.jsx') }));
} };
await build({ entryPoints: [join(tmp, 'entry.jsx')], bundle: true, platform: 'node', format: 'esm', outfile: out, jsx: 'automatic', logLevel: 'error',
  plugins: [stubPlugin], nodePaths: [resolve(src, '..', 'node_modules')], define: { 'import.meta.env': '{"DEV":false,"VITE_API_URL":"http://api.test"}' },
  banner: { js: "import { createRequire } from 'module'; const require = createRequire(import.meta.url);" } });
const m = await import(pathToFileURL(out).href);
process.on('exit', () => { try { rmSync(tmp, { recursive: true, force: true }); } catch { /* */ } });
const ready = (i) => new Promise((r) => (i.isInitialized ? r() : i.on('initialized', () => r())));
const decode = (s) => s.replace(/<style[^>]*>[^<]*<\/style>/g, '').replace(/&#x27;|&#39;/g, "'").replace(/&quot;/g, '"').replace(/&amp;/g, '&');

const bi = (en) => ({ en, zh: en, src_lang: 'en' });
const part = (id, en) => ({ id, name: bi(en) });
const step = (id, n, after, names) => ({ id, n, title: bi(`Step ${id}`), kind: 'operation', parts: [], after, note: null, parts_resolved: names.map((x, i) => part(`${id}_${i}`, x)) });
const steps = [step('s1', 1, [], ['Coil 40_12']), step('s5', 2, [], ['Magnet 40_12']), step('s9', 3, ['s1', 's5'], ['Bearing 618/8-2Z C3'])];
const parts = [part('p_coil', 'Coil 40_12'), part('p_lead', 'Lead wire')];
const ctx = { die: 'D1', config: 'C1', role: 'engineer', email: 'a@b', uiLang: 'en', canEdit: true, productType: 'motor', reload() {} };
const ready_state = (extra = {}) => ({ canDownload: true, entry: { die: 'D1', config: 'C1', view: 'full', step_available: true, version: { template: '3', built_at: '2026-11-20T10:00:00Z', param_hash: 'abc123def456' }, ...extra } });
const base = (over = {}) => ({ ctx, steps, parts, selection: { stepId: null, partId: null }, onSelect() {}, state: ready_state(), onDownload() {}, ...over });
const attr = (html, name) => (new RegExp(`data-${name}="([^"]*)"`).exec(html) || [])[1];

test('no model: one plain line, no viewer', async () => {
  const i18n = m.makeI18n('en'); await ready(i18n);
  const html = decode(m.view(i18n, base({ state: 'none' })));
  assert.ok(html.includes('This motor has no 3-D model yet.'), html);
  assert.ok(!html.includes('data-viewer') && !html.includes('caption'), html);
});

test('default is All: the full model, the step selector says All, revision and date shown, one STEP button', async () => {
  const i18n = m.makeI18n('en'); await ready(i18n);
  const html = decode(m.view(i18n, base()));
  assert.equal(attr(html, 'only'), 'ALL');
  assert.equal(attr(html, 'glb'), 'http://api.test/api/model3d/D1/C1/model.glb');
  assert.ok(html.includes('>All<'), html);
  assert.ok(html.includes('Model v3 · 2026-11-20'), html);
  assert.equal((html.match(/data-testid="model3d-download"/g) || []).length, 1);
});

test('the STEP button only for a caller who may download', async () => {
  const i18n = m.makeI18n('en'); await ready(i18n);
  const html = m.view(i18n, base({ state: { ...ready_state(), canDownload: false } }));
  assert.ok(!html.includes('model3d-download'), html);
});

test('a caller without the full view gets the NDA notice, not the viewer', async () => {
  const i18n = m.makeI18n('en'); await ready(i18n);
  const html = decode(m.view(i18n, base({ state: { ...ready_state({ view: 'simplified' }), canDownload: false } })));
  assert.ok(html.includes('Sign the NDA'), html);
  assert.ok(!html.includes('data-viewer'), html);
});

test('a step selected: its own families glow, earlier ones are faint (same props as the old step view)', async () => {
  const i18n = m.makeI18n('en'); await ready(i18n);
  const html = m.view(i18n, base({ selection: { stepId: 's9', partId: null } }));
  assert.deepEqual(attr(html, 'only').split(',').sort(), ['Bearings_back', 'Bearings_front', 'Coils', 'Magnets'].sort());
  assert.deepEqual(attr(html, 'highlight').split(',').sort(), ['Bearings_back', 'Bearings_front']);
  assert.equal(attr(html, 'explode'), '0.25');
});

test('a step with no parts at all: the old "no geometry" message, no viewer', async () => {
  const i18n = m.makeI18n('en'); await ready(i18n);
  const html = decode(m.view(i18n, base({ steps: [step('s0', 1, [], [])], selection: { stepId: 's0', partId: null } })));
  assert.ok(html.includes('The 3-D model has no geometry for these parts.') && !html.includes('data-viewer'), html);
});

test('a part selected: only its families, a chip with a delete icon leads back to All', async () => {
  const i18n = m.makeI18n('en'); await ready(i18n);
  const html = m.view(i18n, base({ selection: { stepId: 's9', partId: 'p_coil' } }));
  assert.equal(attr(html, 'only'), 'Coils');
  assert.equal(attr(html, 'highlight'), '');
  assert.ok(html.includes('model3d-part-chip') && html.includes('MuiChip-deleteIcon'), html);
});

test('electronics: the full model, no part filter, even with a part or step selected', async () => {
  const i18n = m.makeI18n('en'); await ready(i18n);
  const e = { ...ctx, productType: 'electronics' };
  assert.equal(attr(m.view(i18n, base({ ctx: e, selection: { stepId: null, partId: 'p_coil' } })), 'only'), 'ALL');
  assert.equal(attr(m.view(i18n, base({ ctx: e, selection: { stepId: 's9', partId: null } })), 'only'), 'ALL');
});

test('zh-CN renders its own strings', async () => {
  const i18n = m.makeI18n('zh-CN'); await ready(i18n);
  const html = decode(m.view(i18n, base()));
  assert.ok(html.includes('全部') && html.includes('模型 v3'), html);
});

test('no caption / helperText in the new tab source', () => {
  const s = readFileSync(join(src, 'modules/production/components/Model3DTab.tsx'), 'utf8');
  assert.ok(!/variant\s*=\s*["']caption["']/.test(s) && !/helperText\s*=/.test(s) && !/FormHelperText/.test(s));
});
