// node --test: the project Specification opens on a TABLE of every value (owner 2026-11-18: "when you enter the specification, by
// default it must show a table with all the specification values, to understand it quickly"), with a Table | Wizard switch.
// esbuild bundles the real Specification view of the Projects tab, composed as DevelopmentPage composes it: the page's view hook
// (useSpecViewMode), the Table | Wizard switch in the project title row (SpecViewSwitch, owner after #757: "the specification
// modes are better put in this row") and the view itself (SpecView: what DevelopmentPage mounts for the Specification
// sub-tab) with the APP's i18n and the real EN / zh-CN locale files; the steps come from the real schema
// (src/motor_ai_sim/development/schema.json).  The project fixture is a realistic specification with made-up values.  No DOM
// library is in the repository: the views are rendered server-side, and a row click is the real handler of the table's row
// element, found in the element tree the (hook-free) table returns and called.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { join, dirname, resolve } from 'node:path';
import { tmpdir } from 'node:os';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';

const here = dirname(fileURLToPath(import.meta.url));
const src = resolve(here, '..', '..', '..');
const tmp = mkdtempSync(join(tmpdir(), 'spectable-test-'));
const out = join(tmp, 'bundle.mjs');
const rel = (p) => resolve(src, p).split('\\').join('/');

const store = new Map();
globalThis.localStorage = { getItem: (k) => (store.has(k) ? store.get(k) : null), setItem: (k, v) => store.set(k, String(v)),
  removeItem: (k) => store.delete(k), key: () => null, length: 0 };
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
import SpecView, { SpecViewSwitch, useSpecViewMode } from '${rel('modules/development/components/specWizard/SpecView.tsx')}';
import SpecTable from '${rel('modules/development/components/specWizard/SpecTable.tsx')}';
import { buildWizardSteps } from '${rel('modules/development/components/specWizard/steps.ts')}';
import { wizardProfile } from '${rel('modules/development/components/specWizard/profile.ts')}';
import { wizardCompleteness } from '${rel('modules/development/wizardState.ts')}';
export { specTable, initialSpecView, specHasAnswers, splitUnit } from '${rel('modules/development/components/specWizard/specTableModel.ts')}';
export { developmentViewScope } from '${rel('modules/development/viewState.ts')}';
export { viewKey } from '${rel('lib/viewMemory.ts')}';
export { SpecTable };
export const i18n = appI18n;
// the field renderers of the wizard steps are the page's (useSpecFields); a stub that draws nothing is enough to see which view is open
const fields = new Proxy({}, { get: () => () => null });
export const envFor = (schema, draft, lang, canEditSpec) => {
  const profile = wizardProfile(draft);
  const steps = buildWizardSteps(schema, profile, { staff: true });
  const tr = appI18n.getFixedT(lang, 'development');
  return { tr, lang, draft, profile, spec: { draft, revision: 1, schema_version: 2 }, steps, canEditSpec, busy: false, migrationBusy: false,
    updateDraft: () => undefined, fields, fieldErrors: {}, completeness: wizardCompleteness(steps, draft) };
};
export const renderView = ({ schema, draft, lang = 'en', canEditSpec = true, viewScope = 'development.utest', messages = [], reveal = null }) => {
  const env = envFor(schema, draft, lang, canEditSpec);
  const { completeness, ...rest } = env;
  // the page: the title row (title, then the switch on the Specification tab), then the Specification body
  function Page() {
    const v = useSpecViewMode(viewScope, 'p1', draft, reveal);
    return React.createElement(React.Fragment, null,
      React.createElement('div', { 'data-testid': 'project-title-row' }, React.createElement('span', null, 'Example drone motor'),
        v.mode && React.createElement(SpecViewSwitch, { mode: v.mode, onChange: v.byHand, tr: rest.tr })),
      React.createElement(SpecView, {
        project: { id: 'p1', title: 'Example drone motor', customer_org: '', status: 'spec' }, env: rest, step: 0, onStep: () => undefined, completeness,
        saving: false, saveError: '', onRetrySave: () => undefined, onReloadDraft: () => undefined, submitError: [], submitConflict: false, submitBusy: false,
        onSubmit: () => undefined, reveal: v.wizardReveal, messages, onOpenFieldReview: () => undefined, history: null, assistant: () => null, memoryScope: null,
        mode: v.mode ?? 'wizard', onOpenWizard: v.openWizard, onRevealField: () => undefined }));
  }
  return renderToStaticMarkup(React.createElement(I18nextProvider, { i18n: appI18n }, React.createElement(Page)));
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

const repo = resolve(src, '..', '..');
const schema = JSON.parse(readFileSync(resolve(repo, 'src/motor_ai_sim/development/schema.json'), 'utf8'));
const en = JSON.parse(readFileSync(resolve(src, 'locales/en/development.json'), 'utf8'));
const zh = JSON.parse(readFileSync(resolve(src, 'locales/zh-CN/development.json'), 'utf8'));
const filled = (value) => ({ status: 'filled', value });

// a partly filled project: application, two operating points, supply, efficiency, cooling; the envelope (4_1) and mass (4_2) are
// still empty (required), the mounting is marked "answer later"
const PARTLY = { schema_version: 2, mission_profile: [], production: {}, fields: {
  '1_1': filled('Example multicopter, direct drive'),
  '1_2': filled('Motor and regeneration; both directions'),
  key_points: filled([
    { row_id: 'kp-1', name: 'Cruise', torque_Nm: 8, speed_rpm: 2500, duration_s: 'continuous' },
    { row_id: 'kp-2', name: 'Takeoff', torque_Nm: 22, speed_rpm: 2800, duration_s: 30 }]),
  '1_6': filled({ maximum_rpm: 4500 }),
  '1_7': filled(94),
  '2_1': filled({ chemistry: 'NMC', series_cells: 96, voltage_min_V: 317, voltage_nominal_V: 356, voltage_max_V: 403 }),
  '4_3': { status: 'not_known_yet', value: '' },
} };
const EMPTY = { schema_version: 2, fields: {}, mission_profile: [], production: {} };

const isWizard = (html) => html.includes('data-testid="spec-wizard"') && !html.includes('data-testid="spec-table"');
const isTable = (html) => html.includes('data-testid="spec-table"') && !html.includes('data-testid="spec-wizard"');
const pressed = (html, which) => new RegExp(`aria-pressed="true"[^>]*data-testid="spec-view-${which}"|data-testid="spec-view-${which}"[^>]*aria-pressed="true"`).test(html);

test('default view: a specification with answers opens on the TABLE, a brand-new empty one in the WIZARD', () => {
  store.clear();
  const partly = m.renderView({ schema, draft: PARTLY });
  assert.ok(isTable(partly), 'partly filled -> table');
  assert.ok(pressed(partly, 'table'), 'the switch shows Table');
  const empty = m.renderView({ schema, draft: EMPTY });
  assert.ok(isWizard(empty), 'empty -> wizard, as today');
  assert.ok(pressed(empty, 'wizard'));
  // a submitted specification also opens on the table (any answer counts, including an "answer later" mark alone)
  assert.ok(isTable(m.renderView({ schema, draft: { ...EMPTY, fields: { '4_3': { status: 'not_known_yet', value: '' } } } })));
  assert.equal(m.initialSpecView(null, EMPTY), 'wizard');
  assert.equal(m.initialSpecView('table', EMPTY), 'wizard', 'a remembered Table never shows an empty specification as a table');
  assert.equal(m.initialSpecView(null, PARTLY), 'table');
});

test('the switch Table | Wizard sits in the project TITLE row for both views, not in the specification card; remembered per viewer', () => {
  store.clear();
  for (const draft of [PARTLY, EMPTY]) {
    const html = m.renderView({ schema, draft });
    const row = html.slice(html.indexOf('data-testid="project-title-row"'), html.indexOf('data-testid="spec-table"') > 0 ? html.indexOf('data-testid="spec-table"') : html.indexOf('data-testid="spec-wizard"'));
    assert.ok(row.includes('data-testid="spec-view-switch"'), 'in the title row');
    assert.equal((html.match(/data-testid="spec-view-switch"/g) || []).length, 1, 'once: no second switch in the card header');
    assert.ok(html.includes('>Table<') && html.includes('>Wizard<'), 'both labels');
  }
  // the viewer chose the Wizard earlier: a specification with answers opens there, for that viewer only
  const scope = 'development.uviewer1';
  store.set(m.viewKey(`${scope}.specView`), JSON.stringify('wizard'));
  const chose = m.renderView({ schema, draft: PARTLY, viewScope: scope });
  const other = m.renderView({ schema, draft: PARTLY, viewScope: 'development.uviewer2' });
  assert.ok(isWizard(chose), 'remembered Wizard');
  assert.ok(isTable(other), 'another viewer keeps the default Table');
  const view = readFileSync(resolve(src, 'modules/development/components/specWizard/SpecView.tsx'), 'utf8');
  assert.match(view, /const byHand = \(next: SpecViewMode\) => \{[^}]*show\(next\); if \(scope\) writeView\(scope, next\); \};/, 'the switch writes the choice');
  assert.match(view, /onOpenField=\{\(fieldId\) => \{ onOpenWizard\(\); onRevealField\(fieldId\); \}\}/, 'a row opens the wizard at the field, without changing the remembered view');
  assert.match(view, /openWizard: \(\) => show\('wizard'\)/, 'openWizard does not write the memory');
  // the card headers carry no switch any more ("N of N required answered" stays there)
  for (const f of ['SpecTable.tsx', 'SpecWizard.tsx']) assert.ok(!readFileSync(resolve(src, 'modules/development/components/specWizard', f), 'utf8').includes('headerExtra'), f);
  // the page: the switch right after the title and its pencil, only on the Specification tab
  const page = readFileSync(resolve(src, 'modules/development/components/DevelopmentPage.tsx'), 'utf8');
  assert.ok(page.includes("<EditOutlinedIcon sx={{ fontSize: 16 }} /></IconButton></Tooltip>}</Box>{view === 'specification' && specView.mode && <SpecViewSwitch mode={specView.mode} onChange={specView.byHand} tr={tr} />}"));
  assert.equal((page.match(/<SpecViewSwitch\b/g) || []).length, 1);
});

test('the table: one section per step, one line per value with its unit, open required values marked, points as their own table', () => {
  store.clear();
  const html = m.renderView({ schema, draft: PARTLY });
  for (const id of ['application', 'operating', 'supply', 'mechanical']) assert.ok(html.includes(`data-section="${id}"`), `section ${id}: ${html.slice(0, 400)}`);
  assert.ok(html.includes('Example multicopter, direct drive'));
  // the operating points as their own table: name, torque, speed, duration, power, role
  const points = html.slice(html.indexOf('data-testid="spec-table-points"'));
  assert.ok(points.includes('Cruise') && points.includes('Takeoff') && points.includes('>22<') && points.includes('>2800<') && points.includes('30 s'));
  assert.ok(points.includes('Torque, N·m') && points.includes('Speed, rpm'));
  // a plain number: value and unit in their own columns
  const sections = m.specTable(m.envFor(schema, PARTLY, 'en', true).steps, PARTLY, { lang: 'en', t: m.i18n.getFixedT('en', 'development') });
  const rows = sections.flatMap((s) => s.rows);
  const eff = rows.find((r) => r.fieldId === '1_7');
  assert.deepEqual([eff.value, eff.unit, eff.state], ['94', '%', 'answered']);
  assert.equal(rows.find((r) => r.fieldId === '2_1').value, 'Battery 96S NMC, 317 / 356 / 403 V');
  // required and empty: a row marked "required"; "answer later" reads as such; an empty OPTIONAL field has no row
  for (const id of ['4_1', '4_2']) assert.equal(rows.find((r) => r.fieldId === id)?.state, 'missing', id);
  assert.equal(rows.find((r) => r.fieldId === '4_3')?.state, 'unknown');
  assert.equal(rows.find((r) => r.fieldId === '1_9'), undefined, 'empty optional: no line');
  assert.ok(html.includes('data-state="missing"') && html.includes('>required<'));
  assert.ok(html.includes('>Answer later<'));
  assert.equal(sections.find((s) => s.stepId === 'operating').points.length, 2);
});

test('comments: an open comment on a field is counted on its row', () => {
  store.clear();
  const messages = [{ created_at: '2026-11-18', text: 'check', field_ref: '1_7' }, { created_at: '2026-11-18', text: 'done', field_ref: '1_7', resolved: true }];
  const html = m.renderView({ schema, draft: PARTLY, messages });
  const row = html.slice(html.indexOf('data-field="1_7"'), html.indexOf('</tr>', html.indexOf('data-field="1_7"')));
  assert.match(row, /data-testid="row-comments" aria-label="1 open comment"[\s\S]*?<\/svg>1<\/span>/, row);
  assert.ok(!row.includes('aria-label=""'), 'a value without a tooltip carries no empty accessible name');
  const none = html.slice(html.indexOf('data-field="1_1"'), html.indexOf('</tr>', html.indexOf('data-field="1_1"')));
  assert.ok(!none.includes('row-comments'), 'a row without open comments shows no count');
});

test('a row click (and Edit in a section header) opens the Wizard at that field; who cannot edit gets no edit', () => {
  const env = m.envFor(schema, PARTLY, 'en', true);
  const opened = [], steps = [];
  const tree = m.SpecTable({ env, project: { id: 'p1', title: 'x' }, messages: [], onOpenField: (f) => opened.push(f), onOpenStep: (i) => steps.push(i) });
  const all = [];
  const walk = (node) => { if (Array.isArray(node)) return node.forEach(walk); if (!node || typeof node !== 'object' || !node.props) return; all.push(node); walk(node.props.children); };
  walk(tree);
  const row = all.find((n) => n.props['data-field'] === '1_7' && n.props.onClick);
  assert.ok(row, 'the efficiency row is clickable');
  assert.equal(row.props.role, 'button'); assert.equal(row.props.tabIndex, 0);
  row.props.onClick();
  row.props.onKeyDown({ key: 'Enter', preventDefault() {} });
  all.find((n) => n.props['data-field'] === 'key_points' && n.props.onClick).props.onClick();
  assert.deepEqual(opened, ['1_7', '1_7', 'key_points']);
  const edit = all.find((n) => n.props['data-testid'] === 'section-edit');
  edit.props.onClick();
  assert.deepEqual(steps, [0]);

  // a viewer without edit rights: no Edit, no clickable row
  const ro = m.envFor(schema, PARTLY, 'en', false);
  const nodes = []; const walk2 = (n) => { if (Array.isArray(n)) return n.forEach(walk2); if (!n || typeof n !== 'object' || !n.props) return; nodes.push(n); walk2(n.props.children); };
  walk2(m.SpecTable({ env: ro, project: { id: 'p1', title: 'x' }, messages: [], onOpenField: () => assert.fail('no edit'), onOpenStep: () => assert.fail('no edit') }));
  assert.ok(!nodes.some((n) => n.props['data-testid'] === 'section-edit'));
  assert.ok(!nodes.some((n) => n.props['data-field'] && n.props.onClick));
  const html = m.renderView({ schema, draft: PARTLY, canEditSpec: false });
  assert.ok(isTable(html) && !html.includes('data-testid="section-edit"') && !html.includes('role="button" tabindex="0" data-field'));
});

test('a field the page asks to open while the table shows switches to the Wizard (a submit error, a comment)', () => {
  store.clear();
  // the page's reveal arrives with the first render when the view mounts on it: shown in the wizard
  const view = readFileSync(resolve(src, 'modules/development/components/specWizard/SpecView.tsx'), 'utf8');
  assert.match(view, /useEffect\(\(\) => \{ if \(n !== seen\.current\) \{ seen\.current = n; setStale\(null\); show\('wizard'\); \} \}, \[n\]\);/);
  assert.match(view, /wizardReveal: reveal && reveal\.n !== stale \? reveal : null/, 'a served reveal is not replayed');
  const page = readFileSync(resolve(src, 'modules/development/components/DevelopmentPage.tsx'), 'utf8');
  assert.match(page, /<SpecView key=\{selected\.id\} mode=\{specView\.mode \?\? 'wizard'\} onOpenWizard=\{specView\.openWizard\} onRevealField=\{revealField\}/, 'the Projects tab mounts the view');
  assert.match(page, /reveal=\{specView\.wizardReveal\} messages=\{messages\}/);
  assert.match(page, /const specView = useSpecViewMode\(user\?\.email \? developmentViewScope\(user\.email\) : null, selected\?\.id \?\? null, spec \? spec\.draft as SpecDraft : null, reveal\);/);
  assert.ok(!/<SpecWizard\b/.test(page), 'the page no longer mounts the wizard directly');
});

test('zh-CN: the table and the switch read in Chinese; every new key has its mirror', () => {
  store.clear();
  const html = m.renderView({ schema, draft: PARTLY, lang: 'zh-CN' });
  assert.ok(isTable(html));
  assert.ok(html.includes(`>${zh.wizard.table.table}<`) && html.includes(`>${zh.wizard.table.wizard}<`));
  assert.ok(html.includes(zh.wizard.table.parameter) && html.includes(zh.wizard.table.unit));
  assert.deepEqual(Object.keys(zh.wizard.table).sort(), Object.keys(en.wizard.table).sort());
  for (const v of [...Object.values(en.wizard.table), ...Object.values(zh.wizard.table)]) assert.doesNotMatch(v, /\{\{/, 'ICU, never {{x}}');
});

test('splitUnit: only a plain number loses its unit to the unit column', () => {
  assert.deepEqual(m.splitUnit('94 %', '%'), { value: '94', unit: '%' });
  assert.deepEqual(m.splitUnit('1,200 rpm', 'rpm'), { value: '1,200', unit: 'rpm' });
  assert.deepEqual(m.splitUnit('Battery 317 / 356 / 403 V', 'V'), { value: 'Battery 317 / 356 / 403 V', unit: '' });
  assert.deepEqual(m.splitUnit('2031-01-15', 'date'), { value: '2031-01-15', unit: '' });
});
