/**
 * The crawlable landing (scripts/prerender-landing.mjs), tested without a browser.
 *
 * aerostator.com is a Vite SPA whose built HTML is an empty `<div id="root">`;
 * crawlers that do not run JavaScript (GPTBot, ClaudeBot, PerplexityBot, ...)
 * saw nothing (2026-10-14).  The post-build script writes the landing text into
 * the HTML.  These checks run the script on a copy of the dev template in a temp
 * dir (so they need no `vite build`) and, when `web/dist` exists, check the real
 * build output too:
 *   - the English page carries the slogans and section headings, the Chinese
 *     page the Chinese ones (and not the English ones);
 *   - canonical / hreflang / Open Graph / Twitter / JSON-LD are present and the
 *     JSON-LD parses; nothing claims "patented";
 *   - robots.txt names the AI bots and the sitemap; sitemap.xml lists both pages;
 *     llms.txt exists with the key links; the API and app-only routes are
 *     disallowed in every robots group;
 *   - the id lists the static page uses match landingLinks.ts / conceptLayout.ts.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const WEB = path.resolve(HERE, '..', '..', '..', '..');
const read = (...p) => fs.readFileSync(path.join(WEB, ...p), 'utf8');
const mod = await import(pathToFileURL(path.join(WEB, 'scripts', 'prerender-landing.mjs')).href);

const en = JSON.parse(read('src/locales/en/landing.json'));
const zh = JSON.parse(read('src/locales/zh-CN/landing.json'));

const SECTION_KEYS = ['hero.title', 'hero.agents', 'hero.sub', 'concept.title', 'sim.title',
  'open.title', 'apps.title', 'start.title', 'cta.title', 'footer.licence'];
const dig = (tree, k) => k.split('.').reduce((o, p) => o[p], tree);

/** Run the script on the dev template; returns the output dir. */
function buildFixture() {
  const dist = fs.mkdtempSync(path.join(os.tmpdir(), 'seo-dist-'));
  fs.writeFileSync(path.join(dist, 'index.html'), read('index.html'));
  mod.prerender(dist, { now: new Date('2026-10-14T00:00:00Z') });
  return dist;
}

const targets = [{ name: 'fixture', dist: buildFixture() }];
const realDist = path.join(WEB, 'dist');
if (fs.existsSync(path.join(realDist, 'zh-cn', 'index.html'))) targets.push({ name: 'dist', dist: realDist });

const rootBody = (html) => html.slice(html.indexOf('<div id="root">'), html.indexOf('</body>'));
const textOf = (html) => rootBody(html).replace(/<style>[\s\S]*?<\/style>/g, '').replace(/<[^>]+>/g, ' ');
const jsonLdBlocks = (html) => [...html.matchAll(/<script type="application\/ld\+json">([\s\S]*?)<\/script>/g)]
  .map((m) => JSON.parse(m[1]));

for (const { name, dist } of targets) {
  test(`${name}: English page has the landing text in the raw HTML`, () => {
    const html = fs.readFileSync(path.join(dist, 'index.html'), 'utf8');
    const text = textOf(html);
    for (const k of SECTION_KEYS) assert.ok(text.includes(dig(en, k)), `EN page lacks ${k}`);
    assert.ok(text.includes('From idea to product, together'));
    for (const id of mod.APP_IDS) assert.ok(text.includes(en.apps[id].title), `app card ${id}`);
    for (const id of mod.STEP_IDS) assert.ok(text.includes(en.steps[id]), `step ${id}`);
    assert.match(html, /<html lang="en"/);
    assert.match(html, /<div id="root"><div id="seo-landing"/);
    assert.match(html, /<script type="module"[^>]*src="[^"]*"/, 'the SPA bundle is still loaded');
    assert.ok(Buffer.byteLength(html) > 6000, 'page is more than the empty shell');
  });

  test(`${name}: Chinese page is in Chinese, with its own head`, () => {
    const html = fs.readFileSync(path.join(dist, 'zh-cn', 'index.html'), 'utf8');
    const text = textOf(html);
    for (const k of SECTION_KEYS) assert.ok(text.includes(dig(zh, k)), `ZH page lacks ${k}`);
    assert.ok(!text.includes('From idea to product, together'));
    assert.match(html, /<html lang="zh-CN"/);
    assert.ok(html.includes(`<title>${zh.meta.title}</title>`));
    assert.ok(html.includes('<link rel="canonical" href="https://aerostator.com/zh-cn/" />'));
  });

  test(`${name}: head has title, description, canonical, hreflang, OG, Twitter`, () => {
    const html = fs.readFileSync(path.join(dist, 'index.html'), 'utf8');
    assert.ok(html.includes(`<title>${en.meta.title}</title>`));
    assert.equal((html.match(/<title>/g) || []).length, 1);
    assert.equal((html.match(/<meta name="description"/g) || []).length, 1);
    assert.ok(html.includes(`<meta name="description" content="${en.meta.description}" />`));
    assert.ok(!/order high-performance|\bEV\b/.test(html), 'old description is gone');
    assert.ok(html.includes('<link rel="canonical" href="https://aerostator.com/" />'));
    for (const h of ['en', 'zh-CN', 'x-default']) assert.ok(html.includes(`hreflang="${h}"`), h);
    for (const p of ['og:title', 'og:description', 'og:url', 'og:image', 'og:type', 'twitter:card',
      'twitter:image']) assert.ok(html.includes(`"${p}"`), p);
    assert.ok(html.includes('https://aerostator.com/brand/og-image.png'));
  });

  test(`${name}: JSON-LD parses (Organization + SoftwareApplication), never says patented`, () => {
    for (const f of ['index.html', 'zh-cn/index.html']) {
      const html = fs.readFileSync(path.join(dist, f), 'utf8');
      const blocks = jsonLdBlocks(html).flat();
      const org = blocks.find((b) => b['@type'] === 'Organization');
      const app = blocks.find((b) => b['@type'] === 'SoftwareApplication');
      assert.equal(org.name, 'MOTRES d.o.o.');
      assert.ok(org.logo.startsWith('https://aerostator.com/'));
      assert.equal(app.license, 'https://www.apache.org/licenses/LICENSE-2.0');
      assert.equal(app.offers.price, '0');
      assert.ok(!/\bpatented\b/i.test(html), 'must say patent pending, never patented');
    }
  });

  test(`${name}: robots.txt, sitemap.xml and llms.txt are real files`, () => {
    const robots = fs.readFileSync(path.join(dist, 'robots.txt'), 'utf8');
    const groups = robots.split(/\n\n+/).filter((g) => /^User-agent:/m.test(g));
    for (const bot of ['GPTBot', 'ChatGPT-User', 'OAI-SearchBot', 'ClaudeBot', 'PerplexityBot',
      'Googlebot', 'Bingbot']) assert.ok(robots.includes(`User-agent: ${bot}`), bot);
    for (const g of groups) {
      assert.match(g, /^Allow: \/$/m);
      for (const d of ['/api/', '/oauth/', '/mcp', '/agent-consent'])
        assert.ok(g.includes(`Disallow: ${d}`), `${d} in ${g.split('\n')[0]}`);
    }
    assert.match(robots, /^Sitemap: https:\/\/aerostator\.com\/sitemap\.xml$/m);
    const sm = fs.readFileSync(path.join(dist, 'sitemap.xml'), 'utf8');
    assert.ok(sm.includes('<loc>https://aerostator.com/</loc>'));
    assert.ok(sm.includes('<loc>https://aerostator.com/zh-cn/</loc>'));
    assert.equal((sm.match(/<loc>/g) || []).length, 2, 'public pages only');
    assert.ok(!sm.includes('/api') && !sm.includes('agent-consent'));
    const llms = fs.existsSync(path.join(dist, 'llms.txt'))
      ? fs.readFileSync(path.join(dist, 'llms.txt'), 'utf8') : read('public/llms.txt');
    assert.match(llms, /^# AeroStator/);
    for (const k of ['https://aerostator.com/', 'github.com/eMotres/Motor_Ai_Simulation',
      'COMPONENT_SDK.md', 'MODULE_CONTRIBUTION_RULES.md', 'BYO_COMPUTE.md', 'MCP_DISCOVERY.md',
      'https://aerostator.com/mcp', 'support@aerostator.com']) assert.ok(llms.includes(k), `llms.txt lacks ${k}`);
    assert.ok(!/\bpatented\b/i.test(llms));
  });
}

test('static page id lists match the app (landingLinks.ts, conceptLayout.ts)', () => {
  const links = read('src/components/landing/landingLinks.ts');
  const list = (src, name) => [...src.match(new RegExp(`${name}[^=]*=\\s*\\[([^\\]]*)\\]`))[1]
    .matchAll(/'([^']+)'/g)].map((m) => m[1]);
  assert.deepEqual(mod.CHAIN_IDS, list(links, 'CHAIN_IDS'));
  assert.deepEqual(mod.APP_IDS, list(links, 'APP_IDS'));
  assert.deepEqual(mod.OPEN_IDS, list(links, 'OPEN_IDS'));
  assert.deepEqual(mod.STEP_IDS, list(read('src/components/landing/conceptLayout.ts'), 'STEP_IDS'));
  const ids = [...links.matchAll(/id: '(\w+)', href: /g)].map((m) => m[1]);
  assert.deepEqual(mod.DEV_LINKS.map(([id]) => id), ids);
  assert.ok(links.includes(`REPO_URL = '${mod.REPO_URL}'`));
  assert.ok(links.includes(`BRANCH = '${mod.BRANCH}'`));
  const landing = read('src/components/landing/Landing.tsx');
  assert.ok(landing.includes(`REQUEST_ACCESS_EMAIL = '${mod.REQUEST_ACCESS_EMAIL}'`));
  for (const [, src] of mod.FEATURES) assert.ok(landing.includes(`'${src}'`), src);
});

test('browser tab titles equal the pre-rendered titles; /zh-cn/ opens in Chinese', () => {
  const locale = read('src/i18n/locale.ts');
  assert.ok(locale.includes(`en: '${en.meta.title}'`));
  assert.ok(locale.includes(`'zh-CN': '${zh.meta.title}'`));
  assert.ok(/localeFromPath/.test(locale));
});

test('nginx serves crawler files as real files and keeps API routes out of indexes', () => {
  const nginx = fs.readFileSync(path.join(WEB, '..', 'deploy', 'nginx.conf'), 'utf8');
  assert.ok(/location = \/zh-cn\/ \{/.test(nginx) && /try_files \/zh-cn\/index\.html/.test(nginx));
  assert.ok(!/location = \/llms\.txt/.test(nginx), '/llms.txt must be the static file');
  for (const loc of ['/api/', '= /mcp', '/oauth/', '= /agent-consent']) {
    const re = new RegExp(`location ${loc.replace(/\//g, '\\/')} \\{[\\s\\S]*?\\n    \\}`);
    const m = nginx.match(re);
    assert.ok(m && /X-Robots-Tag "noindex, nofollow"/.test(m[0]), `${loc} needs X-Robots-Tag`);
  }
});

// ── the static concept diagram (no JavaScript needed) ───────────────────────────
// A visitor on a slow cross-border link saw the hero text and the step pills but NO
// diagram until the 1.2 MB bundle ran (owner screenshot, 2026-10-15).  The pre-render now
// renders the app's own ConceptDiagramView to inline SVG; these checks hold it there.
const STEP_LABEL_IDS = ['design', 'simulation', 'configure', 'model3d', 'production'];
const RING_IDS = ['open', 'agents', 'modules', 'compute'];

for (const { name, dist } of targets) {
  for (const [file, L, lang] of [['index.html', en, 'EN'], [path.join('zh-cn', 'index.html'), zh, 'ZH']]) {
    test(`${name}: ${lang} page carries the concept diagram as inline SVG, in its own language`, () => {
      const html = fs.readFileSync(path.join(dist, file), 'utf8');
      const m = html.match(/<div class="concept" data-testid="concept-diagram">([\s\S]*?)<\/div>\s*<p class="sr-only">/);
      assert.ok(m, `${file}: no static diagram block`);
      const block = m[1];
      assert.match(block, /<svg [^>]*viewBox="0 0 1200 760"[^>]*role="img"/);
      for (const id of STEP_LABEL_IDS) {
        assert.ok(block.includes(`>${L.chain[id]}</text>`), `${lang}: step label ${id}`);
        assert.ok(block.includes(`>${L.concept[id].sub}</text>`), `${lang}: step sub ${id}`);
      }
      for (const id of ['idea', 'product']) assert.ok(block.includes(`>${L.chain[id]}</text>`), `${lang}: ${id}`);
      for (const id of RING_IDS) {
        assert.ok(block.includes(`>${L.concept[id].title}</text>`), `${lang}: ring ${id}`);
        assert.ok(block.includes(`>${L.concept[id].sub}</text>`), `${lang}: ring sub ${id}`);
      }
      assert.ok(block.includes(`>${L.concept.core}</text>`), `${lang}: core label`);
      // the motor render is a plain same-origin <img>, and the description stays for bots / readers
      assert.match(block, /<img src="\/landing\/core-l12\.webp"/);
      assert.ok(html.includes(`<p class="sr-only">${L.concept.diagramAlt}</p>`), `${lang}: text description`);
      // the other language's labels are not in this page's diagram
      const other = L === en ? zh : en;
      assert.ok(!block.includes(`>${other.chain.design}</text>`), 'wrong-language label');
    });

    test(`${name}: ${lang} static page is LIGHT unless the visitor chose dark (no prefers-color-scheme flash)`, () => {
      const html = fs.readFileSync(path.join(dist, file), 'utf8');
      assert.doesNotMatch(html, /prefers-color-scheme/, 'the static block must not follow the OS scheme');
      assert.match(html, /#seo-landing\{[^}]*background:#f7f6f3/);
      assert.match(html, /html\[data-theme="dark"\] #seo-landing\{/);
      assert.match(html, /<script data-theme-init>[\s\S]*app\.themeMode/);
    });
  }
}

// ── the three simulation pictures: WebP, with their size, lazy and low priority ──
for (const { name, dist } of targets) {
  test(`${name}: simulation pictures are WebP <img>s with width/height, lazy and fetchpriority=low`, () => {
    for (const file of ['index.html', path.join('zh-cn', 'index.html')]) {
      const html = fs.readFileSync(path.join(dist, file), 'utf8');
      const figs = [...html.matchAll(/<figure><img ([^>]*)>/g)].map((m) => m[1]);
      assert.equal(figs.length, 3, `${file}: three figures`);
      for (const a of figs) {
        assert.match(a, /src="\/landing\/[a-z-]+\.webp"/);
        assert.match(a, /width="884" height="76[67]"/);
        assert.match(a, /loading="lazy"/);
        assert.match(a, /fetchpriority="low"/);
      }
      assert.doesNotMatch(html, /\/landing\/[a-z-]+\.png/);
    }
  });
}
