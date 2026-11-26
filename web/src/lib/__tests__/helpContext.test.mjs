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
