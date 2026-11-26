// node --test: the way back from an open project (owner 2026-11-18: "the return arrow is quite unnoticeable, better make it a
// button so that it is seen").  esbuild bundles the real button and the real Projects page module; the button is rendered
// server-side with the APP's i18n and the real EN / zh-CN locale files.  The open-project header row itself is a portal
// (PanelHeader / HeaderTabs) that only a browser draws, and no DOM library is in the repository, so its place is read from the
// page source and its width at 1280 / 1920 px is checked as a budget from the real tab labels.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { join, dirname, resolve } from 'node:path';
import { tmpdir } from 'node:os';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';

const here = dirname(fileURLToPath(import.meta.url));
const src = resolve(here, '..', '..', '..');
const tmp = mkdtempSync(join(tmpdir(), 'projectback-test-'));
const out = join(tmp, 'bundle.mjs');
const rel = (p) => resolve(src, p).split('\\').join('/');

const store = new Map();
globalThis.localStorage = { getItem: (k) => (store.has(k) ? store.get(k) : null), setItem: (k, v) => store.set(k, String(v)),
  removeItem: (k) => store.delete(k) };
globalThis.window = { location: { href: 'http://app.test/development', origin: 'http://app.test', pathname: '/development', search: '', hash: '' },
  addEventListener() {}, removeEventListener() {}, dispatchEvent() { return true; }, setTimeout, clearTimeout, setInterval, clearInterval,
  matchMedia: () => ({ matches: false, addEventListener() {}, removeEventListener() {} }), localStorage: globalThis.localStorage,
  __fetchAuthInstalled: true };
globalThis.fetch = async () => ({ ok: true, status: 200, json: async () => ({}) });

writeFileSync(join(tmp, 'entry.jsx'), `
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { I18nextProvider } from 'react-i18next';
import appI18n from '${rel('i18n/index.ts')}';
import ProjectBackButton, { BACK_BUTTON_HEIGHT } from '${rel('modules/development/components/ProjectBackButton.tsx')}';
export { SUB_TAB_HEIGHT } from '${rel('components/common/SubTabs.tsx')}';
// the Projects tab's own page module: bundled here so the test fails if the page no longer builds with the button
export { default as DevelopmentPage } from '${rel('modules/development/components/DevelopmentPage.tsx')}';
export { BACK_BUTTON_HEIGHT };
export const i18n = appI18n;
export const renderBack = (lang) => {
  const t = appI18n.getFixedT(lang, 'development');
  return renderToStaticMarkup(React.createElement(I18nextProvider, { i18n: appI18n },
    React.createElement(ProjectBackButton, { label: t('back'), tip: t('backTip'), onClick: () => undefined })));
};
`);
await build({
  entryPoints: [join(tmp, 'entry.jsx')], bundle: true, platform: 'node', format: 'esm',
  outfile: out, jsx: 'automatic', logLevel: 'error', nodePaths: [resolve(src, '..', 'node_modules')],
  define: { 'import.meta.env': '{"DEV":false,"VITE_API_URL":"http://api.test"}' },
  loader: { '.png': 'dataurl', '.svg': 'dataurl', '.css': 'empty' },
  banner: { js: "import { createRequire } from 'module'; const require = createRequire(import.meta.url);" },
});
const m = await import(pathToFileURL(out).href);
process.on('exit', () => { try { rmSync(tmp, { recursive: true, force: true }); } catch { /* */ } });
await new Promise((r) => (m.i18n.isInitialized ? r() : m.i18n.on('initialized', () => r())));
await m.i18n.loadLanguages(['en', 'zh-CN']);
await m.i18n.loadNamespaces(['common', 'development']);

const locale = (lang) => JSON.parse(readFileSync(resolve(src, 'locales', lang, 'development.json'), 'utf8'));
const commonLocale = (lang) => JSON.parse(readFileSync(resolve(src, 'locales', lang, 'common.json'), 'utf8'));

for (const [lang, label, tip] of [['en', 'All projects', 'Back to the project list'], ['zh-CN', '全部项目', '返回项目列表']]) {
  test(`${lang}: the way back is an outlined button with the arrow AND its label, tooltip kept`, () => {
    assert.equal(locale(lang).back, label);
    assert.equal(locale(lang).backTip, tip);
    const html = m.renderBack(lang);
    assert.ok(html.includes('data-testid="project-back"'), html);
    assert.ok(html.includes('MuiButton-outlined'), 'an outlined button, not a bare icon');
    assert.ok(!html.includes('MuiIconButton'), 'no icon-only button any more');
    assert.ok(html.includes('data-testid="ArrowBackIcon"'), 'the arrow stays');
    assert.ok(html.includes(`>${label}</button>`), `the label "${label}" is on the button: ${html}`);
    assert.ok(html.includes(`aria-label="${tip}"`), `the tooltip "${tip}" is the button's accessible name: ${html}`);
    assert.ok(!html.includes('tabindex="-1"'), 'reachable by keyboard');
  });
}

test('the button is as tall as a sub-tab and has a visible keyboard focus ring', () => {
  assert.equal(m.BACK_BUTTON_HEIGHT, m.SUB_TAB_HEIGHT);
  const btn = readFileSync(resolve(src, 'modules/development/components/ProjectBackButton.tsx'), 'utf8');
  assert.match(btn, /'&\.Mui-focusVisible': \{ outline: '2px solid'/);
  assert.match(btn, /flexShrink: 0, whiteSpace: 'nowrap', mr: 1\.5/, 'never shrinks or wraps, a clear gap before the tabs');
});

test('Projects tab: in the open-project header the button stands before the project tabs; the bare arrow is gone', () => {
  const page = readFileSync(resolve(src, 'modules/development/components/DevelopmentPage.tsx'), 'utf8');
  const block = page.slice(page.indexOf('<HeaderTabs tab="development">'), page.indexOf('</HeaderTabs>'));
  assert.ok(block.length > 0);
  const back = block.indexOf("<ProjectBackButton label={tr('back')} tip={tr('backTip')} onClick={() => void backToList()} />");
  assert.ok(back > 0, 'the button is in the header row');
  assert.ok(back < block.indexOf('<SubTabs'), 'before the tabs');
  assert.ok(!/data-testid="project-back"/.test(page), 'no second, icon-only project-back left in the page');
});

// owner 2026-11-18, after #755: "the back arrow is duplicated, leave one" - the old "All projects" text button left of the project
// title (the body row every project tab shares) is gone; the header button is the one return for every role and every tab
test('Projects tab: exactly ONE return to the project list, for every role and every project tab', () => {
  const page = readFileSync(resolve(src, 'modules/development/components/DevelopmentPage.tsx'), 'utf8');
  assert.equal((page.match(/onClick=\{\(\) => void backToList\(\)\}/g) || []).length, 1, 'one control calls backToList');
  assert.equal((page.match(/<ProjectBackButton\b/g) || []).length, 1);
  assert.ok(!page.includes('ArrowBackIcon'), 'no other arrow in the page');
  assert.ok(!page.includes("{tr('back')}</Button>"), 'the body text button is gone');
  // the title row now starts with the title itself: no empty slot left before it
  assert.ok(page.includes("<Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1.5, flexWrap: 'wrap' }}><Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, flex: 1"));
  // the header button is not behind a role or a tab: it sits in <HeaderTabs> outside every view / role condition
  const block = page.slice(page.indexOf('<HeaderTabs tab="development">'), page.indexOf('<SubTabs'));
  assert.ok(!/&&\s*<ProjectBackButton|\?\s*<ProjectBackButton/.test(block), 'not conditional');
});

// Width budget of the open-project header row (PanelHeader: px 3 each side, gap 8, the panel label, then the tabs slot with the
// button).  The row wraps (flexWrap), so if label + button + all tabs are wider than the row, the tabs drop to a second line.
// Worst case: an admin with every tab (production, notes, quote, cost) and an unread chip on Messages.  Text widths are
// a deliberate OVER-estimate (Latin 0.62 em per character, CJK 1 em) so a pass here leaves room in the browser.
const EM_TAB = 12.8;                                     // SUB_TAB_SX fontSize 0.8rem
const EM_LABEL = 14;                                     // the panel label, subtitle2 0.875rem
const textW = (s, em) => [...s].reduce((w, ch) => w + (/[\u3000-\u9fff\uff00-\uffef]/.test(ch) ? 1 : 0.62) * em, 0);
const rowNeeds = (lang) => {
  const tabs = locale(lang).tabs;
  const order = ['specification', 'motors', 'messages', 'documents', 'production', 'quote', 'cost'];   // no project Timeline (owner 2026-11-25)
  const tabsW = order.reduce((w, id) => w + textW(tabs[id], EM_TAB) + 20, 0) + 36;    // px 1.25 each side; unread chip + margin
  const backW = textW(locale(lang).back, EM_TAB) + 18 + 8 - 4 + 20 + 2 + 12;           // icon, its margins, padding, border, mr 1.5
  return textW(commonLocale(lang).tabs.development, EM_LABEL) + 8 + backW + tabsW;
};
for (const width of [1280, 1920]) {
  for (const lang of ['en', 'zh-CN']) {
    test(`${lang} at ${width} px: label + button + every project tab fit one line (tabs not pushed to a second line)`, () => {
      const room = width - 48 - 17;                      // header padding, a vertical scrollbar
      const needs = rowNeeds(lang);
      assert.ok(needs < room, `needs ${needs.toFixed(0)} px, the row has ${room} px`);
    });
  }
}
