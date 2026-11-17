// node --test: the production state chip (owner 2026-11-17), RENDERED with the real EN and zh-CN locale files: the factory
// sees prepared / in production / finished; only an admin gets the menu that sets it.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { join, dirname, resolve } from 'node:path';
import { tmpdir } from 'node:os';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';

const here = dirname(fileURLToPath(import.meta.url));
const src = resolve(here, '..', '..', '..');
const tmp = mkdtempSync(join(tmpdir(), 'lifecycle-test-'));
const out = join(tmp, 'bundle.mjs');
const rel = (p) => resolve(src, p).split('\\').join('/');
writeFileSync(join(tmp, 'entry.jsx'), `
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { I18nextProvider } from 'react-i18next';
import { createI18n } from '${rel('i18n/core.ts')}';
import { ProductionLifecycleChip } from '${rel('components/common/ProductionLifecycle.tsx')}';
import enP from '${rel('locales/en/production.json')}';
import zhP from '${rel('locales/zh-CN/production.json')}';
export const makeI18n = (lng) => createI18n({ lng, resources: { en: { production: enP }, 'zh-CN': { production: zhP } } });
export const chip = (i18n, props) => renderToStaticMarkup(React.createElement(I18nextProvider, { i18n }, React.createElement(ProductionLifecycleChip, props)));
`);
await build({ entryPoints: [join(tmp, 'entry.jsx')], bundle: true, platform: 'node', format: 'esm', outfile: out, jsx: 'automatic', logLevel: 'error',
  nodePaths: [resolve(src, '..', 'node_modules')], define: { 'import.meta.env': '{"DEV":false,"VITE_API_URL":"http://api.test"}' },
  banner: { js: "import { createRequire } from 'module'; const require = createRequire(import.meta.url);" } });
const m = await import(pathToFileURL(out).href);
process.on('exit', () => { try { rmSync(tmp, { recursive: true, force: true }); } catch { /* */ } });
const ready = (i) => new Promise((r) => (i.isInitialized ? r() : i.on('initialized', () => r())));
const decode = (s) => s.replace(/&#x27;|&#39;/g, "'").replace(/&quot;/g, '"').replace(/&amp;/g, '&');

for (const [lng, prepared, inProd, finished] of [['en', 'Prepared', 'In production', 'Finished'], ['zh-CN', '已准备', '生产中', '已完成']]) {
  test(`${lng}: each state reads as its word; the payment line and who set it stay in the tooltip`, async () => {
    const i18n = m.makeI18n(lng); await ready(i18n);
    const html = (state, extra = {}) => decode(m.chip(i18n, { die: 'D', config: 'C', isAdmin: false, answer: { state, explicit: true, ...extra } }));
    assert.ok(html('prepared').includes(`>${prepared}<`));
    const p = html('in_production', { payment: '2026-11-10 advance' });
    assert.ok(p.includes(`>${inProd}<`) && p.includes('2026-11-10 advance'), p);           // in the tooltip (aria-label)
    assert.ok(html('finished').includes(`>${finished}<`));
    assert.ok(!/lifecycle\./.test(html('prepared')), 'no raw key');
  });
}
test('only an admin can open the menu; the factory has a plain chip', async () => {
  const i18n = m.makeI18n('en'); await ready(i18n);
  const factory = m.chip(i18n, { die: 'D', config: 'C', isAdmin: false, answer: { state: 'prepared', explicit: false } });
  const admin = m.chip(i18n, { die: 'D', config: 'C', isAdmin: true, answer: { state: 'prepared', explicit: false } });
  assert.ok(!factory.includes('role="button"') && admin.includes('role="button"'), admin.slice(-600));
});
