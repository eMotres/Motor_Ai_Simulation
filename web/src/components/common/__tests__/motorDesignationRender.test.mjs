// node --test: the designation with its "≠" mark (results computed for another name, reader side of #724), RENDERED
// server-side with the real locale files in EN and zh-CN (coordinator 2026-11-17: nothing is done without a render test
// of the page with the real locale files).  esbuild bundles the real component, as i18nRuntime.test.mjs does.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { join, dirname, resolve } from 'node:path';
import { tmpdir } from 'node:os';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';

const here = dirname(fileURLToPath(import.meta.url));
const src = resolve(here, '..', '..', '..');
const tmp = mkdtempSync(join(tmpdir(), 'designation-test-'));
const out = join(tmp, 'bundle.mjs');
const rel = (p) => resolve(src, p).split('\\').join('/');

writeFileSync(join(tmp, 'entry.jsx'), `
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { I18nextProvider } from 'react-i18next';
import { createI18n } from '${rel('i18n/core.ts')}';
import { DesignationName, DesignationDecoder } from '${rel('components/common/MotorDesignation.tsx')}';
import enCommon from '${rel('locales/en/common.json')}';
import zhCommon from '${rel('locales/zh-CN/common.json')}';
export const makeI18n = (lng) => createI18n({ lng, resources: { en: { common: enCommon }, 'zh-CN': { common: zhCommon } } });
const wrap = (i18n, el) => renderToStaticMarkup(React.createElement(I18nextProvider, { i18n }, el));
export const name = (i18n, answer) => wrap(i18n, React.createElement(DesignationName, { answer }));
export const decoder = (i18n, answer) => wrap(i18n, React.createElement(DesignationDecoder,
  { segments: answer.segments, checks: answer.checks, otherNames: answer.other_names }));
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

const NOW = 'CILN28 200_65 16F4SY', OLD = 'CILN28 200_65 17F4SY';
const answer = (other_names) => ({ designation: NOW, complete: true,
  segments: { turns: { value: '16', source: 'parameters' }, wire: { value: 'F', source: 'parameters' } }, other_names });
const marked = answer([{ computed_for: OLD, now: NOW, results: [{ duty: 'rated', kind: 'em' }, { duty: 'peak', kind: 'em' }, { duty: 'rated', kind: 'thermal' }] }]);
const decode = (s) => s.replace(/&#x27;|&#39;/g, "'").replace(/&quot;/g, '"').replace(/&amp;/g, '&');

for (const [lng, sentence] of [['en', `3 results were computed for ${OLD}; the motor is now ${NOW}`],
                               ['zh-CN', `3 个结果是按 ${OLD} 计算的；电机现在是 ${NOW}`]]) {
  test(`${lng}: the name carries the mark with the old and the new name`, async () => {
    const i18n = m.makeI18n(lng); await ready(i18n);
    const html = decode(m.name(i18n, marked));
    assert.match(html, /data-testid="motor-designation"/);
    assert.ok(html.includes(`>${NOW}<`), html);                               // the motor's current name stays the name
    assert.ok(html.includes(`aria-label="${sentence}"`), html);
    assert.ok(!html.includes('designation.'), html);                          // no raw key leaked
  });
  test(`${lng}: the decoder lists the results under their own name`, async () => {
    const i18n = m.makeI18n(lng); await ready(i18n);
    const html = decode(m.decoder(i18n, marked));
    assert.ok(html.includes(`${sentence}: rated, peak`), html);
    assert.ok(!html.includes('designation.'), html);
  });
}

test('no mark without results computed for another name (unknown is not "different")', async () => {
  const i18n = m.makeI18n('en'); await ready(i18n);
  for (const a of [answer([]), answer(undefined)]) {
    const html = m.name(i18n, a);
    assert.ok(html.includes(NOW) && !html.includes('designation-other-mark'), html);
  }
  assert.equal(m.name(i18n, { designation: null, complete: false }), '');   // a customer's incomplete name: nothing
});
