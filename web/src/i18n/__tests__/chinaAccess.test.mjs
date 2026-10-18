// node --test: "the site must open for a visitor in mainland China" (2026-10-14).
//
// A WeChat visitor saw only the pre-rendered block: index.html pulled Google
// Fonts (blocked there), the stylesheet never finished, and a module script
// waits for the stylesheets before it runs, so the bundle never mounted.
// Guards here:
//   1. the page, its CSS and the font setup reference no blocked / third-party
//      host at load (allowlist: none);
//   2. Google's sign-in script is fetched only by a click, with a timeout;
//   3. the first-visit redirect script (shipped text, run in a VM): zh UA /
//      zh languages / China time zone -> /zh-cn/ once, a saved choice wins,
//      it can never loop;
//   4. the static block is readable on its own and carries Sign in / Request access.
// Pages come from web/index.html through the real prerender (no `vite build`
// needed); when dist/ exists, the BUILT pages and every built CSS are checked too.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';
import { renderPage, loadLocale, LOCALES } from '../../../scripts/prerender-landing.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const WEB = path.resolve(here, '..', '..', '..');
const SRC = path.join(WEB, 'src');
const read = (p) => fs.readFileSync(p, 'utf8');
const rel = (p) => path.resolve(SRC, p).split('\\').join('/');

/** [label, html] for the generated pages, plus the built ones when dist/ exists. */
function pages() {
  const tpl = read(path.join(WEB, 'index.html'));
  const out = LOCALES.map((loc) => [`generated ${loc.file}`, renderPage(tpl, loadLocale(loc.id, WEB), loc)]);
  const dist = path.join(WEB, 'dist');
  for (const loc of LOCALES) {
    const f = path.join(dist, loc.file);
    if (fs.existsSync(f)) out.push([`dist/${loc.file}`, read(f)]);
  }
  return out;
}

function cssFiles() {
  const files = [path.join(SRC, 'index.css')];
  const dir = path.join(WEB, 'dist', 'assets');
  if (fs.existsSync(dir)) for (const f of fs.readdirSync(dir)) if (f.endsWith('.css')) files.push(path.join(dir, f));
  return files;
}

const BLOCKED = /fonts\.googleapis\.com|fonts\.gstatic\.com|gstatic\.com|accounts\.google\.com|apis\.google\.com|googletagmanager|google-analytics|googleusercontent|jsdelivr|unpkg\.com|cdnjs|plausible|hotjar|facebook\.net/i;

// 1. no external host at load -------------------------------------------------
test('built pages name no blocked host anywhere and load nothing from another origin', () => {
  for (const [label, html] of pages()) {
    assert.doesNotMatch(html, BLOCKED, `${label} mentions a blocked host`);
    // Tags the browser FETCHES while loading (not <a>, canonical, alternate or meta).
    const tags = html.match(/<(link|script|img|source|iframe|video|audio)\b[^>]*>/gi) || [];
    for (const tag of tags) {
      if (/rel="(canonical|alternate)"/i.test(tag)) continue;
      const m = tag.match(/\b(?:href|src)="([^"]+)"/i);
      if (!m) continue;
      assert.doesNotMatch(m[1], /^(https?:)?\/\//i, `${label}: ${tag} loads from another origin`);
    }
  }
});

test('CSS (source and built) imports nothing and fetches no remote font or image', () => {
  for (const f of cssFiles()) {
    const css = read(f);
    assert.doesNotMatch(css, BLOCKED, `${f} mentions a blocked host`);
    assert.doesNotMatch(css, /@import\s/i, `${f} has an @import`);
    assert.doesNotMatch(css, /url\(\s*['"]?(https?:)?\/\//i, `${f} fetches a remote url()`);
  }
});

test('Inter is self-hosted: @font-face swap, woff2 files and the OFL licence ship', () => {
  const css = read(path.join(SRC, 'index.css'));
  assert.match(css, /@font-face\s*{[^}]*font-family:\s*'Inter'/);
  assert.match(css, /font-display:\s*swap/);
  const urls = [...css.matchAll(/url\('(\/fonts\/[^']+\.woff2)'\)/g)].map((m) => m[1]);
  assert.ok(urls.length >= 2, 'latin + latin-ext');
  for (const u of urls) assert.ok(fs.statSync(path.join(WEB, 'public', u)).size > 10000, u);
  const lic = read(path.join(WEB, 'public', 'fonts', 'Inter-OFL.txt'));
  assert.match(lic, /SIL OPEN FONT LICENSE/i);
  // A system fallback stack follows 'Inter' (and covers Chinese text).
  assert.match(css, /font-family:\s*'Inter',\s*-apple-system[^;]*'PingFang SC'/);
});

// 2. the GIS loader runs on a click only --------------------------------------
test('loadGis() is called only from click handlers (never an effect or module init)', () => {
  const files = [];
  const walk = (d) => {
    for (const e of fs.readdirSync(d, { withFileTypes: true })) {
      const p = path.join(d, e.name);
      if (e.isDirectory()) { if (e.name !== '__tests__' && e.name !== 'locales') walk(p); }
      else if (/\.(tsx?|jsx?)$/.test(e.name)) files.push(p);
    }
  };
  walk(SRC);
  const allowed = { 'LoginDialog.tsx': 'startGoogle', 'AccountDataDialog.tsx': 'confirmWithGoogle' };
  const seen = {};
  for (const f of files) {
    const base = path.basename(f);
    const text = read(f)
      .replace(/\/\*[\s\S]*?\*\//g, (c) => ' '.repeat(c.length))
      .replace(/\/\/.*$/gm, (c) => ' '.repeat(c.length));
    for (const m of text.matchAll(/\bloadGis\(/g)) {
      if (base === 'localAuth.ts' && text.slice(m.index - 16, m.index) === 'export function ') continue;
      if (base === 'AuthContext.tsx') {
        // logout only, and only when the script is already on the page
        assert.match(text.slice(Math.max(0, m.index - 260), m.index), /isGisLoaded\(\)/,
          'AuthContext may call loadGis only behind isGisLoaded()');
        continue;
      }
      const want = allowed[base];
      assert.ok(want, `${base} calls loadGis()`);
      const before = text.slice(0, m.index);
      const fn = [...before.matchAll(/const (\w+) = \(/g)].pop()[1];
      assert.equal(fn, want, `${base}: loadGis() must sit inside ${want}`);
      seen[base] = (seen[base] || 0) + 1;
    }
  }
  assert.deepEqual(Object.keys(seen).sort(), ['AccountDataDialog.tsx', 'LoginDialog.tsx']);
  assert.match(read(path.join(SRC, 'components/auth/LoginDialog.tsx')), /onClick=\{startGoogle\}/);
  assert.match(read(path.join(SRC, 'components/auth/AccountDataDialog.tsx')), /onClick=\{confirmWithGoogle\}/);
});

test('loadGis: nothing on import, script appended by the call, unreachable -> GisUnreachableError', async () => {
  const tmp = mkdtempSync(path.join(tmpdir(), 'china-gis-'));
  const outFile = path.join(tmp, 'gis.mjs');
  await build({
    stdin: { contents: `export * from '${rel('lib/localAuth.ts')}';`, resolveDir: SRC, loader: 'ts' },
    bundle: true, format: 'esm', platform: 'node', outfile: outFile, logLevel: 'silent',
    define: { 'import.meta.env': JSON.stringify({ VITE_GOOGLE_CLIENT_ID: 'test-client.apps.googleusercontent.com', VITE_API_URL: 'http://x' }) },
  });
  const appended = [];
  const mkScript = () => ({ remove() { this.removed = true; } });
  const fake = (after) => {
    globalThis.window = globalThis;
    globalThis.document = {
      createElement: () => mkScript(),
      head: { appendChild: (s) => { appended.push(s); setTimeout(() => after(s), 0); } },
    };
  };
  const bust = () => pathToFileURL(outFile).href + '?' + Math.random();

  // Case A: blocked host (onerror).
  fake((s) => s.onerror && s.onerror(new Error('blocked')));
  let mod = await import(bust());
  assert.equal(appended.length, 0, 'importing the module must not touch the DOM');
  await assert.rejects(mod.loadGis(1000), (e) => e.name === 'GisUnreachableError');
  assert.equal(appended.length, 1);
  assert.equal(appended[0].src, 'https://accounts.google.com/gsi/client');
  assert.ok(appended[0].async);

  // Case B: the host never answers -> timeout, and a later click may retry.
  fake(() => {});
  mod = await import(bust());
  const t0 = Date.now();
  await assert.rejects(mod.loadGis(30), (e) => e.name === 'GisUnreachableError');
  assert.ok(Date.now() - t0 < 2000);
  assert.ok(appended.at(-1).removed, 'the dead script tag is removed');
  await assert.rejects(mod.loadGis(30), (e) => e.name === 'GisUnreachableError');
  assert.equal(appended.length, 3, 'a second click tries again');

  // Case C: loads fine.
  fake((s) => {
    globalThis.google = { accounts: { id: { initialize() {}, renderButton() {}, disableAutoSelect() {} } } };
    s.onload();
  });
  mod = await import(bust());
  assert.equal(mod.isGisLoaded(), false);
  const gis = await mod.loadGis(1000);
  assert.equal(typeof gis.renderButton, 'function');
  assert.equal(mod.isGisLoaded(), true);
  delete globalThis.google; delete globalThis.window; delete globalThis.document;
});

// 3. the first-visit redirect script ------------------------------------------
function redirectScript(html) {
  const m = html.match(/<script data-first-visit-redirect>([\s\S]*?)<\/script>/);
  assert.ok(m, 'index.html carries the first-visit redirect script');
  return m[1];
}

/** Run the shipped script once in a sandbox. Returns { target, store }. */
function visit(script, {
  path: pathname = '/', search = '', hash = '', ua = 'Mozilla/5.0', languages = ['en-US'],
  tz = 'Europe/Ljubljana', store = {}, brokenStorage = false,
} = {}) {
  let target = null;
  const storage = brokenStorage
    ? { getItem() { throw new Error('denied'); }, setItem() { throw new Error('denied'); } }
    : { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); } };
  const ctx = vm.createContext({
    window: { location: { pathname, search, hash, replace: (u) => { target = u; } } },
    navigator: { userAgent: ua, languages, language: languages[0] },
    localStorage: storage,
    Intl: { DateTimeFormat: () => ({ resolvedOptions: () => ({ timeZone: tz }) }) },
  });
  vm.runInContext(script, ctx);
  return { target, store };
}
const WECHAT = 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.79(0x18004f2c) NetType/WIFI Language/zh_CN';

test('redirect script: WeChat UA, zh language list or China time zone -> /zh-cn/', () => {
  for (const [label, html] of pages()) {
    const s = redirectScript(html);
    assert.equal(visit(s, { ua: WECHAT }).target, '/zh-cn/', `${label}: WeChat UA`);
    assert.equal(visit(s, { languages: ['zh-Hans-CN', 'en'] }).target, '/zh-cn/', `${label}: zh-Hans-CN`);
    assert.equal(visit(s, { languages: ['en-US', 'zh_TW'] }).target, '/zh-cn/', `${label}: zh later in the list`);
    assert.equal(visit(s, { tz: 'Asia/Shanghai' }).target, '/zh-cn/', `${label}: China time zone`);
    assert.equal(visit(s, { ua: WECHAT, brokenStorage: true }).target, '/zh-cn/', `${label}: blocked storage`);
    assert.equal(visit(s, { ua: WECHAT, hash: '#x', search: '?from=wechat' }).target, '/zh-cn/?from=wechat#x');
  }
});

test('redirect script: other visitors, saved choice, ?lang=en and special links stay put', () => {
  const s = redirectScript(pages()[0][1]);
  assert.equal(visit(s, {}).target, null, 'English browser');
  assert.equal(visit(s, { languages: ['en-US'], tz: 'Asia/Tokyo' }).target, null);
  assert.equal(visit(s, { ua: WECHAT, store: { 'ui.locale': 'en' } }).target, null, 'saved English wins');
  assert.equal(visit(s, { ua: WECHAT, store: { 'ui.locale': 'zh-CN' } }).target, null, 'saved Chinese: the app handles it');
  const en = visit(s, { ua: WECHAT, search: '?lang=en' });
  assert.equal(en.target, null, '?lang=en stays on /');
  assert.equal(en.store['ui.locale'], 'en', '?lang=en is remembered');
  assert.equal(visit(s, { ua: WECHAT, search: '?verify=abc' }).target, null, 'mailed link');
  assert.equal(visit(s, { ua: WECHAT, search: '?reset=abc' }).target, null);
  assert.equal(visit(s, { ua: WECHAT, search: '?signin=1' }).target, null);
  assert.equal(visit(s, { ua: WECHAT, path: '/agent-consent', search: '?request=1' }).target, null, 'only "/" is redirected');
});

test('redirect script never loops: the target is not redirected again', () => {
  const s = redirectScript(pages()[0][1]);
  assert.equal(visit(s, { ua: WECHAT }).target, '/zh-cn/');
  assert.equal(visit(s, { ua: WECHAT, path: '/zh-cn/' }).target, null);
  assert.equal(visit(s, { ua: WECHAT, path: '/zh-cn' }).target, null);
});

test('every page ships the same redirect script; the English link keeps the choice', () => {
  const all = pages();
  const first = redirectScript(all[0][1]);
  for (const [, html] of all) assert.equal(redirectScript(html), first);
  const zh = all.find(([l]) => l.includes('zh-cn'))[1];
  assert.match(zh, /<a href="\/\?lang=en" hreflang="en"/);
});

// 4. the static block works without the bundle --------------------------------
test('static block: readable colours, scrollable page, Sign in / Request access, no slow note', () => {
  for (const loc of LOCALES) {
    const html = renderPage(read(path.join(WEB, 'index.html')), loadLocale(loc.id, WEB), loc);
    assert.match(html, /#seo-landing\{[^}]*background:#f7f6f3;color:#0f172a/, 'explicit dark-on-light pair (the light default)');
    assert.match(html, /html,body,#root\{height:auto;overflow:auto\}/, 'scrolls without the app');
    assert.match(html, new RegExp(`<a href="${loc.path}\\?signin=1">`));
    assert.match(html, new RegExp(`<a class="alt" href="${loc.path}\\?signup=1">`));
    // owner 2026-10-18: no "the app is taking a long time" note - it frightened users
    assert.doesNotMatch(html, /class="slow-note"/);
    assert.doesNotMatch(html, /data-mount-watch/);
    // Body text was pale (#475569 on a light page); the lead colour is now #1e293b.
    assert.doesNotMatch(html, /\.lead\{[^}]*#475569/);
  }
});

// detectInitialLanguage learns the WeChat UA -----------------------------------
test('detectInitialLanguage: Language/zh_CN in the user agent -> zh-CN', async () => {
  const tmp = mkdtempSync(path.join(tmpdir(), 'china-lang-'));
  const outFile = path.join(tmp, 'd.mjs');
  await build({
    stdin: {
      contents: `export * from '${rel('i18n/detectInitialLanguage.ts')}'; export { detectLocale } from '${rel('i18n/locale.ts')}';`,
      resolveDir: SRC, loader: 'ts',
    },
    bundle: true, format: 'esm', platform: 'node', outfile: outFile, logLevel: 'silent',
  });
  const m = await import(pathToFileURL(outFile).href);
  assert.equal(m.isChineseUserAgent(WECHAT), true);
  assert.equal(m.isChineseUserAgent('Mozilla/5.0 Language/en_US'), false);
  assert.equal(m.detectInitialLanguage({ languages: ['en-US'], timeZone: 'Europe/Paris', userAgent: WECHAT }), 'zh-CN');
  assert.equal(m.detectInitialLanguage({ languages: ['en-US'], timeZone: 'Europe/Paris', userAgent: 'Mozilla/5.0' }), 'en');
  assert.equal(m.detectLocale({ storage: null, languages: ['en'], timeZone: 'Europe/Paris', pathname: '/', userAgent: WECHAT }), 'zh-CN');
  const saved = { getItem: () => 'en', setItem() {} };
  assert.equal(m.detectLocale({ storage: saved, languages: ['en'], timeZone: null, pathname: '/', userAgent: WECHAT }), 'en');
});
