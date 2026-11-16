// node --test: the Overview tree (modules/overview/overviewModel.ts).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { buildView, flatProjects, rowState } from '../overviewModel.ts';

const ds = (state, at = '') => ({ state, explicit: false, default: state, reason: '', by: '', at });
const answer = {
  items: {
    'DIE A/L12': { die: 'DIE A', config: 'L12', item_id: 'itm_1', kind: 'motor', state: 'present', layers: ['shared'], projects: [{ id: 'hydro', link_status: 'current' }], productions: ['DIE A/L12'], works_with: ['CTRL/R07'], analysis_controllers: [] },
    'DIE A/L20': { die: 'DIE A', config: 'L20', item_id: null, kind: 'motor', state: '', layers: [], projects: [{ id: 'joint', link_status: 'trial' }], productions: [], works_with: [], analysis_controllers: [] },
    'CTRL/R07': { die: 'CTRL', config: 'R07', item_id: null, kind: 'board', state: '', layers: [], projects: [{ id: 'hydro', link_status: 'current' }], productions: ['CTRL/R07'], works_with: ['DIE A/L12'], analysis_controllers: [] },
    'GONE/X': { die: 'GONE', config: 'X', item_id: null, kind: 'motor', state: '', layers: [], projects: [{ id: 'old', link_status: 'current' }], productions: [], works_with: [], analysis_controllers: [] },
  },
  projects: {
    hydro: { id: 'hydro', title: 'Hydro', customer_org: 'O', status: 'design', updated_at: '2026-11-10', motors: ['DIE A/L12', 'CTRL/R07'], display: ds('active') },
    joint: { id: 'joint', title: 'Joint', customer_org: 'O', status: 'closed', updated_at: '2026-11-12', motors: ['DIE A/L20'], display: ds('archived') },
    old: { id: 'old', title: 'Old', customer_org: 'O', status: 'design', updated_at: '2026-10-01', motors: ['GONE/X'], display: ds('parked') },
    fresh: { id: 'fresh', title: 'Fresh', customer_org: 'O', status: 'spec', updated_at: '2026-11-15', motors: [], display: ds('active') },
  },
  productions: {
    'DIE A/L12': { die: 'DIE A', config: 'L12', title: 'x', status: 'active', current_revision: '1.2', order_quantity: 40, product_type: 'motor', updated_at: '', display: ds('active') },
    'CTRL/R07': { die: 'CTRL', config: 'R07', title: 'y', status: 'paused', current_revision: '1.0', order_quantity: 5, product_type: 'electronics', updated_at: '', display: ds('parked') },
  },
  no_motor_yet: ['fresh'], review: [], registry: null,
};
const tree = [{ name: 'DIE A', slots: 24, poles: 28, stator_diameter: 80, configs: [{ name: 'L12' }, { name: 'L20' }] },
  { name: 'DIE Z', slots: 12, poles: 14, stator_diameter: 40, configs: [{ name: 'L10' }] }];

test('a row is as current as its most current project or production', () => {
  assert.equal(rowState([{ display: ds('archived') }], [{ display: ds('active') }]), 'active');
  assert.equal(rowState([{ display: ds('archived') }], [{ display: ds('parked') }]), 'parked');
  assert.equal(rowState([], []), 'active');
});

test('the tree: dies with their motors, boards apart, catalog-only and no-motor groups', () => {
  const v = buildView(answer, tree, 'all');
  assert.deepEqual(v.motors.map((f) => [f.die, f.rows.map((r) => r.item.config), f.inCatalog]), [['DIE A', ['L12', 'L20'], true], ['GONE', ['X'], false]]);
  assert.deepEqual(v.controllers.map((f) => f.die), ['CTRL']);
  assert.deepEqual(v.catalogOnly.map((d) => d.name), ['DIE Z']);
  assert.deepEqual(v.noMotorYet.map((p) => p.id), ['fresh']);
  assert.deepEqual(v.counts, { active: 2, parked: 1, archived: 1, all: 4 });
  const a12 = v.motors[0].rows[0];
  assert.equal(a12.projects[0].title, 'Hydro');
  assert.equal(a12.productions[0].current_revision, '1.2');
  assert.equal(a12.state, 'active');
});

test('the filter hides rows and empty families; counts stay over all rows', () => {
  const v = buildView(answer, tree, 'active');
  assert.deepEqual(v.motors.map((f) => [f.die, f.rows.map((r) => r.item.config)]), [['DIE A', ['L12']]]);
  assert.deepEqual(v.controllers.map((f) => f.die), ['CTRL']);       // its project is active
  assert.equal(v.counts.all, 4);
  assert.deepEqual(buildView(answer, tree, 'archived').motors.map((f) => f.rows.map((r) => r.item.config)), [['L20']]);
});

test('the flat projects tab: newest first, filtered by its own state', () => {
  assert.deepEqual(flatProjects(answer, 'active').map((p) => p.id), ['fresh', 'hydro']);
  assert.deepEqual(flatProjects(answer, 'all').map((p) => p.id), ['fresh', 'joint', 'hydro', 'old']);
});
