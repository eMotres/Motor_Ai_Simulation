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
import { DesignationDecoder } from '${rel('components/common/MotorDesignation.tsx')}';
import enCommon from '${rel('locales/en/common.json')}';
import zhCommon from '${rel('locales/zh-CN/common.json')}';
import enMotors from '${rel('locales/en/motors.json')}';
import zhMotors from '${rel('locales/zh-CN/motors.json')}';
export const makeI18n = (lng) => createI18n({ lng, resources: { en: { common: enCommon, motors: enMotors },
  'zh-CN': { common: zhCommon, motors: zhMotors } } });
export const decoder = (i18n, materials) => renderToStaticMarkup(React.createElement(I18nextProvider, { i18n },
  React.createElement(DesignationDecoder, { segments: { turns: { value: '7', source: 'name' } }, materials })));
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

const DUTIES = [{ name: 'water 1', power_kw: 0.01, torque_nm: 0.1 },
  { name: 'rated', power_kw: 1.5, torque_nm: 4.25, rpm: 3370, current_arms: 12, result: { mass_kg: 2, efficiency_pct: 91.25, ripple_pct: 4.04 },
    runs: [{ drive: 'sine', primary: true, steps: 96, gap_layers: 2 }] }];
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
    assert.ok(!html.includes('B15AHV950M') && !html.includes('Al2O3'), html);           // the materials left the row
    assert.ok(html.includes('1.50 kW') && html.includes('4.25 N·m') && html.includes('3370 rpm') && html.includes('91.3 %')
      && html.includes('4.0 %') && html.includes('12.0 A'), html);
    assert.ok(html.includes('Pump project') && html.includes(prod) && html.includes(oldWord), html);
    assert.equal((html.match(/data-old="true"/g) || []).length, 1);
    assert.ok(html.includes('>2.00 kg<') && !html.includes('4.25 Nm ·'), html);             // the right side keeps only the mass
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
  assert.ok(html.includes('1.50 kW') && !html.includes('0.01 kW'), html);
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

// ── owner 2026-11-17: the continuous point beside the name (~/jobs/TASK_catalog_row_values.md) ──────────────────────
const owner = { name: 'CIANO14 40 new', slots: 12, poles: 14, stator_diameter: 40, layer: 'shared', configs: [{
  name: 'L20', designation: 'CIANO14 40_20 7F2SY', designation_complete: true, family: 'CIANO14 40',
  beside: { steel: '20SW1200', magnet: 'F52SH_150C', slot_insulation: 'Nomex' },
  battery: { cells: 12, v_cell_nom: 3.7, v_min: 36, v_max: 50.4, v_nom: 44.4 },
  duties: [{ name: 'rated', power_kw: 1.11, torque_nm: 0.82, rpm: 13000, current_arms: 37.0,
    result: { efficiency_pct: 92.8, ripple_pct: 5.0, mass_kg: 0.61 }, runs: [{ drive: 'sine', primary: true, steps: 48, gap_layers: 1 }] }] }] };
const text = (html) => decode(html).replace(/<style[^>]*>[\s\S]*?<\/style>/g, '').replace(/<[^>]+>/g, '');

for (const [lng, mat] of [['en', 'Materials (not in the name)'], ['zh-CN', '材料（不在名称中）']]) {
  test(`${lng}: the owner's example row reads power · torque · speed · efficiency · ripple · battery · phase current`, async () => {
    const i18n = m.makeI18n(lng); await ready(i18n);
    const html = m.render(i18n, { dies: [owner], isAdmin: false, canWrite: false, overview: null });
    const line = text(html.slice(html.indexOf('data-testid="by-die-values"'))).split('0.61 kg')[0];
    assert.ok(line.includes('1.11 kW · 0.82 N·m · 13000 rpm · 92.8 % · 5.0 % · 44.4 V · 37.0 A'), line);
    assert.ok(!html.includes('20SW1200') && !html.includes('Nomex'), html);
    // the ripple was solved at 48 steps / 1 row: muted, the reason in its tooltip (aria-label in the markup)
    const rip = html.slice(html.indexOf('data-testid="by-die-v-ripple"') - 400, html.indexOf('data-testid="by-die-v-ripple"') + 200);
    assert.ok(/aria-label="[^"]*48[^"]*1[^"]*96[^"]*2/.test(decode(rip)), rip);
    assert.ok(decode(html).includes(lng === 'en' ? '12 cells; range 36-50.4 V' : '12 节；范围 36-50.4 V'), html);
  });
  test(`${lng}: the materials are in the decoder popover, in a block of their own`, async () => {
    const i18n = m.makeI18n(lng); await ready(i18n);
    const html = decode(m.decoder(i18n, [{ label: 'Steel', value: '20SW1200' }, { label: 'Slot insulation', value: 'Nomex' }]));
    assert.ok(html.includes(`>${mat}<`) && html.includes('>20SW1200<') && html.includes('>Nomex<'), html);
  });
}

test('no battery: the line-line peak voltage of the point, labelled so; a missing value is a dash', async () => {
  const i18n = m.makeI18n('en'); await ready(i18n);
  const nob = { ...owner, configs: [{ ...owner.configs[0], battery: null,
    duties: [{ ...owner.configs[0].duties[0], current_arms: null, result: { ...owner.configs[0].duties[0].result, v_ll_peak_v: 48.7 } }] }] };
  const html = decode(m.render(i18n, { dies: [nob], isAdmin: false, canWrite: false, overview: null }));
  assert.ok(text(html).includes('48.7 V · —'), text(html));
  assert.ok(html.includes('aria-label="No battery set: line-line peak voltage of the point"'), html);
});
