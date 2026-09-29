// node --test — the i18n runtime (docs/I18N.md), on the REAL TypeScript
// modules: esbuild bundles a small entry (the node tests elsewhere re-state
// pure functions; the i18n layer is thin glue over i18next, so it is tested
// as shipped).  Covers: locale detection, the switcher persisting (storage +
// server PUT, both failure-tolerant), Intl formatting with untouched unit
// symbols, ICU plurals, EN fallback, API-error translation, and real
// components rendered in zh-CN.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { join, dirname, resolve } from 'node:path';
import { tmpdir } from 'node:os';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';

const here = dirname(fileURLToPath(import.meta.url));
const src = resolve(here, '..', '..');
const tmp = mkdtempSync(join(tmpdir(), 'i18n-test-'));
const out = join(tmp, 'bundle.mjs');
const rel = (p) => resolve(src, p).split('\\').join('/');

const entry = `
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { I18nextProvider } from 'react-i18next';
export { normalizeLocale, detectLocale } from '${rel('i18n/locale.ts')}';
export { formatNumber, formatQuantity, formatPercent, formatDateTime } from '${rel('i18n/format.ts')}';
export { setLocale } from '${rel('i18n/persist.ts')}';
export { translateApiError } from '${rel('i18n/errors.ts')}';
import { createI18n } from '${rel('i18n/core.ts')}';
import LanguageSwitcher from '${rel('components/common/LanguageSwitcher.tsx')}';
import HelpTip from '${rel('components/common/HelpTip.tsx')}';
import enCommon from '${rel('locales/en/common.json')}';
import zhCommon from '${rel('locales/zh-CN/common.json')}';
import enErrors from '${rel('locales/en/errors.json')}';
import zhErrors from '${rel('locales/zh-CN/errors.json')}';
import enHelp from '${rel('locales/en/help.json')}';
import zhHelp from '${rel('locales/zh-CN/help.json')}';
export function makeI18n(lng, dropZhAccount) {
  const zhC = { ...zhCommon };
  if (dropZhAccount) delete zhC.account;   // simulate a block not translated yet
  return createI18n({ lng, resources: {
    en: { common: enCommon, errors: enErrors, help: enHelp },
    'zh-CN': { common: zhC, errors: zhErrors, help: zhHelp } } });
}
export function render(i18n) {
  return renderToStaticMarkup(React.createElement(I18nextProvider, { i18n },
    React.createElement('div', null,
      React.createElement(LanguageSwitcher, { variant: 'button' }),
      React.createElement(HelpTip, { i18nKey: 'controller.noSuchKey', title: 'fallback text' }),
      React.createElement(HelpTip, { i18nKey: 'controller.drawnFromTheChosenTopology' }))));
}
`;
writeFileSync(join(tmp, 'entry.jsx'), entry);
await build({
  entryPoints: [join(tmp, 'entry.jsx')], bundle: true, platform: 'node', format: 'esm',
  outfile: out, jsx: 'automatic', logLevel: 'error', nodePaths: [resolve(src, '..', 'node_modules')],
  define: { 'import.meta.env': '{"DEV":false,"VITE_API_URL":"http://api.test"}' },
  banner: { js: "import { createRequire } from 'module'; const require = createRequire(import.meta.url);" },
});
const m = await import(pathToFileURL(out).href);
process.on('exit', () => { try { rmSync(tmp, { recursive: true, force: true }); } catch { /* */ } });

const memStore = () => { const d = new Map(); return { getItem: (k) => d.get(k) ?? null, setItem: (k, v) => d.set(k, String(v)) }; };
const ready = (i) => new Promise((r) => (i.isInitialized ? r() : i.on('initialized', () => r())));

test('locale detection: storage, then browser languages, then EN', () => {
  assert.equal(m.normalizeLocale('zh-TW'), 'zh-CN');
  assert.equal(m.normalizeLocale('en_GB'), 'en');
  assert.equal(m.normalizeLocale('de'), null);
  assert.equal(m.detectLocale({ storage: null, languages: ['de-DE', 'zh-Hans-CN'] }), 'zh-CN');
  assert.equal(m.detectLocale({ storage: null, languages: ['fr'] }), 'en');
  const s = memStore(); s.setItem('ui.locale', 'zh-CN');
  assert.equal(m.detectLocale({ storage: s, languages: ['en-US'] }), 'zh-CN');
  const broken = { getItem() { throw new Error('SecurityError'); }, setItem() { throw new Error('x'); } };
  assert.equal(m.detectLocale({ storage: broken, languages: [] }), 'en');
});

test('the switcher persists: storage + server PUT; a failing PUT still switches', async () => {
  const i = m.makeI18n('en'); await ready(i);
  const s = memStore(); const calls = [];
  await m.setLocale(i, 'zh-CN', { storage: s,
    fetchImpl: async (u, init) => { calls.push([u, init]); return new Response('{}'); } });
  assert.equal(i.language, 'zh-CN');
  assert.equal(s.getItem('ui.locale'), 'zh-CN');
  assert.equal(calls[0][0], 'http://api.test/api/me/preferences');
  assert.equal(calls[0][1].method, 'PUT');
  assert.deepEqual(JSON.parse(calls[0][1].body), { locale: 'zh-CN' });
  await m.setLocale(i, 'en', { storage: s, fetchImpl: async () => { throw new TypeError('offline'); } });
  assert.equal(i.language, 'en');
  assert.equal(s.getItem('ui.locale'), 'en');
});

test('Intl formatting follows the locale; unit symbols are never translated', () => {
  assert.equal(m.formatNumber(12345.678, 'en', { digits: 1 }), '12,345.7');
  assert.equal(m.formatNumber(12345.678, 'zh-CN', { digits: 1 }), '12,345.7');
  assert.equal(m.formatNumber(12345.678, 'de', { digits: 1 }), '12.345,7');
  assert.equal(m.formatQuantity(850, 'N·m', 'zh-CN'), '850\u202FN·m');
  assert.equal(m.formatQuantity(96.4, '%', 'zh-CN'), '96.4%');
  assert.equal(m.formatQuantity(NaN, 'kW', 'en'), '—');
  assert.equal(m.formatPercent(0.964, 'zh-CN'), '96.4%');
  assert.match(m.formatDateTime('2026-09-29T08:00:00Z', 'zh-CN',
    { year: 'numeric', month: 'long', day: 'numeric', timeZone: 'UTC' }), /2026年9月29日/);
});

test('ICU plurals and interpolation in both languages', async () => {
  const en = m.makeI18n('en'); await ready(en);
  assert.equal(en.t('units.motorCount', { count: 1 }), '1 motor');
  assert.equal(en.t('units.motorCount', { count: 3 }), '3 motors');
  const zh = m.makeI18n('zh-CN'); await ready(zh);
  assert.equal(zh.t('units.motorCount', { count: 3 }), '3 台电机');
  assert.equal(zh.t('auth.validation.passwordMin', { min: 12 }), '至少 12 个字符');
});

test('a key missing in zh-CN falls back to English', async () => {
  const zh = m.makeI18n('zh-CN', true); await ready(zh);
  assert.equal(zh.t('account.signIn'), 'Sign in');
  assert.equal(zh.t('language.label'), '语言');
});

test('API errors: known code translated with params, unknown code shows the English detail', async () => {
  const zh = m.makeI18n('zh-CN'); await ready(zh);
  assert.equal(m.translateApiError(zh.t,
    { detail: 'unknown material: M19', code: 'material.unknown', params: { name: 'M19' } }), '未知材料：M19');
  assert.equal(m.translateApiError(zh.t, { detail: 'something new', code: 'http.500', params: {} }), 'something new');
  assert.equal(m.translateApiError(zh.t, { detail: { error: 'bad', invalid_parameters: [] } }), 'bad');
});

test('real components render in zh-CN (switcher + HelpTip)', async () => {
  const zh = m.makeI18n('zh-CN'); await ready(zh);
  const html = m.render(zh);
  assert.match(html, /aria-label="语言"/);
  assert.match(html, /aria-label="fallback text"/);   // missing key -> the English title
  assert.match(html, /根据所选拓扑/);                   // translated HelpTip
  const en = m.makeI18n('en'); await ready(en);
  assert.match(m.render(en), /aria-label="Language"/);
});
