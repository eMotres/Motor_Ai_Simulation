// node --test - what a loop group and a limit check say about themselves (the fields come from the backend, read
// from the code; the words from i18n).  A stub `t` returns "key|params" so the test sees exactly which text and which
// numbers the view would print.  Fixtures = GET /api/system/graph output.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

let V = null; let L = null;
try { V = await import('../systemGraphView.ts'); L = await import('../systemGraphLayout.ts'); } catch { /* old Node */ }
const t = V ? test : test.skip;
const fx = (n) => JSON.parse(fs.readFileSync(new URL(`./fixtures/${n}.json`, import.meta.url), 'utf8'));
const stub = (key, p = {}) => { const { defaultValue, ...rest } = p; return `${key}|${JSON.stringify(rest)}`; };
const loop = (d, id) => d.loops.find((l) => l.id === id);

t('the fixed-point loop states what iterates, the stop condition, the limit, the damping and the fallback', () => {
  const l = loop(fx('system_graph_propeller_s1'), 'thermal_fixed_point');
  const x = V.loopText(l, stub);
  assert.match(x.title, /^system\.loops\.thermal_fixed_point/);
  assert.match(x.rule, /system\.rule\.stopMaxDelta\|\{"value":"0\.01","unit":"K"\}/);
  assert.match(x.rule, /system\.rule\.limit\|\{"n":400\}/);
  assert.match(x.rule, /system\.rule\.damping\|\{"start":"1"\}/);
  assert.match(x.rule, /system\.rule\.fallback\|\{"damping":"0\.2","max":1200\}/);
  assert.equal(x.last, '');                                       // no stored run: just the rule
  assert.equal(x.lines, 2);
  assert.equal(x.tip.length, 1);
  assert.equal(V.loopHead(x.lines) > V.loopHead(1), true);
});

t('the speed search says how it scans and bisects and when it stops', () => {
  const x = V.loopText(loop(fx('system_graph_propeller_s1'), 'speed_search'), stub);
  assert.match(x.steps, /system\.rule\.steps\|\{"n":41,"tol":"1","unit":"rpm"\}/);
  assert.match(x.rule, /system\.rule\.stopAllPass/);
  assert.equal(x.lines, 3);
});

t('a stored run shows the actual iteration count and the final residual next to the rule', () => {
  const l = { ...loop(fx('system_graph_modules'), 'em_thermal_orchestrator'), convergence: { iterations: 3, max_iterations: 6, converged: true, residual: 0.9, unit: 'K' } };
  const x = V.loopText(l, stub);
  assert.match(x.last, /system\.rule\.lastConverged\|\{"n":3,"max":6\}/);
  assert.match(x.last, /system\.rule\.residual\|\{"res":"0\.9","unit":"K"\}/);
  assert.equal(x.lines, 3);
  const bad = V.loopText({ ...l, convergence: { iterations: 6, max_iterations: 6, converged: false, residual: 4.2, unit: 'K' } }, stub);
  assert.match(bad.last, /system\.rule\.lastNot/);
  assert.equal(V.loopText(loop(fx('system_graph_modules'), 'em_thermal_orchestrator'), stub).last, '');
});

t('the coupling loops of the modules view carry their limit, tolerance and relaxation', () => {
  const d = fx('system_graph_modules');
  const m = V.loopText(loop(d, 'em_thermal_module'), stub);
  assert.match(m.rule, /"value":"0\.5","unit":"K"/);
  assert.match(m.rule, /"n":12/);
  assert.match(m.rule, /"start":"0\.6"/);
  const o = V.loopText(loop(d, 'em_thermal_orchestrator'), stub);
  assert.match(o.rule, /"value":"2","unit":"K"/);
  assert.match(o.rule, /"n":6/);
  assert.equal(o.tip.length, 2);                                  // the long text + the bearing / duty-ratio tolerances
});

t('exits: the way on, the flagged ones, and one tag for everything that fails the point', () => {
  const d = fx('system_graph_propeller_s1');
  const g = V.exitGroups(loop(d, 'thermal_fixed_point'));
  assert.deepEqual(g.map((x) => x.tone), ['ok', 'fail']);
  assert.deepEqual(g[0].exits.map((e) => e.id), ['converged']);
  assert.deepEqual(g[1].exits.map((e) => e.id).sort(), ['loss_data', 'no_current', 'nonconverged', 'runaway']);
  assert.equal(g[1].exits.find((e) => e.id === 'runaway').param.above_C, 600);
  assert.deepEqual(V.exitGroups(loop(d, 'speed_search')).map((x) => x.tone), ['ok', 'warn']);
  const m = fx('system_graph_modules');
  assert.deepEqual(V.exitGroups(loop(m, 'em_thermal_orchestrator')).map((x) => x.tone), ['ok', 'warn', 'fail']);
  assert.deepEqual(V.exitGroups(loop(m, 'design_search')), []);
});

t('each limit check states its condition in one line', () => {
  const d = fx('system_graph_propeller_s1');
  const c = (id) => V.conditionText(d.nodes.find((n) => n.id === id), stub);
  assert.match(c('chk_winding'), /system\.cond\.windingLimit\|\{"limit":200,"basis":"system\.basis\.project_class\|\{\}"\}/);
  assert.equal(c('chk_magnet'), 'system.cond.magnet|{}');
  assert.equal(c('chk_demag'), 'system.cond.demag|{}');
  assert.equal(c('chk_controller'), 'system.cond.controller|{}');
  assert.equal(c('chk_battery'), 'system.cond.battery|{}');
  // with the selected motor's model the magnet limit is a number
  const n = { id: 'chk_magnet', condition: { key: 'magnet', params: { limit_C: 150 } } };
  assert.equal(V.conditionText(n, stub), 'system.cond.magnetLimit|{"limit":150,"basis":""}');
  assert.equal(V.conditionText({ id: 'x' }, stub), '');
});

const lt = L ? test : test.skip;
lt('a block with a condition line is taller; a loop box leaves room for its header text', () => {
  assert.equal(L.blockHeight({ condition: { key: 'a', params: {} } }, 0), L.NODE_H + L.COND_ROW);
  assert.equal(L.blockHeight({ context: true, condition: null }, 3), L.COMPACT_H);
  const d = fx('system_graph_propeller_s1');
  const heights = {}; const heads = {};
  for (const n of d.nodes) heights[n.id] = L.blockHeight(n, 0);
  for (const l of d.loops) heads[l.id] = V.loopHead(V.loopText(l, stub).lines);
  const a = L.layoutGraph(d, { heights, lanes: {}, heads: {} });
  const b = L.layoutGraph(d, { heights, lanes: {}, heads });
  for (const l of d.loops) assert.ok(b.loops[l.id].h > a.loops[l.id].h, `${l.id} grows with its header`);
});
