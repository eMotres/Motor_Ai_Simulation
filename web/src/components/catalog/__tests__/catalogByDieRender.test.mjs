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
export { dieMenuItems, motorMenuItems } from '${rel('components/catalog/CatalogByDie.tsx')}';
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

const DUTIES = [{ name: 'water 1', power_kw: 0.01, torque_nm: 0.1 }, { name: 'rated', power_kw: 1.5, torque_nm: 4.25, result: { mass_kg: 2 } }];
const cfg = (name, designation, beside) => ({ name, designation, designation_complete: true, family: designation.split('_')[0], beside,
  duties: DUTIES });
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

// ── coordinator's browser findings on #731: the rated line, the phone width, the row menus ─────────────────────────
test('the rated line quotes the point marked rated, not the first water point', async () => {
  const i18n = m.makeI18n('en'); await ready(i18n);
  const html = decode(m.render(i18n, { dies, isAdmin: true, overview }));
  assert.ok(html.includes('1.50 kW · 4.25 Nm · 2.00 kg') && !html.includes('0.01 kW'), html);
});

test('390 px (phone): every row wraps and nothing in the view is wider than 358 px (390 minus the 16 px gutters)', async () => {
  // A static check of the emitted styles: no DOM measures here (no DOM library in the repository).
  const i18n = m.makeI18n('en'); await ready(i18n);
  const html = m.render(i18n, { dies, isAdmin: true, overview });
  const widths = [...html.matchAll(/(?:^|[;{])\s*(?:min-width|width|flex-basis):\s*(\d+(?:\.\d+)?)px/g)].map((x) => Number(x[1]));
  assert.ok(widths.length > 0 && Math.max(...widths) <= 358, `widths ${widths}`);
  const rows = (html.match(/data-testid="by-die-motor"/g) || []).length;
  assert.equal(rows, 2);
  // each motor row and each die header is a wrapping flex row
  const wrapClasses = new Set([...html.matchAll(/\.(css-[a-z0-9]+)\{[^}]*flex-wrap:wrap/g)].map((x) => x[1]));
  const rowClasses = [...html.matchAll(/class="MuiBox-root (css-[a-z0-9]+)" data-testid="by-die-motor"/g)].map((x) => x[1]);
  assert.ok(rowClasses.length === 2 && rowClasses.every((c) => wrapClasses.has(c)), `${rowClasses} / ${[...wrapClasses]}`);
});

test('the row menus carry every action of the list view, each calling the right handler with the right motor', async () => {
  const i18n = m.makeI18n('en'); await ready(i18n);
  const calls = [];
  const actions = new Proxy({}, { get: (_o, name) => (...args) => calls.push([name, ...args]) });
  const t = (k, o) => i18n.t(`motors:${k}`, o);
  const admin = { canWrite: true, isAdmin: true, standardUser: false };
  const motor = m.motorMenuItems({ die: 'D', config: 'C' }, admin, actions, t);
  assert.deepEqual(motor.map((x) => x.key), ['ren', 'lock', 'his', 'dup', 'bat', 'mov', 'cpy', 'ds', 'rep', 'pdf', 'del']);
  motor.forEach((x) => x.run());
  assert.deepEqual(calls, [['renameCfg', 'D', 'C'], ['toggleCfgLock', 'D', 'C'], ['cfgHistory', 'D', 'C'], ['duplicateCfg', 'D', 'C'],
    ['battery', 'D', 'C'], ['moveCfg', 'D', 'C', 'move'], ['moveCfg', 'D', 'C', 'copy'], ['datasheet', 'D', 'C'],
    ['report', 'D', 'C', false], ['report', 'D', 'C', true], ['deleteCfg', 'D', 'C']]);
  assert.ok(motor.every((x) => x.label && !x.label.includes('byDie.')));
  calls.length = 0;
  const die = m.dieMenuItems({ name: 'D', layer: 'workspace', configs: [] }, admin, actions, t);
  assert.deepEqual(die.map((x) => x.label), ['＋ Add a motor (configuration)', 'Rename die', 'History', 'Duplicate', 'Archive', 'Delete die']);
  die.forEach((x) => x.run());
  assert.deepEqual(calls.map((c) => c[0]), ['addConfig', 'renameDie', 'dieHistory', 'duplicateDie', 'retireOrArchive', 'deleteDie']);
  assert.equal(m.dieMenuItems({ name: 'S', layer: 'shared', configs: [] }, admin, actions, t)[4].label, 'Retire for all users');
  const reader = { canWrite: false, isAdmin: false, standardUser: false };
  assert.deepEqual(m.motorMenuItems({ die: 'D', config: 'C' }, reader, actions, t).map((x) => x.key), ['ds', 'rep', 'pdf', 'mine']);
  assert.deepEqual(m.dieMenuItems({ name: 'D', configs: [] }, reader, actions, t), []);
});
