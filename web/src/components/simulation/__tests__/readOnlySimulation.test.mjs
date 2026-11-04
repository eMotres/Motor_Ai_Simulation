// node --test - the READ-ONLY Electromagnetic Simulation view that project members get (owner 2026-11-04).
// The pure helpers are imported (lib/simReadOnly.ts, lib/simPoints.ts; Node >= 22.18 / 24); the guarantees that cannot be
// type-checked - "this view never reaches the workbench or a solver" - are pinned on the sources.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { pointsOf, isStale, switcherLayout } from '../../../lib/simPoints.ts';
import { canOpenReadOnlySimulation, driveFor, initialPoint, runMetasOf } from '../../../lib/simReadOnly.ts';

const read = (rel) => readFileSync(new URL(rel, import.meta.url), 'utf8');
const run = (drive, o = {}) => ({ drive, has_payload: true, ...o });
const duty = (name, o = {}) => ({
  name, mode: 'motor', current_arms: 240, rpm: 3000, gamma_deg: 0, torque_nm: 95.5, power_kw: 30,
  primary_drive: 'current', runs: [run('pwm_voltage'), run('current', { primary: true })],
  result: { efficiency_pct: 94.1, v_ll_peak_v: 480 }, stale: false, ...o,
});
const cfg = (duties, o = {}) => ({ die: 'D', config: 'L40', phases: 3, default_duty: 'rated', duties, ...o });

test('the server answer feeds the SAME point logic as the live tab (order, default star, stale)', () => {
  const c = cfg([duty('30 kW'), duty('peak'), duty('rated', { stale: true })]);
  const p = pointsOf(c, false);
  assert.deepEqual(p.map((x) => x.name), ['rated', 'peak', '30 kW']);
  assert.deepEqual(p.filter((x) => x.isDefault).map((x) => x.name), ['rated']);
  // the member view carries a stale FLAG (the server sends no signatures); the tree path still compares signatures
  assert.equal(p.find((x) => x.name === 'rated').stale, true);
  assert.equal(isStale(duty('a'), { name: 'c', duties: [] }), false);
  assert.equal(isStale(duty('a', { result: { build_sig: 'OLD' } }), { name: 'c', build_sig: 'NEW', duties: [] }), true);
});

test('a member never gets the editing controls; chips need two points, like the live tab for a viewer', () => {
  const one = pointsOf(cfg([duty('rated')]), false);
  assert.deepEqual(switcherLayout(one, false, 'rated'), { chips: false, controls: false });
  const two = pointsOf(cfg([duty('rated'), duty('peak')]), false);
  assert.deepEqual(switcherLayout(two, false, 'rated'), { chips: true, controls: false });
});

test('test / validation points are not offered to a viewer (also stripped server-side)', () => {
  const p = pointsOf(cfg([duty('rated'), duty('noload_3021', { kind: 'test' })]), false);
  assert.deepEqual(p.map((x) => x.name), ['rated']);
});

test('run chips: primary excitation first; the shown one survives a point change when the new point has it', () => {
  const d = duty('rated');
  assert.deepEqual(runMetasOf(d).map((r) => r.drive), ['current', 'pwm_voltage']);
  assert.equal(driveFor(d, 'pwm_voltage'), 'pwm_voltage');
  assert.equal(driveFor(d, 'bldc_current'), 'current');
  assert.equal(driveFor(d, null), 'current');
  assert.equal(driveFor(duty('x', { runs: [] }), 'current'), null);
  assert.deepEqual(runMetasOf(null), []);
});

test('first point: the asked one, else the default, else the first', () => {
  const c = cfg([duty('a'), duty('rated'), duty('peak')]);
  assert.equal(initialPoint(c, 'peak'), 'peak');
  assert.equal(initialPoint(c, 'gone'), 'rated');
  assert.equal(initialPoint(c, null), 'rated');
  assert.equal(initialPoint(cfg([duty('a'), duty('b')], { default_duty: null }), null), 'a');
  assert.equal(initialPoint(cfg([]), 'x'), null);
  assert.equal(initialPoint(null, 'x'), null);
});

test('the door: members with a stored point; staff keep the live tab', () => {
  assert.equal(canOpenReadOnlySimulation({ staff: false, hasPoint: true }), true);
  assert.equal(canOpenReadOnlySimulation({ staff: false, hasPoint: false }), false);
  assert.equal(canOpenReadOnlySimulation({ staff: true, hasPoint: true }), false);
});

// ---- sources: the view reaches neither the workbench nor a solver -----------------------------------------------------

test('ReadOnlySimulationView: only the two read routes; no workbench store, localStorage, event or write helper', () => {
  const src = read('../ReadOnlySimulationView.tsx');
  const code = src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
  for (const bad of ['applyDutyEverywhere', 'applyStoredRun', 'showStoredRun', 'applyCatalogDuty', 'dutyOps', 'getSavers',
    'localStorage', 'sessionStorage', 'dispatchEvent', 'useMotorStore', 'useUIStore', 'family/activate', 'fetch(',
    'setDutyRuns', 'setPickedRun', 'fetchFamilyTree', 'runCoupled', '/api/kernel', '/api/simulation', '/api/thermal',
    '/api/mechanical', '/api/coupled']) {
    assert.ok(!code.includes(bad), `the read-only view must not use ${bad}`);
  }
  assert.match(code, /developmentApi\.motorSimPoints\(/);
  assert.match(code, /developmentApi\.motorSimRun\(/);
  // no Run / Save / Recompute / rename / delete / new-point control
  assert.doesNotMatch(code, /Run Simulation|Recompute|Save|Rename|Delete|Duplicate|New point|Refine/);
  assert.match(code, /<SummaryTable[^>]*\breadOnly\b/);
  assert.match(code, /<TransientCharts\s+readOnly\b/);
});

test('the project API twin is GET-only', () => {
  const api = read('../../../modules/development/api.ts');
  for (const name of ['motorSimPoints', 'motorSimRun']) {
    const line = api.split('\n').find((l, i, a) => a[i - 1]?.includes(name) || l.includes(`${name}:`))
      ?? '';
    assert.ok(api.includes(`${name}:`), name);
    const at = api.indexOf(`${name}:`);
    assert.match(api.slice(at, at + 300), /call<[A-Za-z]+>\('GET'/, `${name} must be a GET`);
  }
});

test('TransientCharts read-only: every effect that fetches, listens to the workbench or restores is switched off', () => {
  const src = read('../TransientCharts.tsx');
  assert.match(src, /readOnly\?: boolean;/);
  // progress poll, stop, refine event, mount restore, stale re-check, cross-tab storage, same-tab restore, run notice
  const markers = [
    /useEffect\(\(\) => \{\s*if \(readOnly\) return;\s*\/\/ a read-only view never asks the solver anything/,
    /useEffect\(\(\) => \{\s*if \(readOnly\) return;\s*const onStop/,
    /useEffect\(\(\) => \{\s*if \(readOnly\) return;\s*const on = \(\) => \{ void refineRef/,
    /if \(readOnly\) return;\s*\/\/ the stored run arrives as a prop/,
    /useEffect\(\(\) => \{\s*if \(readOnly\) return;\s*const restored/,
    /useEffect\(\(\) => \{\s*if \(readOnly\) return;\s*const onStorage/,
    /useEffect\(\(\) => \{\s*if \(readOnly\) return;\s*const onRestored/,
    /if \(readOnly\) return;\s*\/\/ not the workbench's run rail/,
  ];
  for (const m of markers) assert.match(src, m, String(m));
  // controls: history popovers and both Recompute links are not rendered
  assert.match(src, /\{!readOnly && <Box sx=\{\{ display: 'flex', alignItems: 'center', gap: 0\.25 \}\}>\s*<HistoryPopover/);
  assert.match(src, /\{!readOnly && !geoStale && historyNoticeFor\(data\)/);
  assert.match(src, /\{!readOnly && coupledHistory && !busy/);
  assert.match(src, /const geoStale = !readOnly && !!data/);
});

test('SummaryTable read-only: no Compare, no Recompute, no Refine, no bearing fetch, no workbench comparison', () => {
  const src = read('../SummaryTable.tsx');
  assert.match(src, /readOnly\?: boolean;/);
  assert.match(src, /if \(readOnly \|\| !\(sumRpm > 0\) \|\| runCarriesMech\)/);
  assert.match(src, /\{!readOnly && <Box sx=\{\{ display: 'flex', alignItems: 'center', gap: 1, ml: 'auto' \}\}>\s*<AddToCompareButton/);
  assert.match(src, /\{!readOnly && canRefineRipple\(s\)/);
  assert.match(src, /const geoStale = !readOnly && \(/);
  assert.match(src, /const matDiffs: string\[\] = readOnly \? \[\]/);
  assert.match(src, /if \(!readOnly\) try \{\s*const view: any/);
});

test('the live tab keeps its own behaviour: the switcher and the run selector still draw through the shared chips', () => {
  const sw = read('../SimPointSwitcher.tsx');
  assert.match(sw, /<PointChips points=\{points\} loaded=\{d\} busy=\{busy\}/);
  assert.match(sw, /applyDutyEverywhere\(duty\.die, duty\.config, name, canWrite\)/);
  const sel = read('../StoredRunSelector.tsx');
  assert.match(sel, /<RunChips runs=\{runs\} pick=\{pick\} busy=\{busy\}/);
  assert.match(sel, /await showStoredRun\(duty, drive\)/);
});

test('the project motor row offers the view only to non-staff, with a stored point', () => {
  const src = read('../../../modules/development/MotorOpenActions.tsx');
  assert.match(src, /canOpenReadOnlySimulation\(\{ staff: liveSimulation, hasPoint: viewDuty != null \}\)/);
  assert.match(src, /t\('simView\.button'\)/);
});

test('an account that cannot start a calculation gets the view, never the live tab (global engineer included)', () => {
  const src = read('../../../modules/development/MotorOpenActions.tsx');
  // the live doors need staff AND canSolve; the view is for everybody else
  assert.match(src, /const liveSimulation = engineer && canSolve;/);
  assert.match(src, /liveSimulation && <Button[\s\S]*?openSimulation\(availability\.simulationDuty!\)/);
  assert.match(src, /liveSimulation && availability\.loadDuty/);
  assert.doesNotMatch(src, /[?{] engineer &&/);
  const door = (engineer, canSolve, hasPoint) => canOpenReadOnlySimulation({ staff: engineer && canSolve, hasPoint });
  assert.equal(door(true, false, true), true);     // global engineer, setting on: View simulation
  assert.equal(door(true, true, true), false);     // admin / setting off: the live tab's own door
  assert.equal(door(false, true, true), true);     // customer: as before
  assert.equal(door(true, false, false), false);   // no stored point: nothing to view
});
