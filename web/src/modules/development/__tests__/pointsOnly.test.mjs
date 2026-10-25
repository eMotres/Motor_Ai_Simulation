import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  POINT_TILES, baseVariantPoint, busHeadroom, dutyAfterVariantSwitch, findVariantIndex, finite,
  pointOperating, tileValue, visibleTiles,
} from '../pointsOnly.ts';

const point = (duty, comparison = {}, operating_point = {}) => ({ duty, has_result: true, comparison, operating_point });
const variants = [
  { step: 1, die: 'D', canonical_die: 'D', config: 'opt1', status: 'current', note: 'baseline', points: [point('takeoff', { torque_nm: 4, efficiency_pct: 90, p_loss_total_w: 200 }), point('cruise', { torque_nm: 1 })] },
  { step: 2, die: 'D', canonical_die: 'D', config: 'opt2', status: 'current', note: '', points: [point('takeoff', { torque_nm: 4.5, efficiency_pct: 92, p_loss_total_w: 150, p_cu_w: 100 })] },
  { step: 3, die: 'D', canonical_die: 'D', config: 'opt3', status: 'trial', note: 'housing 2.1 mm', points: [] },
];

test('missing and non-numeric stored values are null, never zero', () => {
  for (const bad of [null, undefined, '', 'abc', NaN, Infinity, true]) assert.equal(finite(bad), null);
  assert.equal(finite(0), 0);
  assert.equal(finite('2.5'), 2.5);
  const torque = POINT_TILES.find((s) => s.id === 'torque');
  assert.equal(tileValue(point('x', {}), torque), null);
  assert.equal(tileValue(point('x', { torque_nm: null }), torque), null);
  assert.equal(tileValue(null, torque), null);
});

test('tiles are read in their display unit without inventing anything', () => {
  const power = POINT_TILES.find((s) => s.id === 'power');
  assert.equal(tileValue(point('x', { power_w: 1500 }), power), 1.5);
  assert.equal(tileValue(point('x', { power_w: 0 }), power), 0);
});

test('core tiles always show, others only where some step stores them', () => {
  const ids = (list) => list.map((s) => s.id);
  const base = visibleTiles(variants[0].points[0], []);
  assert.ok(ids(base).includes('torque') && ids(base).includes('coilTemp'));   // core, blank when missing
  assert.ok(!ids(base).includes('pCu'));
  const withPeers = visibleTiles(variants[0].points[0], variants.map((v) => v.points[0] ?? null));
  assert.ok(ids(withPeers).includes('pCu'));                                    // stored in step 2: the table lines up
});

test('operating point reads stored speed, current and angle only', () => {
  assert.deepEqual(pointOperating(point('x', {}, { rpm: 3000, current_arms: 40, gamma_deg: 10, torque_nm: 5 })),
    { rpm: 3000, currentA: 40, gammaDeg: 10 });
  assert.deepEqual(pointOperating({ duty: 'x', electromagnetic: { rpm: 2000, i_phase_rms: 12 } }),
    { rpm: 2000, currentA: 12, gammaDeg: null });
  assert.deepEqual(pointOperating(null), { rpm: null, currentA: null, gammaDeg: null });
});

test('bus headroom is arithmetic on the stored requirement only', () => {
  const p = point('x', { v_dc_required_v: 40 });
  assert.deepEqual(busHeadroom(p, 48), { requiredV: 40, headroomPct: 20, ok: true });
  const short = busHeadroom(p, 36);
  assert.equal(short.ok, false);
  assert.ok(Math.abs(short.headroomPct + 10) < 1e-9);
  assert.equal(busHeadroom(point('x', {}), 48), null);       // nothing stored: no verdict is invented
  assert.equal(busHeadroom(p, null), null);
  assert.equal(busHeadroom(p, 0), null);
  assert.equal(busHeadroom(p, -5), null);
});

test('step comparison picks the same mode of the previous or first step', () => {
  assert.equal(baseVariantPoint(variants, 1, 'takeoff', 'previous'), variants[0].points[0]);
  assert.equal(baseVariantPoint(variants, 1, 'takeoff', 'first'), variants[0].points[0]);
  assert.equal(baseVariantPoint(variants, 0, 'takeoff', 'previous'), null);
  assert.equal(baseVariantPoint(variants, 0, 'takeoff', 'first'), null);
  assert.equal(baseVariantPoint(variants, 1, 'takeoff', 'none'), null);
  assert.equal(baseVariantPoint(variants, 1, 'peak', 'previous'), null);     // that step has no such point
  assert.equal(baseVariantPoint(variants, 2, 'takeoff', 'previous'), variants[1].points[0]);
  assert.equal(baseVariantPoint(variants, 9, 'takeoff', 'previous'), null);
});

test('switching step keeps the mode when stored, else the first stored point', () => {
  assert.equal(dutyAfterVariantSwitch(variants[1], 'takeoff'), 'takeoff');
  assert.equal(dutyAfterVariantSwitch(variants[1], 'cruise'), 'takeoff');
  assert.equal(dutyAfterVariantSwitch(variants[2], 'takeoff'), null);
  assert.equal(dutyAfterVariantSwitch(null, 'takeoff'), null);
  assert.equal(dutyAfterVariantSwitch(variants[0], null), 'takeoff');
});

test('a project motor is found by ordinary alias or canonical die, case-insensitively', () => {
  const list = [{ ...variants[0], die: 'Alias', canonical_die: 'Canon' }, variants[1]];
  assert.equal(findVariantIndex(list, 'alias', 'OPT1'), 0);
  assert.equal(findVariantIndex(list, 'canon', 'opt1'), 0);
  assert.equal(findVariantIndex(list, 'D', 'opt2'), 1);
  assert.equal(findVariantIndex(list, 'D', 'zzz'), -1);
  assert.equal(findVariantIndex(list, null, 'opt1'), -1);
});

test('wiring: Configure shows the points card only for a project target, notes are editable by project editors', () => {
  const src = (rel) => readFileSync(new URL(rel, import.meta.url), 'utf8');
  const tab = src('../../../components/compare/CompareTab.tsx');
  assert.match(tab, /pointsTarget \? <PointsConfigure \/> : <ConfiguratorPanel \/>/);
  const store = src('../../../stores/pointsConfigureStore.ts');
  assert.doesNotMatch(store, /zustand\/middleware/);                                       // transient: a reload returns to the ordinary Configure
  const page = src('../components/DevelopmentPage.tsx');
  assert.match(page, /canEditProject && \(r\.motor\.results/);
  assert.match(page, /<MotorOpenActions motor=\{r\.motor\} duty=\{r\.result\.duty\} engineer=\{canManageGlobally\} t=\{tr\} projectId=\{selected\.id\}/);
  const admin = src('../../../components/admin/sections/ProjectsSection.tsx');
  assert.match(admin, /<MotorNoteField value=\{m\.note \?\? ''\}/);
  assert.match(admin, /\/motors\/note', 'PATCH'/);
  const actions = src('../MotorOpenActions.tsx');
  assert.match(actions, /projectId && availability\.pointsDuty/);
  const panel = src('../../../components/compare/PointsConfigure.tsx');
  assert.match(panel, /<Slider disabled/);                                       // continuous knobs stay disabled
});
