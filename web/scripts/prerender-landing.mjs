#!/usr/bin/env node
/**
 * Post-build step: turn the Vite SPA shell into crawlable pages.
 *
 * WHY.  aerostator.com is a Vite single-page app: the HTML that `vite build`
 * writes is `<div id="root"></div>` plus a script.  Crawlers that do not run
 * JavaScript (GPTBot, ChatGPT-User, OAI-SearchBot, ClaudeBot, PerplexityBot and
 * most link-preview fetchers) therefore saw an empty page (2026-10-14).
 *
 * WHAT.  After `vite build` this script, with no dependency beyond Node:
 *   - writes dist/index.html (English, served at /) and dist/zh-cn/index.html
 *     (Simplified Chinese, served at /zh-cn/): the same file Vite wrote, with
 *       * an accurate <title>, description, canonical, hreflang, Open Graph,
 *         Twitter card and JSON-LD (Organization + SoftwareApplication) in <head>,
 *       * the landing text as plain semantic HTML inside <div id="root">;
 *   - writes dist/robots.txt and dist/sitemap.xml.
 * The concept diagram (the "circle": five steps around the AeroStator Core render, the
 * open ecosystem as the outer ring) is in the static page too, as inline SVG plus a plain
 * <img> of the machine: it is rendered here by the SAME React view the app uses
 * (src/components/landing/ConceptDiagram.tsx -> ConceptDiagramView, via scripts/diagram-ssr.tsx
 * bundled with esbuild and run through react-dom/server), so a visitor whose JavaScript is
 * slow or blocked (mainland China) sees it before any script runs.
 * THEME.  The static block is LIGHT, like the app's default (src/theme.ts loadThemeMode); it
 * turns dark only when localStorage says the visitor chose dark (index.html stamps
 * html[data-theme] before first paint).  It never follows prefers-color-scheme, because the
 * app does not, and a dark page that flips to light when the bundle mounts is a flash.
 * React's `createRoot().render()` empties #root on mount, so every visitor that
 * runs JavaScript gets the real app exactly as before (signed-in users, the
 * first-visit language auto-detect, the sign-in dialog).  The static block is
 * only what a crawler, a link preview or a no-JS browser reads.
 *
 * SOURCE OF TRUTH.  The words come from src/locales/<lng>/landing.json (the
 * same namespace Landing.tsx renders), so the static page cannot drift in
 * wording; the lists of ids mirror landingLinks.ts / conceptLayout.ts and a test
 * (src/components/landing/__tests__/seo.test.mjs) compares them.
 *
 * PUBLIC-SAFE ONLY.  Nothing here may carry design data, patent details beyond
 * the page's own "patent pending", or anything behind sign-in.
 *
 * Usage: node scripts/prerender-landing.mjs [distDir]   (default: ./dist)
 */
import fs from 'node:fs';
import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { buildSync } from 'esbuild';

const HERE = path.dirname(fileURLToPath(import.meta.url));
export const WEB = path.resolve(HERE, '..');

export const SITE = 'https://aerostator.com';
export const REPO_URL = 'https://github.com/eMotres/Motor_Ai_Simulation';
export const BRANCH = 'pre-migration-freeze-2026-09-15';
export const REQUEST_ACCESS_EMAIL = 'vadim@motresres.com';
export const COMPANY = 'MOTRES d.o.o.';
export const OG_IMAGE = `${SITE}/brand/og-image.png`;
export const LOGO_URL = `${SITE}/brand/aerostator-logo-512.png`;

/** Mirrors landingLinks.ts (a test compares). */
export const DEV_LINKS = [
  ['github', REPO_URL],
  ['sdk', `${REPO_URL}/blob/${BRANCH}/docs/COMPONENT_SDK.md`],
  ['rules', `${REPO_URL}/blob/${BRANCH}/docs/MODULE_CONTRIBUTION_RULES.md`],
  ['byo', `${REPO_URL}/blob/${BRANCH}/docs/BYO_COMPUTE.md`],
  ['mcp', `${REPO_URL}/blob/${BRANCH}/docs/MCP_DISCOVERY.md`],
];
export const CHAIN_IDS = ['idea', 'design', 'simulation', 'configure', 'model3d', 'production', 'product'];
export const STEP_IDS = ['design', 'simulation', 'configure', 'model3d', 'production'];
export const OPEN_IDS = ['source', 'agents', 'modules', 'compute'];
export const APP_IDS = ['aviation', 'robotics', 'marine', 'yours'];
export const FEATURES = [
  ['em', '/landing/em-field.webp'],
  ['thermal', '/landing/thermal-map.webp'],
  ['mech', '/landing/rotor-displacement.webp'],
];

/** The two public pages.  `path` is the canonical URL path. */
export const LOCALES = [
  { id: 'en', lang: 'en', path: '/', file: 'index.html', ogLocale: 'en_US', label: 'English' },
  { id: 'zh-CN', lang: 'zh-CN', path: '/zh-cn/', file: 'zh-cn/index.html', ogLocale: 'zh_CN', label: '简体中文' },
];

/** AI and search crawlers named explicitly in robots.txt. */
export const BOTS = [
  'GPTBot', 'ChatGPT-User', 'OAI-SearchBot', 'ClaudeBot', 'Claude-User', 'Claude-SearchBot',
  'anthropic-ai', 'PerplexityBot', 'Perplexity-User', 'Googlebot', 'Google-Extended', 'Bingbot',
  'Applebot', 'DuckDuckBot', 'CCBot', 'Bytespider', 'Baiduspider',
];
/** Never indexed: the API, OAuth, the MCP endpoint and the consent page. */
export const DISALLOW = ['/api/', '/oauth/', '/mcp', '/agent-consent', '/preferences', '/zh-cn/preferences'];

/** Pixel size of a WebP file (lossy 'VP8 ', lossless 'VP8L' or extended 'VP8X'), read from
 *  its header, so the static <img> can carry width/height and the card does not shift. */
export function webpSize(buf) {
  if (buf.toString('latin1', 0, 4) !== 'RIFF' || buf.toString('latin1', 8, 12) !== 'WEBP') {
    throw new Error('not a WebP file');
  }
  const kind = buf.toString('latin1', 12, 16);
  if (kind === 'VP8 ') return { w: buf.readUInt16LE(26) & 0x3fff, h: buf.readUInt16LE(28) & 0x3fff };
  if (kind === 'VP8L') {
    const b = buf.readUInt32LE(21);
    return { w: (b & 0x3fff) + 1, h: ((b >> 14) & 0x3fff) + 1 };
  }
  if (kind === 'VP8X') return { w: buf.readUIntLE(24, 3) + 1, h: buf.readUIntLE(27, 3) + 1 };
  throw new Error(`unknown WebP chunk ${kind}`);
}

const esc = (s) => String(s)
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

/** Bundle the app's own diagram view for Node (once per process) and return `renderDiagram`. */
let diagramRenderer = null;
function loadDiagramRenderer(webDir) {
  if (diagramRenderer) return diagramRenderer;
  const outDir = path.join(webDir, 'node_modules', '.cache', 'landing-ssr');
  fs.mkdirSync(outDir, { recursive: true });
  const outfile = path.join(outDir, 'diagram.cjs');
  buildSync({
    entryPoints: [path.join(webDir, 'scripts', 'diagram-ssr.tsx')],
    outfile,
    bundle: true,
    format: 'cjs',
    platform: 'node',
    packages: 'external', // react, react-dom, @mui/material resolve from web/node_modules
    jsx: 'automatic',
    logLevel: 'silent',
  });
  diagramRenderer = createRequire(outfile)(outfile).renderDiagram;
  return diagramRenderer;
}

/** The concept diagram as static SVG + <img> markup, in the locale's words, LIGHT theme. */
export function renderDiagramMarkup(L, webDir = WEB) {
  return loadDiagramRenderer(webDir)(L, 'light');
}

export function loadLocale(id, webDir = WEB) {
  const file = path.join(webDir, 'src', 'locales', id, 'landing.json');
  return JSON.parse(fs.readFileSync(file, 'utf8'));
}

function get(tree, dotted) {
  const v = dotted.split('.').reduce((o, k) => (o == null ? o : o[k]), tree);
  if (typeof v !== 'string') throw new Error(`landing.json: missing string at "${dotted}"`);
  return v;
}

const STYLE = `
html,body,#root{height:auto;overflow:auto}
body{margin:0;background:#f7f6f3}
#seo-landing{box-sizing:border-box;font-family:Inter,-apple-system,BlinkMacSystemFont,system-ui,"Segoe UI",Roboto,"PingFang SC","Noto Sans SC","Microsoft YaHei",sans-serif;font-size:16px;max-width:1200px;margin:0 auto;padding:20px 16px 48px;background:#f7f6f3;color:#0f172a;line-height:1.6;-webkit-text-size-adjust:100%}
#seo-landing a{color:#1d4ed8}
#seo-landing header{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:center;gap:12px}
#seo-landing .brand{display:flex;align-items:center;gap:10px;font-size:1.5rem;font-weight:700;text-decoration:none;color:inherit}
#seo-landing nav a{margin-left:12px}
#seo-landing h1{font-size:2rem;line-height:1.2;margin:28px 0 12px;text-align:center;color:#0f172a}
#seo-landing h2{font-size:1.5rem;margin:44px 0 8px;text-align:center;color:#0f172a}
#seo-landing h3{font-size:1.1rem;margin:0 0 6px;color:#0f172a}
#seo-landing .lead{text-align:center;color:#1e293b;max-width:760px;margin:0 auto 16px}
#seo-landing .auth{display:flex;flex-wrap:wrap;justify-content:center;gap:12px;margin:20px 0}
#seo-landing .auth a{display:inline-block;padding:11px 26px;border-radius:8px;border:2px solid #1d4ed8;font-weight:600;text-decoration:none;background:#1d4ed8;color:#fff}
#seo-landing .auth a.alt{background:#fff;color:#1d4ed8}
#seo-landing .slow-note{display:none;margin:16px 0;padding:12px 16px;border:1px solid #b45309;border-radius:8px;background:#fffbeb;color:#451a03}
#seo-landing.slow .slow-note{display:block}
#seo-landing ol.chain{list-style:none;display:flex;flex-wrap:wrap;gap:8px;justify-content:center;padding:0}
#seo-landing ol.chain li{border:1.5px solid #1d4ed8;border-radius:99px;padding:3px 12px;font-weight:600;background:#fff}
#seo-landing .grid{display:grid;gap:16px;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));list-style:none;padding:0}
#seo-landing .grid>*{border:1px solid #94a3b8;border-radius:8px;padding:14px;background:#fff}
#seo-landing .concept{display:none;max-width:1100px;margin:0 auto 32px}
#seo-landing .sr-only{position:absolute;width:1px;height:1px;margin:-1px;padding:0;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
#seo-landing figure{margin:0;border:1px solid #94a3b8;border-radius:8px;overflow:hidden;background:#fff}
#seo-landing figure img{display:block;width:100%;height:auto}
#seo-landing figcaption{padding:8px 12px;font-weight:600;border-top:1px solid #94a3b8;background:#f1f5f9}
#seo-landing footer{margin-top:48px;padding-top:16px;border-top:1px solid #94a3b8;text-align:center;font-size:.9rem;color:#334155}
html[data-theme="dark"] body{background:#0f172a}html[data-theme="dark"] #seo-landing{background:#0f172a;color:#f1f5f9}html[data-theme="dark"] #seo-landing a{color:#93c5fd}html[data-theme="dark"] #seo-landing h1,html[data-theme="dark"] #seo-landing h2,html[data-theme="dark"] #seo-landing h3{color:#f8fafc}html[data-theme="dark"] #seo-landing .lead{color:#e2e8f0}html[data-theme="dark"] #seo-landing footer{color:#cbd5e1}html[data-theme="dark"] #seo-landing .auth a{background:#3b82f6;border-color:#3b82f6;color:#0f172a}html[data-theme="dark"] #seo-landing .auth a.alt{background:#0f172a;color:#93c5fd;border-color:#93c5fd}html[data-theme="dark"] #seo-landing .grid>*,html[data-theme="dark"] #seo-landing footer,html[data-theme="dark"] #seo-landing figure,html[data-theme="dark"] #seo-landing figcaption{border-color:#475569}html[data-theme="dark"] #seo-landing .grid>*,html[data-theme="dark"] #seo-landing figure,html[data-theme="dark"] #seo-landing figcaption,html[data-theme="dark"] #seo-landing ol.chain li{background:#1e293b}html[data-theme="dark"] #seo-landing .slow-note{background:#451a03;color:#fef3c7;border-color:#f59e0b}html[data-theme="dark"] #seo-landing .concept{background:#fff;border-radius:12px;padding:8px}
@media (min-width:600px){#seo-landing{padding-left:32px;padding-right:32px}}
@media (min-width:700px){#seo-landing h1{font-size:2.4rem}#seo-landing .concept{display:block}}
@media (min-width:900px){#seo-landing .grid.steps{grid-template-columns:repeat(5,1fr)}}
`.trim();

/** Sign in + Request access as plain links: they open the app's e-mail dialog once the
 *  bundle runs (?signin=1 / ?signup=1, see requestedAuthMode in lib/localAuth.ts) and
 *  stay visible and readable when it never does.  Same locale path, so a Chinese page
 *  stays Chinese. */
export function authLinks(L, loc) {
  const t = (k) => esc(get(L, k));
  return `<p class="auth"><a href="${loc.path}?signin=1">${t('hero.signIn')}</a>`
    + `<a class="alt" href="${loc.path}?signup=1">${t('hero.request')}</a></p>`;
}

/** The landing text as semantic HTML (what Landing.tsx shows, without the widgets). */
export function renderBody(L, loc, webDir = WEB) {
  const t = (k) => esc(get(L, k));
  const li = (items) => items.map((s) => `<li>${s}</li>`).join('');
  const card = (h, p) => `<li><h3>${h}</h3><p>${p}</p></li>`;
  const out = [];
  out.push(`<div id="seo-landing" lang="${loc.lang}">`);
  out.push(`<style>${STYLE}</style>`);
  out.push('<header>');
  out.push(`<a class="brand" href="${loc.path}"><img src="/brand/aerostator-logo-64.png" width="40" height="40" alt="AeroStator logo"><span>AeroStator Core</span></a>`);
  // The English link carries ?lang=en: the first-visit script (index.html) saves "en" and
  // stays, so a visitor who picks English is not sent back to /zh-cn/.
  out.push('<nav aria-label="Language">' + LOCALES.map((l) =>
    `<a href="${l.path === '/' ? '/?lang=en' : l.path}" hreflang="${l.lang}" lang="${l.lang}">${esc(l.label)}</a>`).join('') + '</nav>');
  out.push('</header>');
  out.push(`<p class="slow-note" role="status">${t('static.slow')} <a href="mailto:${REQUEST_ACCESS_EMAIL}">${esc(REQUEST_ACCESS_EMAIL)}</a></p>`);
  out.push('<main>');
  out.push(`<h1>${t('hero.title')}</h1>`);
  out.push(`<h2 style="margin-top:0;font-size:1.3rem">${t('hero.agents')}</h2>`);
  out.push(`<p class="lead">${t('hero.sub')}</p>`);
  out.push(`<ol class="chain" aria-label="${t('chain.label')}">${li(CHAIN_IDS.map((id) => t(`chain.${id}`)))}</ol>`);
  out.push(authLinks(L, loc));

  out.push(`<section aria-labelledby="concept-h"><h2 id="concept-h">${t('concept.title')}</h2>`);
  // The diagram shows from 700 px up (below that the step cards say the same, as in the app);
  // its text description stays in the HTML for screen readers and bots.
  out.push(`<p class="lead">${t('concept.sub')}</p>`);
  out.push(`<div class="concept" data-testid="concept-diagram">${renderDiagramMarkup(L, webDir)}</div>`);
  out.push(`<p class="sr-only">${t('concept.diagramAlt')}</p>`);
  out.push(`<ol class="grid steps">${STEP_IDS.map((id) => card(t(`chain.${id}`), t(`steps.${id}`))).join('')}</ol></section>`);

  out.push(`<section aria-labelledby="sim-h"><h2 id="sim-h">${t('sim.title')}</h2><p class="lead">${t('sim.sub')}</p>`);
  out.push('<div class="grid">' + FEATURES.map(([k, src]) => {
    const { w, h } = webpSize(fs.readFileSync(path.join(webDir, 'public', src)));
    return `<figure><img src="${src}" width="${w}" height="${h}" loading="lazy" decoding="async" fetchpriority="low" alt="${t(`sim.${k}`)}"><figcaption>${t(`sim.${k}`)}</figcaption></figure>`;
  }).join('') + '</div></section>');

  out.push(`<section aria-labelledby="open-h"><h2 id="open-h">${t('open.title')}</h2><p class="lead">${t('open.sub')}</p>`);
  out.push(`<ul class="grid">${OPEN_IDS.map((id) => card(t(`open.${id}.title`), t(`open.${id}.text`))).join('')}</ul></section>`);

  out.push(`<section aria-labelledby="apps-h"><h2 id="apps-h">${t('apps.title')}</h2><p class="lead">${t('apps.sub')}</p>`);
  out.push(`<ul class="grid">${APP_IDS.map((id) => card(t(`apps.${id}.title`), t(`apps.${id}.text`))).join('')}</ul></section>`);

  out.push(`<section aria-labelledby="start-h"><h2 id="start-h">${t('start.title')}</h2><ul class="grid">`);
  out.push(card(t('start.everyone.title'), t('start.everyone.text')));
  out.push(card(t('start.engineers.title'),
    `${t('start.engineers.text')} <a href="mailto:${REQUEST_ACCESS_EMAIL}?subject=${encodeURIComponent('AeroStator Core engineer role')}">${t('start.engineers.link')}</a>`));
  out.push(`<li id="developers"><h3>${t('start.developers.title')}</h3><ul>` +
    DEV_LINKS.map(([id, href]) => `<li><a href="${esc(href)}" rel="noopener noreferrer">${t(`start.links.${id}`)}</a></li>`).join('') +
    '</ul></li></ul></section>');

  out.push(`<section aria-labelledby="cta-h"><h2 id="cta-h">${t('cta.title')}</h2>`);
  out.push(`${authLinks(L, loc)}</section>`);
  out.push('</main>');
  out.push(`<footer><p>${t('footer.licence')}</p><p>${esc(COMPANY)} · <a href="${SITE}">aerostator.com</a> · <a href="/terms">${t('footer.terms')}</a> · <a href="/privacy">${t('footer.privacy')}</a></p></footer>`);
  out.push('</div>');
  return out.join('\n');
}

export function jsonLd(L, loc) {
  const description = get(L, 'meta.description');
  return [
    {
      '@context': 'https://schema.org',
      '@type': 'Organization',
      name: COMPANY,
      url: SITE,
      logo: LOGO_URL,
      sameAs: [REPO_URL],
    },
    {
      '@context': 'https://schema.org',
      '@type': 'SoftwareApplication',
      name: 'AeroStator',
      alternateName: 'AeroStator Core',
      url: `${SITE}${loc.path}`,
      description,
      inLanguage: loc.lang,
      applicationCategory: 'EngineeringApplication',
      operatingSystem: 'Web',
      license: 'https://www.apache.org/licenses/LICENSE-2.0',
      isAccessibleForFree: true,
      codeRepository: REPO_URL,
      publisher: { '@type': 'Organization', name: COMPANY, url: SITE },
      offers: { '@type': 'Offer', price: '0', priceCurrency: 'EUR' },
    },
  ];
}

export function renderHead(L, loc) {
  const title = esc(get(L, 'meta.title'));
  const desc = esc(get(L, 'meta.description'));
  const url = `${SITE}${loc.path}`;
  const alt = LOCALES.filter((l) => l.id !== loc.id);
  const ld = JSON.stringify(jsonLd(L, loc)).replace(/</g, '\\u003c');
  return [
    `<title>${title}</title>`,
    `<meta name="description" content="${desc}" />`,
    '<meta name="robots" content="index, follow, max-image-preview:large" />',
    `<link rel="canonical" href="${url}" />`,
    ...LOCALES.map((l) => `<link rel="alternate" hreflang="${l.lang}" href="${SITE}${l.path}" />`),
    `<link rel="alternate" hreflang="x-default" href="${SITE}/" />`,
    '<meta property="og:type" content="website" />',
    '<meta property="og:site_name" content="AeroStator" />',
    `<meta property="og:title" content="${title}" />`,
    `<meta property="og:description" content="${desc}" />`,
    `<meta property="og:url" content="${url}" />`,
    `<meta property="og:locale" content="${loc.ogLocale}" />`,
    ...alt.map((l) => `<meta property="og:locale:alternate" content="${l.ogLocale}" />`),
    `<meta property="og:image" content="${OG_IMAGE}" />`,
    '<meta property="og:image:width" content="1200" />',
    '<meta property="og:image:height" content="630" />',
    '<meta property="og:image:alt" content="AeroStator Core" />',
    '<meta name="twitter:card" content="summary_large_image" />',
    `<meta name="twitter:title" content="${title}" />`,
    `<meta name="twitter:description" content="${desc}" />`,
    `<meta name="twitter:image" content="${OG_IMAGE}" />`,
    `<script type="application/ld+json">${ld}</script>`,
  ].join('\n    ');
}

/** Turn Vite's index.html into the crawlable page of one locale. */
export function renderPage(template, L, loc, webDir = WEB) {
  let html = template;
  html = html.replace(/<html lang="[^"]*"/, `<html lang="${loc.lang}"`);
  html = html.replace(/[ \t]*<title>[\s\S]*?<\/title>\r?\n?/, '');
  html = html.replace(/[ \t]*<meta\s+name="description"[^>]*>\r?\n?/, '');
  if (!html.includes('</head>')) throw new Error('template has no </head>');
  // Function replacers: the text may contain "$" sequences that String.replace would expand.
  html = html.replace(/[ \t]*<\/head>/, () => `    ${renderHead(L, loc)}\n  </head>`);
  if (!/<div id="root"><\/div>/.test(html)) throw new Error('template has no empty <div id="root"></div>');
  html = html.replace('<div id="root"></div>', () => `<div id="root">${renderBody(L, loc, webDir)}</div>`);
  return html;
}

export function renderRobots() {
  const rules = ['Allow: /', ...DISALLOW.map((p) => `Disallow: ${p}`)].join('\n');
  const parts = [
    ['# aerostator.com: the public landing is open to search engines and AI crawlers.',
      '# The API, OAuth, MCP endpoint and consent page are not for indexing.',
      '# (A crawler that has its own group ignores the "*" group, so the rules repeat.)'].join('\n'),
    `User-agent: *\n${rules}`,
    ...BOTS.map((b) => `User-agent: ${b}\n${rules}`),
    `Sitemap: ${SITE}/sitemap.xml`,
  ];
  return parts.join('\n\n') + '\n';
}

export function renderSitemap(date) {
  const alts = LOCALES.map((l) =>
    `    <xhtml:link rel="alternate" hreflang="${l.lang}" href="${SITE}${l.path}"/>`).join('\n')
    + `\n    <xhtml:link rel="alternate" hreflang="x-default" href="${SITE}/"/>`;
  const urls = LOCALES.map((l) =>
    `  <url>\n    <loc>${SITE}${l.path}</loc>\n    <lastmod>${date}</lastmod>\n${alts}\n  </url>`).join('\n');
  return `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:xhtml="http://www.w3.org/1999/xhtml">\n${urls}\n</urlset>\n`;
}

/** Write every generated file into `dist`.  Returns the list of paths written. */
export function prerender(dist, { webDir = WEB, now = new Date() } = {}) {
  const tplPath = path.join(dist, 'index.html');
  if (!fs.existsSync(tplPath)) throw new Error(`${tplPath} not found: run "vite build" first`);
  const template = fs.readFileSync(tplPath, 'utf8');
  const written = [];
  for (const loc of LOCALES) {
    const html = renderPage(template, loadLocale(loc.id, webDir), loc, webDir);
    const out = path.join(dist, loc.file);
    fs.mkdirSync(path.dirname(out), { recursive: true });
    fs.writeFileSync(out, html);
    written.push(out);
  }
  fs.writeFileSync(path.join(dist, 'robots.txt'), renderRobots());
  fs.writeFileSync(path.join(dist, 'sitemap.xml'), renderSitemap(now.toISOString().slice(0, 10)));
  written.push(path.join(dist, 'robots.txt'), path.join(dist, 'sitemap.xml'));
  return written;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const dist = path.resolve(process.argv[2] || path.join(WEB, 'dist'));
  for (const f of prerender(dist)) console.log('prerender:', path.relative(process.cwd(), f));
}
