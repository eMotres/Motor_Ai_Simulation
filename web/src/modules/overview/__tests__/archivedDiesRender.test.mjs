// node --test: the die archive on the Overview and in the Motors catalog (owner 2026-11-17), with the REAL locale files in
// EN and zh-CN: the Archived list is RENDERED server-side (esbuild bundles the real component, as i18nRuntime.test.mjs does),
// and every message the archive flow shows is formatted by the real i18next with its parameters filled.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { join, dirname, resolve } from 'node:path';
import { tmpdir } from 'node:os';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';

const here = dirname(fileURLToPath(import.meta.url));
const src = resolve(here, '..', '..', '..');
const tmp = mkdtempSync(join(tmpdir(), 'archive-test-'));
const out = join(tmp, 'bundle.mjs');
const rel = (p) => resolve(src, p).split('\\').join('/');

writeFileSync(join(tmp, 'entry.jsx'), `
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { I18nextProvider } from 'react-i18next';
import { createI18n } from '${rel('i18n/core.ts')}';
import ArchivedDies from '${rel('modules/overview/ArchivedDies.tsx')}';
import enCommon from '${rel('locales/en/common.json')}';
import zhCommon from '${rel('locales/zh-CN/common.json')}';
import enMotors from '${rel('locales/en/motors.json')}';
import zhMotors from '${rel('locales/zh-CN/motors.json')}';
export const makeI18n = (lng) => createI18n({ lng, resources: { en: { common: enCommon, motors: enMotors },
  'zh-CN': { common: zhCommon, motors: zhMotors } } });
export const render = (i18n, dies) => renderToStaticMarkup(React.createElement(I18nextProvider, { i18n },
  React.createElement(ArchivedDies, { dies, onRestore: () => {} })));
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

const DIES = [{ die: 'OLDDIE 12_40', layer: 'workspace', by: 'admin@example.test', at: '2026-11-17T10:00:00Z', configs: ['L10'], retired: false },
  { die: 'NEWDIE 12_40', layer: 'shared', by: 'admin@example.test', at: '2026-11-17T11:00:00Z', configs: null, retired: true }];

for (const [lng, head, restore] of [['en', '2 archived dies', 'Restore'], ['zh-CN', '2 个已归档冲片', '恢复']]) {
  test(`${lng}: the Archived list renders each die with its Restore`, async () => {
    const i18n = m.makeI18n(lng); await ready(i18n);
    const html = m.render(i18n, DIES);
    assert.ok(html.includes(`>${head}<`), html);
    assert.ok(html.includes('OLDDIE 12_40 · 1') && html.includes('>NEWDIE 12_40<'), html);
    assert.equal((html.match(new RegExp(`aria-label="${restore}"`, 'g')) || []).length, 2, html);
    assert.ok(!html.includes('overview.archive'), html);                    // no raw key
  });

  test(`${lng}: every archive message is a string with its parameters filled`, async () => {
    const i18n = m.makeI18n(lng); await ready(i18n);
    const t = (k, o) => i18n.t(k, o);
    const die = 'OLDDIE 12_40';
    const msgs = [
      t('common:overview.archive.tip'), t('common:overview.archive.title', { die }), t('common:overview.archive.body', { die }),
      t('common:overview.archive.confirm'), t('common:overview.archive.shared', { die }),
      t('common:overview.archive.refused', { die, why: 'X' }), t('common:overview.archive.archivedTip'), t('common:overview.archive.retiredTip'),
      ...['running_job', 'open_production', 'project_link', 'product_revision'].map((c) => t(`common:overview.archive.refusal.${c}`, { names: 'Pump' })),
      ...['archiveDieTip', 'archiveDieConfirm'].map((k) => t(`motors:${k}`)),
      ...['archiveDieTitle', 'archiveDieBody', 'archiveDieDone', 'archiveDieRestored'].map((k) => t(`motors:${k}`, { die })),
      t('motors:restoreRetiredHint', { names: die }),
    ];
    for (const s of msgs) {
      assert.equal(typeof s, 'string');
      assert.ok(s && !/\{|archive\.|archiveDie/.test(s), `${lng}: ${s}`);
    }
    for (const s of msgs.slice(1, 3)) assert.ok(s.includes(die), s);
    for (const s of msgs.slice(8, 12)) assert.ok(s.includes('Pump'), s);
  });
}

test('every literal archive key the pages use exists in both locales', () => {
  const keys = (file, re) => [...readFileSync(resolve(src, file), 'utf8').matchAll(re)].map((x) => x[1]);
  const load = (lng, ns) => JSON.parse(readFileSync(resolve(src, `locales/${lng}/${ns}.json`), 'utf8'));
  const look = (o, k) => k.split('.').reduce((a, p) => (a && typeof a === 'object' ? a[p] : undefined), o);
  const ov = [...keys('modules/overview/OverviewPage.tsx', /\bt\('(overview\.archive\.[A-Za-z_.]+)'/g),
    ...keys('modules/overview/ArchivedDies.tsx', /\bt\('(overview\.archive\.[A-Za-z_.]+)'/g)];
  const fc = keys('components/catalog/FamilyCatalog.tsx', /\btx\('(archiveDie[A-Za-z]*)'/g);
  assert.ok(ov.length >= 8 && fc.length >= 5, `${ov} ${fc}`);
  for (const lng of ['en', 'zh-CN']) {
    assert.deepEqual(ov.filter((k) => typeof look(load(lng, 'common'), k) !== 'string'), []);
    assert.deepEqual(fc.filter((k) => typeof load(lng, 'motors')[k] !== 'string'), []);
  }
});
