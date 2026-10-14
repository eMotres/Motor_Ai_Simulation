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
 *   - writes dist/index.html (English, served at /) and dist/zh/index.html
 *     (Simplified Chinese, served at /zh/): the same file Vite wrote, with
 *       * an accurate <title>, description, canonical, hreflang, Open Graph,
 *         Twitter card and JSON-LD (Organization + SoftwareApplication) in <head>,
 *       * the landing text as plain semantic HTML inside <div id="root">;
 *   - writes dist/robots.txt and dist/sitemap.xml.
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
import path from 'node:path';
import { fileURLToPath } from 'node:url';

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
  ['em', '/landing/em-field.png'],
  ['thermal', '/landing/thermal-map.png'],
  ['mech', '/landing/rotor-displacement.png'],
];

/** The two public pages.  `path` is the canonical URL path. */
export const LOCALES = [
  { id: 'en', lang: 'en', path: '/', file: 'index.html', ogLocale: 'en_US', label: 'English' },
  { id: 'zh-CN', lang: 'zh-CN', path: '/zh/', file: 'zh/index.html', ogLocale: 'zh_CN', label: '简体中文' },
];

/** AI and search crawlers named explicitly in robots.txt. */
export const BOTS = [
  'GPTBot', 'ChatGPT-User', 'OAI-SearchBot', 'ClaudeBot', 'Claude-User', 'Claude-SearchBot',
  'anthropic-ai', 'PerplexityBot', 'Perplexity-User', 'Googlebot', 'Google-Extended', 'Bingbot',
  'Applebot', 'DuckDuckBot', 'CCBot', 'Bytespider', 'Baiduspider',
];
/** Never indexed: the API, OAuth, the MCP endpoint and the consent page. */
export const DISALLOW = ['/api/', '/oauth/', '/mcp', '/agent-consent'];

const esc = (s) => String(s)
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

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
#seo-landing{font-family:Inter,system-ui,-apple-system,"Segoe UI",Roboto,"Noto Sans SC","PingFang SC","Microsoft YaHei",sans-serif;max-width:1100px;margin:0 auto;padding:20px 16px 48px;color:#0f172a;line-height:1.5}
#seo-landing a{color:#1d4ed8}
#seo-landing header{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:center;gap:12px}
#seo-landing .brand{display:flex;align-items:center;gap:10px;font-size:1.5rem;font-weight:700;text-decoration:none;color:inherit}
#seo-landing nav a{margin-left:12px}
#seo-landing h1{font-size:2.4rem;line-height:1.15;margin:32px 0 12px;text-align:center}
#seo-landing h2{font-size:1.6rem;margin:48px 0 8px;text-align:center}
#seo-landing .lead{text-align:center;color:#475569;max-width:760px;margin:0 auto 16px}
#seo-landing ol.chain{list-style:none;display:flex;flex-wrap:wrap;gap:8px;justify-content:center;padding:0}
#seo-landing ol.chain li{border:1.5px solid #1d4ed8;border-radius:99px;padding:3px 12px;font-weight:600}
#seo-landing .grid{display:grid;gap:16px;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));list-style:none;padding:0}
#seo-landing .grid>*{border:1px solid #cbd5e1;border-radius:8px;padding:14px}
#seo-landing h3{font-size:1.05rem;margin:0 0 6px}
#seo-landing figure{margin:0;border:1px solid #cbd5e1;border-radius:8px;overflow:hidden;background:#fff}
#seo-landing figure img{display:block;width:100%;height:auto}
#seo-landing figcaption{padding:8px 12px;font-weight:600;border-top:1px solid #cbd5e1;background:#f8fafc}
#seo-landing footer{margin-top:48px;padding-top:16px;border-top:1px solid #cbd5e1;text-align:center;font-size:.85rem;color:#475569}
@media (prefers-color-scheme:dark){#seo-landing{color:#e2e8f0}#seo-landing a{color:#93c5fd}#seo-landing .lead,#seo-landing footer{color:#94a3b8}#seo-landing .grid>*,#seo-landing footer{border-color:#334155}}
`.trim();

/** The landing text as semantic HTML (what Landing.tsx shows, without the widgets). */
export function renderBody(L, loc) {
  const t = (k) => esc(get(L, k));
  const li = (items) => items.map((s) => `<li>${s}</li>`).join('');
  const card = (h, p) => `<li><h3>${h}</h3><p>${p}</p></li>`;
  const out = [];
  out.push(`<div id="seo-landing" lang="${loc.lang}">`);
  out.push(`<style>${STYLE}</style>`);
  out.push('<header>');
  out.push(`<a class="brand" href="${loc.path}"><img src="/brand/aerostator-logo-64.png" width="40" height="40" alt="AeroStator logo"><span>AeroStator Core</span></a>`);
  out.push('<nav aria-label="Language">' + LOCALES.map((l) =>
    `<a href="${l.path}" hreflang="${l.lang}" lang="${l.lang}">${esc(l.label)}</a>`).join('') + '</nav>');
  out.push('</header>');
  out.push('<main>');
  out.push(`<h1>${t('hero.title')}</h1>`);
  out.push(`<h2 style="margin-top:0;font-size:1.3rem">${t('hero.agents')}</h2>`);
  out.push(`<p class="lead">${t('hero.sub')}</p>`);
  out.push(`<ol class="chain" aria-label="${t('chain.label')}">${li(CHAIN_IDS.map((id) => t(`chain.${id}`)))}</ol>`);
  out.push(`<p class="lead"><a href="mailto:${REQUEST_ACCESS_EMAIL}?subject=${encodeURIComponent('AeroStator Core access')}">${t('hero.request')}</a></p>`);

  out.push(`<section aria-labelledby="concept-h"><h2 id="concept-h">${t('concept.title')}</h2>`);
  out.push(`<p class="lead">${t('concept.sub')}</p><p class="lead">${t('concept.diagramAlt')}</p>`);
  out.push(`<ol class="grid">${STEP_IDS.map((id) => card(t(`chain.${id}`), t(`steps.${id}`))).join('')}</ol></section>`);

  out.push(`<section aria-labelledby="sim-h"><h2 id="sim-h">${t('sim.title')}</h2><p class="lead">${t('sim.sub')}</p>`);
  out.push('<div class="grid">' + FEATURES.map(([k, src]) =>
    `<figure><img src="${src}" loading="lazy" alt="${t(`sim.${k}`)}"><figcaption>${t(`sim.${k}`)}</figcaption></figure>`).join('') + '</div></section>');

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
  out.push(`<p class="lead"><a href="mailto:${REQUEST_ACCESS_EMAIL}?subject=${encodeURIComponent('AeroStator Core access')}">${t('hero.request')}</a></p></section>`);
  out.push('</main>');
  out.push(`<footer><p>${t('footer.licence')}</p><p>${esc(COMPANY)} · <a href="${SITE}">aerostator.com</a></p></footer>`);
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
export function renderPage(template, L, loc) {
  let html = template;
  html = html.replace(/<html lang="[^"]*"/, `<html lang="${loc.lang}"`);
  html = html.replace(/[ \t]*<title>[\s\S]*?<\/title>\r?\n?/, '');
  html = html.replace(/[ \t]*<meta\s+name="description"[^>]*>\r?\n?/, '');
  if (!html.includes('</head>')) throw new Error('template has no </head>');
  // Function replacers: the text may contain "$" sequences that String.replace would expand.
  html = html.replace(/[ \t]*<\/head>/, () => `    ${renderHead(L, loc)}\n  </head>`);
  if (!/<div id="root"><\/div>/.test(html)) throw new Error('template has no empty <div id="root"></div>');
  html = html.replace('<div id="root"></div>', () => `<div id="root">${renderBody(L, loc)}</div>`);
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
    const html = renderPage(template, loadLocale(loc.id, webDir), loc);
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
