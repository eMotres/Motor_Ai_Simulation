// node --test: the Motors catalog by die (owner 2026-11-17), RENDERED server-side with the REAL EN and zh-CN locale files
// (esbuild bundles the real component, as i18nRuntime.test.mjs does): designations, not old names; the parameters beside
// the name; the old copy marked; projects and production for admins only.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { join, dirname, resolve } from 'node:path';
import { tmpdir } from 'node:os';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';

const here = dirname(fileURLToPath(import.meta.url));
const src = resolve(here, '..', '..', '..');
const tmp = mkdtempSync(join(tmpdir(), 'bydie-test-'));
const out = join(tmp, 'bundle.mjs');
const rel = (p) => resolve(src, p).split('\\').join('/');

writeFileSync(join(tmp, 'entry.jsx'), `
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { I18nextProvider } from 'react-i18next';
import { createI18n } from '${rel('i18n/core.ts')}';
import CatalogByDie from '${rel('components/catalog/CatalogByDie.tsx')}';
import enCommon from '${rel('locales/en/common.json')}';
import zhCommon from '${rel('locales/zh-CN/common.json')}';
import enMotors from '${rel('locales/en/motors.json')}';
import zhMotors from '${rel('locales/zh-CN/motors.json')}';
export const makeI18n = (lng) => createI18n({ lng, resources: { en: { common: enCommon, motors: enMotors },
  'zh-CN': { common: zhCommon, motors: zhMotors } } });
const noop = () => {};
const actions = new Proxy({}, { get: () => noop });
export const render = (i18n, props) => renderToStaticMarkup(React.createElement(I18nextProvider, { i18n },
  React.createElement(CatalogByDie, { thumbs: () => null, rated: () => ({ duty: 'rated', power_kw: 1.5, torque_nm: 4.25, mass_kg: 2 }),
    active: null, canWrite: true, standardUser: false, busy: false, actions, ...props })));
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
const decode = (s) => s.replace(/&#x27;|&#39;/g, "'").replace(/&quot;/g, '"').replace(/&amp;/g, '&');

const cfg = (name, designation, beside) => ({ name, designation, designation_complete: true, family: designation.split('_')[0], beside });
const dies = [
  { name: 'CILN28', slots: 24, poles: 28, stator_diameter: 200, locked: true, layer: 'shared',
    configs: [cfg('G2-L40', 'CILN28 200_40 24F4SY', { steel: 'B15AHV950M', magnet: 'N52UH_150C', magnet_lamination_mm: 5, slot_insulation: 'Al2O3' })] },
  { name: 'CILN28 vadim 2', slots: 24, poles: 28, stator_diameter: 200, layer: 'workspace',
    configs: [cfg('G2-L40 copy', 'CILN28 200_40 24F4SY', { steel: 'NO20', magnet: 'N52UH_150C' })] },
];
const overview = { items: { 'CILN28/G2-L40': { projects: [{ id: 'p1', link_status: 'current' }], productions: ['CILN28/G2-L40'] } },
  projects: { p1: { id: 'p1', title: 'Pump project', status: 'design' } },
  productions: { 'CILN28/G2-L40': { die: 'CILN28', config: 'G2-L40', status: 'active', current_revision: '1.2' } } };

for (const [lng, solidWord, sliceWord, oldWord, prod, motors] of [
  ['en', 'solid magnet', 'magnet in 5 mm slices', '>old<', 'production 1.2', '2 motors'],
  ['zh-CN', '整体磁钢', '磁钢分段 5 mm', '>旧<', '生产 1.2', '2 台电机']]) {
  test(`${lng}: an admin sees the family, the designations, the parameters beside them, the links and the old copy`, async () => {
    const i18n = m.makeI18n(lng); await ready(i18n);
    const html = decode(m.render(i18n, { dies, isAdmin: true, overview }));
    assert.ok(html.includes('>CILN28 200<') && html.includes(motors), html);           // one family header for both laminations
    assert.equal((html.match(/data-testid="by-die-block"/g) || []).length, 2);
    assert.equal((html.match(/>CILN28 200_40 24F4SY</g) || []).length, 2);
    assert.ok(!html.includes('G2-L40 copy') && !html.includes('>CILN28 vadim 2<'), html);  // no old names shown
    assert.ok(html.includes(`B15AHV950M · N52UH_150C · ${sliceWord} · Al2O3`), html);
    assert.ok(html.includes(`NO20 · N52UH_150C · ${solidWord}`), html);
    assert.ok(html.includes('Pump project') && html.includes(prod) && html.includes(oldWord), html);
    assert.equal((html.match(/data-old="true"/g) || []).length, 1);
    assert.ok(html.includes('1.50 kW · 4.25 Nm · 2.00 kg'), html);
    assert.ok(!/byDie\.|motors:/.test(html), html);                                       // no raw key
  });
  test(`${lng}: a member or the factory gets the same layout without any project, production or old mark`, async () => {
    const i18n = m.makeI18n(lng); await ready(i18n);
    const html = decode(m.render(i18n, { dies, isAdmin: false, canWrite: false, overview }));
    assert.ok(html.includes('>CILN28 200_40 24F4SY<'), html);
    assert.ok(!html.includes('Pump project') && !html.includes(prod) && !html.includes('data-old'), html);
    assert.ok(!html.includes('by-die-die-menu'), html);                                    // no die actions for a reader
  });
}
