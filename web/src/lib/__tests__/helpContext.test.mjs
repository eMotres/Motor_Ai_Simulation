// node --test - the window context of the help assistant (lib/supportContext.ts, modules/gears/helpContext.ts).
// Pins: default context = tab ids only; registered contexts merge and unregister; another tab's entry is ignored; buildSupportContext
// carries ui_context; a visitor's context is tab ids only; the Gears context comes from state and carries no numbers; every id the
// web declares has an EN and a zh-CN help document and is in the shared registry.  Needs a Node that strips TypeScript (>= 22.18).
import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

let C = null, G = null;
try { C = await import('../supportContext.ts'); G = await import('../../modules/gears/helpContext.ts'); } catch { /* old Node */ }
const t = C && G ? test : test.skip;
const DOCS = fileURLToPath(new URL('../../../../src/motor_ai_sim/help_docs/', import.meta.url));
const base = { lang: 'en', appVersion: '1', appGitSha: 'x', userAgent: 'ua' };

t('default context is the tab id only', () => {
  C.clearHelpContexts();
  assert.deepEqual(C.buildSupportContext({ ...base, tab: 'cost' }).ui_context, { tab: 'cost' });
  assert.deepEqual(C.buildAnonContext({ tab: 'cost', lang: 'en' }), { lang: 'en', ui_context: { tab: 'cost' } });
});

t('registered contexts merge, unregister, and only the open tab counts', () => {
  C.clearHelpContexts();
  C.setHelpContext('a', { tab: 'gears', sub: 'motor', preset: 'p' });
  C.setHelpContext('b', { tab: 'gears', die: 'D1', status: [] });
  C.setHelpContext('c', { tab: 'other', mode: 'x' });
  const ui = C.buildSupportContext({ ...base, tab: 'gears', route: '/gears' }).ui_context;
  assert.deepEqual(ui, { tab: 'gears', route: '/gears', sub: 'motor', preset: 'p', die: 'D1' });
  C.setHelpContext('a', null); C.setHelpContext('b', null);
  assert.deepEqual(C.buildSupportContext({ ...base, tab: 'gears' }).ui_context, { tab: 'gears' });
  assert.deepEqual(C.buildAnonContext({ tab: 'gears', lang: 'en' }).ui_context, { tab: 'gears' });
  C.clearHelpContexts();
});

t('a visitor context keeps the tab and sub ids only', () => {
  C.clearHelpContexts();
  C.setHelpContext('a', { tab: 'gears', sub: 'point', die: 'D1', error: 'e' });
  assert.deepEqual(C.buildAnonContext({ tab: 'gears', lang: 'en' }), { lang: 'en', ui_context: { tab: 'gears', sub: 'point' } });
  C.clearHelpContexts();
});

const noNumbers = (o) => JSON.stringify(o, (k, v) => { if (typeof v === 'number' && k !== 'stageCount' && k !== 'rows') assert.fail(`number at ${k}`); return v; });

t('the Gears page context comes from the state and carries no result numbers', () => {
  const ctx = G.gearsPageHelp({
    sub: 'spectrum', preset: 'humanoid_robot_joint', stageKinds: ['planetary', 'planetary'],
    ran: { response: { result: { state: 'estimated', passed: false, overall_ratio: 12.884, mass_kg: 3.2 } } }, stale: true,
    errors: [{ path: '/spectrum/bins/0/hours', message: 'must be above 0.5' }], failure: null, recordOpen: false,
  });
  assert.deepEqual(ctx, {
    tab: 'gears', sub: 'spectrum', preset: 'humanoid_robot_joint', stageKinds: ['planetary', 'planetary'], stageCount: 2, record: false,
    calculated: true, stale: true, status: ['state_estimated', 'not_passed', 'inputs_changed'], error: '/spectrum/bins/#/hours: must be above #',
  });
  noNumbers(ctx);
  assert.ok(!JSON.stringify(ctx).includes('12.884') && !JSON.stringify(ctx).includes('3.2'));
});

t('a point has no verdict chip and an unrun page says so; the motor sub-tab leaves result fields to its panel', () => {
  const point = G.gearsPageHelp({ sub: 'point', preset: 'standard', stageKinds: ['pair'], ran: null, stale: false, errors: [], failure: 'network error', recordOpen: true });
  assert.equal(point.calculated, false); assert.deepEqual(point.status, []); assert.equal(point.error, 'network error'); assert.equal(point.record, true);
  const motor = G.gearsPageHelp({ sub: 'motor', preset: 'standard', stageKinds: ['pair'], ran: null, stale: false, errors: [], failure: 'x', recordOpen: false });
  assert.ok(!('calculated' in motor) && !('error' in motor) && !('status' in motor));
});

t('the With motor panel reports the selected motor, configuration and row statuses only', () => {
  const ctx = G.motorPanelHelp({ die: 'CIANO56 400_80', configuration: 'cfg A', source: 'record', rows: 2, statuses: ['ok', 'missing', 'ok'], errors: [], failure: null });
  assert.deepEqual(ctx, { tab: 'gears', sub: 'motor', die: 'CIANO56 400_80', configuration: 'cfg A', source: 'record', rows: 2, calculated: true, status: ['row_ok', 'row_missing'] });
  assert.equal(G.motorPanelHelp({ die: '', configuration: '', source: 'inline', rows: 1, statuses: null, errors: [], failure: null }).calculated, false);
});

t('every id the web declares is in the shared registry and has an EN and a zh-CN document', () => {
  const reg = JSON.parse(readFileSync(`${DOCS}registry.json`, 'utf8'));
  const entry = reg[G.GEARS_HELP_TAB];
  assert.ok(entry, 'gears is registered');
  assert.deepEqual(entry.subs, [...G.GEARS_HELP_SUBS]);
  const ids = [G.GEARS_HELP_TAB, ...G.GEARS_HELP_SUBS.map((s) => `${G.GEARS_HELP_TAB}.${s}`)];
  for (const id of ids) {
    assert.ok(entry.docs.includes(id), `${id} listed`);
    for (const lg of ['en', 'zh-CN']) assert.ok(existsSync(`${DOCS}${id}.${lg}.md`), `${id}.${lg}.md exists`);
  }
});

// ── the Simulations pages (lib/helpContexts.ts) ──────────────────────────────
let H = null;
try { H = await import('../helpContexts.ts'); } catch { /* old Node */ }
const th = C && H ? test : test.skip;

th('Electromagnetic context: modes and chips, no numbers, error digits masked', () => {
  const ctx = H.simulationHelp({ motorName: 'M1', opMode: 'motor', drive: 'current', targetKind: 'nm', connection: '4S', coupled: true, coupledDrive: 'inverter',
    solveTo: 'limits', running: false, stopped: false, failed: true, ran: true, notice: 'speed 12500 rpm is outside 0..9000' });
  assert.deepEqual(ctx, { tab: 'simulation', motor: 'M1', opMode: 'motor', input: 'target_torque', connection: '4S', coupled: true, coupledDrive: 'inverter',
    solveTo: 'limits', calculated: true, status: ['error'], error: 'speed # rpm is outside #..#' });
  const idle = H.simulationHelp({ opMode: 'generator', drive: 'current', targetKind: 'off', connection: '2S', coupled: false, coupledDrive: 'sine', solveTo: 'steady',
    running: false, stopped: false, failed: false, ran: false });
  assert.equal(idle.input, 'current_drive'); assert.deepEqual(idle.status, ['not_run']); assert.ok(!('solveTo' in idle) && !('motor' in idle));
});

th('Thermal and Mechanical contexts carry modes and flags only', () => {
  const t = H.thermalHelp({ motorName: 'M1', coolMode: 'robotics', boreMode: 'still', outerSource: 'manual', busy: false, ran: true, stale: true, emMissing: false, compareRows: 3, issue: null });
  assert.deepEqual(t, { tab: 'thermal', motor: 'M1', coolMode: 'robotics', boreMode: 'still', outerSource: 'manual', calculated: true, stale: true, emMissing: false, compareRows: 3, status: ['done'] });
  const m = H.mechanicalHelp({ loads: 'both', symmetry: 'sector', busy: true, ran: false, stale: false, error: 'rpm 99999 too high' });
  assert.deepEqual(m, { tab: 'mechanical', loads: 'both', symmetry: 'sector', calculated: false, stale: false, status: ['solving'], error: 'rpm # too high' });
});

th('every Simulations tab declared by the web is in the shared registry with EN and zh-CN documents', () => {
  const reg = JSON.parse(readFileSync(`${DOCS}registry.json`, 'utf8'));
  for (const id of ['simulation', 'thermal', 'mechanical']) {
    assert.ok(reg[id], `${id} registered`);
    assert.ok(reg[id].docs.includes(id));
    for (const lg of ['en', 'zh-CN']) assert.ok(existsSync(`${DOCS}${id}.${lg}.md`), `${id}.${lg}.md`);
  }
});

th('Propellers and Schemas contexts: ids, names, flags and counts only', () => {
  assert.deepEqual(H.propellersHelp({ view: 'catalog', rows: 12, error: 'HTTP 500' }), { tab: 'propellers', sub: 'catalog', rows: 12, error: 'HTTP #' });
  const d = H.designerHelp({ polar: 'mfoil_table', ran: true, busy: false, runs: 4, drafts: 1, optimised: true, error: 'The design is not valid: root_chord 3' });
  assert.deepEqual(d, { tab: 'propellers', sub: 'designer', polar: 'mfoil_table', calculated: true, runs: 4, drafts: 1, optimised: true, status: ['done'], error: 'The design is not valid: root_chord #' });
  const s = H.schemasHelp({ mode: 'view', graphView: 'propeller_s1', showAll: false, die: 'D1', configuration: 'cfg', duty: '', loading: false, error: null });
  assert.deepEqual(s, { tab: 'schemas', sub: 'view', graphView: 'propeller_s1', showAll: false, die: 'D1', configuration: 'cfg', status: ['shown'] });
  assert.deepEqual(H.schemasHelp({ mode: 'compose', graphView: 'modules', showAll: false, loading: false }), { tab: 'schemas', sub: 'compose' });
  assert.deepEqual(H.composerHelp({ graph: 'My S1', template: false, running: true, error: null }), { tab: 'schemas', sub: 'compose', graph: 'My S1', template: false, status: ['running'] });
});

th('every web help tab is in the registry, with its sub-views, EN and zh-CN documents', () => {
  const reg = JSON.parse(readFileSync(`${DOCS}registry.json`, 'utf8'));
  for (const id of H.HELP_TABS) {
    assert.ok(reg[id], `${id} registered`);
    for (const d of reg[id].docs) for (const lg of ['en', 'zh-CN']) assert.ok(existsSync(`${DOCS}${d}.${lg}.md`), `${d}.${lg}.md`);
    for (const sub of reg[id].subs) assert.ok(reg[id].docs.includes(`${id}.${sub}`), `${id}.${sub} has a document`);
  }
});
