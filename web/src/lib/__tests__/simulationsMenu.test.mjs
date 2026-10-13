// node --test - the Simulations dropdown (owner 2026-10-13): Electromagnetic, Thermal and
// Mechanical share one tab; Geometry, Mesh and Controller stay separate; gates are kept.
// Imports lib/simulationsMenu.ts + lib/accessUi.ts (Node >= 22.18 / 24; else skipped).
import test from 'node:test';
import assert from 'node:assert/strict';

let M = null; let A = null;
try { M = await import('../simulationsMenu.ts'); A = await import('../accessUi.ts'); } catch { /* old Node */ }
const t = M ? test : test.skip;

// The registry of App.tsx: id, order, gate.
const REG = [
  ['motors', 0, 'always'], ['geometry', 20, 'fullUI'], ['materials', 30, 'fullUI'],
  ['mesh', 40, 'fullUI'], ['simulation', 50, 'fullUI'], ['static3d', 55, 'admin'],
  ['mechanical', 57, 'admin'], ['thermal', 58, 'admin'], ['controller', 59, 'admin'],
  ['sweep', 60, 'admin'], ['comparePoints', 65, 'fullUI'], ['cost', 70, 'admin'],
  ['compare', 75, 'signedIn'], ['admin', 90, 'admin']]
  .map(([id, order, gate]) => ({ id, order, gate, label: id }));

// What App.tsx does: gate by role, then the standard-user restriction.
const visible = (role) => REG.filter((t) =>
  (role === 'user' ? A.tabAllowed(t.id, true) : true)
  && (t.gate === 'always' || t.gate === 'signedIn'
      || (t.gate === 'fullUI' && role !== 'user')
      || (t.gate === 'admin' && role === 'admin')));

const bar = (role) => M.barEntries(visible(role)).map((e) =>
  e.kind === 'tab' ? e.id : `sim[${e.members.map((m) => m.id).join(',')}]`);

t('the dropdown lists exactly Electromagnetic, Thermal, Mechanical in that order', () => {
  assert.deepEqual([...M.SIMULATION_TAB_IDS], ['simulation', 'thermal', 'mechanical']);
});

t('admin: one Simulations tab at the place of Electromagnetic; Geometry, Mesh, Controller stay', () => {
  assert.deepEqual(bar('admin'), ['motors', 'geometry', 'materials', 'mesh', 'sim[simulation,thermal,mechanical]',
    'static3d', 'controller', 'sweep', 'comparePoints', 'cost', 'compare', 'admin']);
});

t('engineer (full UI, not admin): only the gated member is in the menu', () => {
  const b = bar('engineer');
  assert.ok(b.includes('sim[simulation]'));
  assert.ok(!b.includes('controller') && !b.includes('admin'));
  assert.ok(b.includes('geometry') && b.includes('mesh'));
});

t('standard user: Motors + Configure only, no Simulations tab', () => {
  assert.deepEqual(bar('user'), ['motors', 'compare']);
});

t('a menu with no visible member produces no entry', () => {
  assert.deepEqual(M.barEntries([{ id: 'motors', label: 'Motors', order: 0 }]).map((e) => e.id), ['motors']);
});

t('the bar value and the label follow the open panel', () => {
  assert.equal(M.barValue('thermal'), 'simulations');
  assert.equal(M.barValue('mesh'), 'mesh');
  const members = [{ id: 'simulation', label: 'Electromagnetic' }, { id: 'thermal', label: 'Thermal' }];
  assert.equal(M.simulationsLabel('Simulations', 'simulation', members), 'Simulations · Electromagnetic');
  assert.equal(M.simulationsLabel('Simulations', 'mesh', members), 'Simulations');
});
