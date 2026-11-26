// node --test - the window context of the help assistant (lib/supportContext.ts, modules/gears/helpContext.ts).
// Pins: default context = tab id only; registered contexts merge and unregister; another tab's entry is ignored; buildSupportContext
// carries ui_context; a visitor sends the language and the tab / sub ids ONLY (never the snapshot); the Gears context comes from state
// and carries no name, no free text and no number; every id the web declares has an EN and a zh-CN help document.
// Needs a Node that strips TypeScript (>= 22.18); on an older Node the import error is thrown, not skipped, when HELP_STRICT=1.
import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

let C = null, G = null, importError = null;
try { C = await import('../supportContext.ts'); G = await import('../../modules/gears/helpContext.ts'); } catch (e) { importError = e; }
if (importError && process.env.HELP_STRICT === '1') throw importError;
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
  C.setHelpContext('a', { tab: 'gears', sub: 'motor', preset: 'standard' });
  C.setHelpContext('b', { tab: 'gears', source: 'inline', status: [] });
  C.setHelpContext('c', { tab: 'other', calculated: true });
  assert.deepEqual(C.buildSupportContext({ ...base, tab: 'gears' }).ui_context, { tab: 'gears', sub: 'motor', preset: 'standard', source: 'inline' });
  C.setHelpContext('a', null); C.setHelpContext('b', null);
  assert.deepEqual(C.buildSupportContext({ ...base, tab: 'gears' }).ui_context, { tab: 'gears' });
  C.clearHelpContexts();
});

t('a visitor sends the language and the tab / sub ids only, never the snapshot', () => {
  C.clearHelpContexts();
  C.setConfigureSnapshot({ machine: { name: 'M1' }, configure: { tiles: { torque: 1.5 } } });
  C.setHelpContext('a', { tab: 'compare', sub: 'point', calculated: true, error: 'invalid_field' });
  const anon = C.buildAnonContext({ tab: 'compare', lang: 'zh-CN' });
  assert.deepEqual(anon, { lang: 'zh-CN', ui_context: { tab: 'compare', sub: 'point' } });
  assert.deepEqual(Object.keys(anon).sort(), ['lang', 'ui_context']);
  const signed = C.buildSupportContext({ ...base, tab: 'compare' });
  assert.ok(signed.configure && signed.machine);                    // the signed-in snapshot is a different path
  C.setConfigureSnapshot(null); C.clearHelpContexts();
});

const noNumbers = (o) => JSON.stringify(o, (k, v) => { if (typeof v === 'number' && k !== 'stageCount' && k !== 'rows') assert.fail(`number at ${k}`); return v; });

t('the Gears page context comes from the state: lists, flags and codes only', () => {
  const ctx = G.gearsPageHelp({
    sub: 'spectrum', preset: 'humanoid_robot_joint', stageKinds: ['planetary', 'planetary'],
    ran: { response: { result: { state: 'estimated', passed: false, overall_ratio: 12.884, mass_kg: 3.2 } } }, stale: true,
    errors: [{ path: '/spectrum/bins/0/hours', message: 'must be above 0.5' }], failure: null, recordOpen: false,
  });
  assert.deepEqual(ctx, {
    tab: 'gears', sub: 'spectrum', preset: 'humanoid_robot_joint', stageKinds: ['planetary', 'planetary'], stageCount: 2, record: false,
    calculated: true, stale: true, status: ['state_estimated', 'not_passed', 'inputs_changed'], error: 'invalid_field',
  });
  noNumbers(ctx);
  assert.ok(!JSON.stringify(ctx).includes('12.884') && !JSON.stringify(ctx).includes('hours'));
});

t('a user-typed preset or state is not forwarded; a failure becomes a code', () => {
  const ctx = G.gearsPageHelp({ sub: 'point', preset: 'price EUR 12345', stageKinds: ['pair', 'rotor 125 mm'], ran: { response: { result: { state: 'efficiency 98.7%' } } },
    stale: false, errors: [], failure: 'password is hunter2', recordOpen: true });
  assert.deepEqual(ctx, { tab: 'gears', sub: 'point', stageKinds: ['pair'], stageCount: 2, record: true, calculated: true, stale: false, status: [], error: 'request_failed' });
});

t('the motor sub-tab leaves result fields to its panel; the panel forwards no motor or configuration name', () => {
  const motor = G.gearsPageHelp({ sub: 'motor', preset: 'standard', stageKinds: ['pair'], ran: null, stale: false, errors: [], failure: 'x', recordOpen: false });
  assert.ok(!('calculated' in motor) && !('error' in motor) && !('status' in motor));
  const ctx = G.motorPanelHelp({ source: 'record', rows: 2, statuses: ['ok', 'missing', 'ok', 'junk 12'], errors: [], failure: null });
  assert.deepEqual(ctx, { tab: 'gears', sub: 'motor', source: 'record', rows: 2, calculated: true, status: ['row_ok', 'row_missing'] });
  assert.equal(G.motorPanelHelp({ source: 'inline', rows: 1, statuses: null, errors: [{ path: '/x', message: 'm' }], failure: null }).error, 'invalid_field');
});

t('every id the web declares is in the shared registry with its documents, and the registry allowlists match the web lists', () => {
  const reg = JSON.parse(readFileSync(`${DOCS}registry.json`, 'utf8'));
  const entry = reg[G.GEARS_HELP_TAB];
  assert.ok(entry, 'gears is registered');
  assert.deepEqual(entry.subs, [...G.GEARS_HELP_SUBS]);
  const ids = [G.GEARS_HELP_TAB, ...G.GEARS_HELP_SUBS.map((s) => `${G.GEARS_HELP_TAB}.${s}`), 'gears.saved'];
  for (const id of ids) {
    assert.ok(entry.docs.includes(id), `${id} listed`);
    for (const lg of ['en', 'zh-CN']) assert.ok(existsSync(`${DOCS}${id}.${lg}.md`), `${id}.${lg}.md exists`);
  }
  assert.deepEqual(entry.keys.stageKinds.enums, ['planetary', 'compound_planetary', 'pair']);
});

// ── the Simulations pages (lib/helpContexts.ts) ──────────────────────────────
let H = null;
try { H = await import('../helpContexts.ts'); } catch (e) { if (process.env.HELP_STRICT === '1') throw e; }
const th = C && H ? test : test.skip;

th('Electromagnetic context: lists, flags and codes only; a typed value is not forwarded', () => {
  const ctx = H.simulationHelp({ opMode: 'motor', drive: 'current', targetKind: 'nm', coupled: true, coupledDrive: 'inverter',
    solveTo: 'limits', running: false, stopped: false, failed: true, ran: true, hasNotice: true });
  assert.deepEqual(ctx, { tab: 'simulation', opMode: 'motor', input: 'target_torque', coupled: true, coupledDrive: 'inverter',
    solveTo: 'limits', calculated: true, status: ['error'], error: 'run_failed' });
  const hostile = H.simulationHelp({ opMode: 'rpm 9000', drive: 'pwm_voltage', targetKind: 'off', coupled: true, coupledDrive: 'price EUR 12', solveTo: 'x',
    running: true, stopped: false, failed: false, ran: false, hasNotice: false });
  assert.deepEqual(hostile, { tab: 'simulation', input: 'other_drive', coupled: true, calculated: false, status: ['running'] });
  const idle = H.simulationHelp({ opMode: 'generator', drive: 'current', targetKind: 'off', coupled: false, coupledDrive: 'sine', solveTo: 'steady',
    running: false, stopped: false, failed: false, ran: false, hasNotice: false });
  assert.equal(idle.input, 'sine_current'); assert.deepEqual(idle.status, ['not_run']); assert.ok(!('solveTo' in idle) && !('motor' in idle));
});

th('Thermal and Mechanical contexts carry listed modes and flags only', () => {
  const t = H.thermalHelp({ coolMode: 'robotics', boreMode: 'still', outerSource: 'manual', busy: false, ran: true, stale: true, emMissing: false, compareRows: 3, invalid: false });
  assert.deepEqual(t, { tab: 'thermal', coolMode: 'robotics', boreMode: 'still', outerSource: 'manual', calculated: true, stale: true, emMissing: false, compareRows: 3, status: ['done'] });
  assert.ok(!('coolMode' in H.thermalHelp({ coolMode: 'liquid 12 L/min', boreMode: 'x', outerSource: 'y', busy: true, ran: false, stale: false, emMissing: true, compareRows: 0, invalid: true })));
  const m = H.mechanicalHelp({ loads: 'both', symmetry: 'sector', busy: true, ran: false, stale: false, failed: true });
  assert.deepEqual(m, { tab: 'mechanical', loads: 'both', symmetry: 'sector', calculated: false, stale: false, status: ['solving'], error: 'run_failed' });
});

th('every Simulations tab declared by the web is in the registry, with documents and the same allowed values', () => {
  const reg = JSON.parse(readFileSync(`${DOCS}registry.json`, 'utf8'));
  for (const id of H.HELP_TABS) {
    assert.ok(reg[id], `${id} registered`);
    for (const d of reg[id].docs) for (const lg of ['en', 'zh-CN']) assert.ok(existsSync(`${DOCS}${d}.${lg}.md`), `${d}.${lg}.md`);
    for (const sub of reg[id].subs) assert.ok(reg[id].docs.includes(`${id}.${sub}`));
  }
  assert.deepEqual(reg.simulation.keys.opMode.enum, [...H.SIM_OP_MODES]);
  assert.deepEqual(reg.simulation.keys.coupledDrive.enum, [...H.SIM_COUPLED_DRIVES]);
  assert.deepEqual(reg.simulation.keys.solveTo.enum, [...H.SIM_SOLVE_TO]);
  assert.deepEqual(reg.thermal.keys.coolMode.enum, [...H.COOL_MODES]);
  assert.deepEqual(reg.thermal.keys.boreMode.enum, [...H.BORE_MODES]);
  assert.deepEqual(reg.thermal.keys.outerSource.enum, [...H.OUTER_SOURCES]);
  assert.deepEqual(reg.mechanical.keys.loads.enum, [...H.LOADS]);
  assert.deepEqual(reg.mechanical.keys.symmetry.enum, [...H.SYMMETRIES]);
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
