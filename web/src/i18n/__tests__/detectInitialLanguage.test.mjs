// node --test — first-visit language (detectInitialLanguage.ts + locale.ts +
// persist.ts carry-over), on the REAL TypeScript modules via esbuild.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { join, dirname, resolve } from 'node:path';
import { tmpdir } from 'node:os';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';

const here = dirname(fileURLToPath(import.meta.url));
const src = resolve(here, '..', '..');
const tmp = mkdtempSync(join(tmpdir(), 'i18n-detect-'));
const out = join(tmp, 'bundle.mjs');
const rel = (p) => resolve(src, p).split('\\').join('/');

writeFileSync(join(tmp, 'entry.jsx'), `
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { I18nextProvider } from 'react-i18next';
export { detectInitialLanguage, isChineseTag, isChinaTimeZone, CHINA_TIME_ZONES } from '${rel('i18n/detectInitialLanguage.ts')}';
export { detectLocale, DOCUMENT_TITLES } from '${rel('i18n/locale.ts')}';
export { setLocale, applyServerLocale, pendingLocaleChoice } from '${rel('i18n/persist.ts')}';
import { createI18n } from '${rel('i18n/core.ts')}';
import LanguageSwitcher from '${rel('components/common/LanguageSwitcher.tsx')}';
import enCommon from '${rel('locales/en/common.json')}';
import zhCommon from '${rel('locales/zh-CN/common.json')}';
export function makeI18n(lng) {
  return createI18n({ lng, resources: { en: { common: enCommon }, 'zh-CN': { common: zhCommon } } });
}
export function renderInline(i18n) {
  return renderToStaticMarkup(React.createElement(I18nextProvider, { i18n },
    React.createElement(LanguageSwitcher, { variant: 'inline' })));
}
`);
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
const det = (languages, timeZone) => m.detectInitialLanguage({ languages, timeZone });

test('Chinese language tags give zh-CN, in any position and any case', () => {
  for (const tag of ['zh', 'zh-CN', 'zh-Hans', 'zh-SG', 'zh-TW', 'zh-HK', 'ZH-cn', 'zh_CN', 'zh-Hant-TW'])
    assert.equal(det([tag], 'Europe/Paris'), 'zh-CN', tag);
  assert.equal(det(['en-US', 'de', 'zh-CN'], 'America/New_York'), 'zh-CN');
});

test('China time zones give zh-CN even with English browser languages', () => {
  for (const tz of ['Asia/Shanghai', 'Asia/Chongqing', 'Asia/Harbin', 'Asia/Urumqi',
    'Asia/Kashgar', 'Asia/Hong_Kong', 'Asia/Macau'])
    assert.equal(det(['en-US'], tz), 'zh-CN', tz);
});

test('every other tag and zone falls back to English', () => {
  for (const tag of ['en', 'en-US', 'de-DE', 'fr', 'ja-JP', 'ko', 'zhx', 'azh-CN', ''])
    assert.equal(det([tag], 'Europe/Ljubljana'), 'en', tag);
  for (const tz of ['Europe/Ljubljana', 'America/New_York', 'Asia/Tokyo', 'Asia/Singapore', 'UTC', '', null])
    assert.equal(det(['en-US'], tz), 'en', String(tz));
  assert.equal(det([], null), 'en');
  assert.equal(det(null, null), 'en');
  assert.equal(m.isChineseTag(undefined), false);
  assert.equal(m.isChinaTimeZone(undefined), false);
});

test('a saved choice always wins over detection', () => {
  const zh = memStore(); zh.setItem('ui.locale', 'zh-CN');
  assert.equal(m.detectLocale({ storage: zh, languages: ['en-US'], timeZone: 'Europe/Paris' }), 'zh-CN');
  const en = memStore(); en.setItem('ui.locale', 'en');
  assert.equal(m.detectLocale({ storage: en, languages: ['zh-CN'], timeZone: 'Asia/Shanghai' }), 'en');
  assert.equal(m.detectLocale({ storage: memStore(), languages: ['zh-CN'], timeZone: null }), 'zh-CN');
  assert.equal(m.detectLocale({ storage: memStore(), languages: ['fr'], timeZone: 'Asia/Shanghai' }), 'zh-CN');
  assert.equal(m.detectLocale({ storage: memStore(), languages: ['fr'], timeZone: 'Europe/Paris' }), 'en');
});

test('switching sets <html lang> and the document title in each language', async () => {
  const doc = { documentElement: { lang: '' }, title: '' };
  globalThis.document = doc;
  try {
    const i = m.makeI18n('zh-CN'); await ready(i);
    assert.equal(doc.documentElement.lang, 'zh-CN');
    assert.equal(doc.title, m.DOCUMENT_TITLES['zh-CN']);
    await m.setLocale(i, 'en', { storage: memStore(), remote: false });
    assert.equal(doc.documentElement.lang, 'en');
    assert.equal(doc.title, m.DOCUMENT_TITLES.en);
    assert.notEqual(m.DOCUMENT_TITLES.en, m.DOCUMENT_TITLES['zh-CN']);
  } finally { delete globalThis.document; }
});

test('the landing switch renders EN | 中文 with the active language pressed', async () => {
  const zh = m.makeI18n('zh-CN'); await ready(zh);
  const html = m.renderInline(zh);
  assert.match(html, /data-testid="lang-inline-en"[^>]*>EN</);
  assert.match(html, /data-testid="lang-inline-zh-CN"[^>]*>中文</);
  const pressed = (loc) => new RegExp(`<button[^>]*(?:aria-pressed="true"[^>]*data-testid="lang-inline-${loc}"|data-testid="lang-inline-${loc}"[^>]*aria-pressed="true")`);
  assert.match(html, pressed('zh-CN'));
  assert.doesNotMatch(html, pressed('en'));
});

test('a landing choice made signed out carries over to the account on sign-in', async () => {
  const s = memStore();
  const i = m.makeI18n('en'); await ready(i);
  // Signed out: the PUT is refused (401); the choice is kept locally and marked unsynced.
  await m.setLocale(i, 'zh-CN', { storage: s, fetchImpl: async () => new Response('{}', { status: 401 }) });
  assert.equal(m.pendingLocaleChoice(), 'zh-CN');
  // Sign-in: the account has NO preference -> the choice is sent up, language stays.
  const puts = [];
  const f = async (u, init) => {
    if (init?.method === 'PUT') { puts.push(JSON.parse(init.body)); return new Response('{}'); }
    return new Response(JSON.stringify({ locale: null }));
  };
  assert.equal(await m.applyServerLocale(i, f), 'zh-CN');
  assert.equal(i.language, 'zh-CN');
  assert.deepEqual(puts, [{ locale: 'zh-CN' }]);
  assert.equal(m.pendingLocaleChoice(), null);
});

test('an unsynced landing choice beats a stale account preference; a synced one does not', async () => {
  const i = m.makeI18n('zh-CN'); await ready(i);
  await m.setLocale(i, 'en', { storage: memStore(), fetchImpl: async () => new Response('{}', { status: 401 }) });
  const puts = [];
  const f = (serverLocale) => async (u, init) => {
    if (init?.method === 'PUT') { puts.push(JSON.parse(init.body)); return new Response('{}'); }
    return new Response(JSON.stringify({ locale: serverLocale }));
  };
  assert.equal(await m.applyServerLocale(i, f('zh-CN')), 'en');
  assert.equal(i.language, 'en');
  assert.deepEqual(puts, [{ locale: 'en' }]);
  // Nothing pending now (the PUT succeeded): the account preference wins as it always did.
  const j = m.makeI18n('en'); await ready(j);
  assert.equal(await m.applyServerLocale(j, f('zh-CN')), 'zh-CN');
  assert.equal(j.language, 'zh-CN');
});
