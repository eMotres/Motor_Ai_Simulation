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
