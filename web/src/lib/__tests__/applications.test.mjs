// node --test - a configuration's APPLICATION (owner 2026-10-13) gates Configure's propeller UI,
// feeds the Motors filter and the Admin "not set" audit.
// Imports lib/applications.ts + configuratorPropeller.ts (Node >= 22.18 / 24; else skipped).
import test from 'node:test';
import assert from 'node:assert/strict';

let A = null; let P = null;
try { A = await import('../applications.ts'); P = await import('../configuratorPropeller.ts'); } catch { /* old Node */ }
const t = A && P ? test : test.skip;

const DRONE = { restricted: true, cooling_options: ['propeller_air'], propellers: ['p1', 'p2'],
                default_propeller: 'p1', defaults: { L12: 'p1' }, die: 'D', config: 'L12' };
const MIXED = { restricted: true, cooling_options: ['robotics', 'propeller_air'], propellers: ['p1'],
                default_propeller: 'p1', defaults: { L13: 'p1' }, die: 'D', config: 'L13' };
const list = [{ id: 'p1', vendor: 'V', model: 'M', selectable: true }, { id: 'p2', vendor: 'V', model: 'N', selectable: true }];

const offers = (c) => P.isPropellerCooled(c) || P.hasPropellerCooling(c);

t('propeller and not-set keep today\'s propeller UI untouched', () => {
  for (const sel of [null, 'propeller']) {
    assert.equal(A.coolingForApplication(DRONE, sel), DRONE);
    assert.equal(P.isPropellerCooled(A.coolingForApplication(DRONE, sel)), true);
    assert.equal(P.hasPropellerCooling(A.coolingForApplication(MIXED, sel)), true);
  }
});

t('a robot shows no propeller UI: no picker, no prop-wash cooling, robotics stays', () => {
  const c = A.coolingForApplication(MIXED, 'robot');
  assert.equal(offers(c), false);
  assert.deepEqual(P.allowedPropellers(c, list), []);
  assert.deepEqual(c.cooling_options, ['robotics']);
  assert.equal(c.default_propeller, null);
  // a drone die handed to a robot: robotics replaces the propeller, the registry object is intact
  assert.deepEqual(A.coolingForApplication(DRONE, 'robot').cooling_options, ['robotics']);
  assert.deepEqual(DRONE.cooling_options, ['propeller_air']);
});

t('aviation and marine: no propeller and no robotics block', () => {
  for (const sel of ['aviation', 'marine']) {
    const c = A.coolingForApplication(DRONE, sel);
    assert.equal(offers(c), false);
    assert.equal(c.cooling_options, null);
    assert.equal(c.restricted, false);
    assert.equal(A.propellerOffered(sel), false);
  }
});

t('selected application: the pick when listed, else the first, else null', () => {
  assert.equal(A.selectedApplication(['robot', 'propeller']), 'robot');
  assert.equal(A.selectedApplication(['robot', 'propeller'], 'propeller'), 'propeller');
  assert.equal(A.selectedApplication(['robot'], 'propeller'), 'robot');
  assert.equal(A.selectedApplication([], 'robot'), null);
  assert.equal(A.selectedApplication(undefined), null);
});

t('the switch appears only with two applications; switching back restores the propeller', () => {
  assert.equal(A.hasApplicationSwitch(['robot']), false);
  assert.equal(A.hasApplicationSwitch([]), false);
  assert.equal(A.hasApplicationSwitch(['robot', 'propeller']), true);
  const apps = ['robot', 'propeller'];
  assert.equal(offers(A.coolingForApplication(MIXED, A.selectedApplication(apps))), false);
  assert.equal(offers(A.coolingForApplication(MIXED, A.selectedApplication(apps, 'propeller'))), true);
});

const dies = [
  { name: 'A', configs: [{ name: 'L1', applications: ['propeller'] }, { name: 'L2', applications: ['robot', 'propeller'] }] },
  { name: 'B', configs: [{ name: 'L3', applications: ['marine'] }, { name: 'L4' }] },
];

t('the catalog filter narrows dies and configurations', () => {
  assert.equal(A.filterDiesByApplication(dies, 'all').length, 2);
  const p = A.filterDiesByApplication(dies, 'propeller');
  assert.deepEqual(p.map((d) => [d.name, d.configs.map((c) => c.name)]), [['A', ['L1', 'L2']]]);
  assert.deepEqual(A.filterDiesByApplication(dies, 'robot')[0].configs.map((c) => c.name), ['L2']);
  assert.deepEqual(A.filterDiesByApplication(dies, 'aviation'), []);
  assert.equal(A.parseApplicationFilter('nonsense'), 'all');
  assert.equal(A.parseApplicationFilter('marine'), 'marine');
});

t('the admin audit flags a missing or malformed list', () => {
  const rows = A.applicationAudit([...dies, { name: 'C', configs: [{ name: 'L5', applications: [], applications_error: 'unknown application' }] }]);
  const by = Object.fromEntries(rows.map((r) => [`${r.die}/${r.config}`, r.state]));
  assert.deepEqual(by, { 'A/L1': 'set', 'A/L2': 'set', 'B/L3': 'set', 'B/L4': 'notSet', 'C/L5': 'invalid' });
});

t('editor state: tick appends, the last one cannot be unticked, default moves to the front', () => {
  assert.deepEqual(A.toggleApplication(['robot'], 'aviation'), ['robot', 'aviation']);
  assert.deepEqual(A.toggleApplication(['robot', 'aviation'], 'robot'), ['aviation']);
  assert.deepEqual(A.toggleApplication(['robot'], 'robot'), ['robot']);
  assert.deepEqual(A.setDefaultApplication(['robot', 'aviation', 'marine'], 'marine'), ['marine', 'robot', 'aviation']);
  assert.deepEqual(A.setDefaultApplication(['robot'], 'marine'), ['robot']);
});

t('editor state: Save needs a real change (content or default), never an empty list', () => {
  assert.equal(A.canSaveApplications(['robot'], ['robot']), false);
  assert.equal(A.canSaveApplications(['robot'], ['aviation']), true);
  assert.equal(A.canSaveApplications(['robot', 'aviation'], ['aviation', 'robot']), true);
  assert.equal(A.canSaveApplications([], ['robot']), true);
  assert.equal(A.canSaveApplications(['robot'], []), false);
});

t('saveApplications PUTs the list and surfaces the server reason on a 422', async () => {
  const calls = [];
  globalThis.fetch = async (url, init) => {
    calls.push([url, init.method, init.body]);
    return JSON.parse(init.body).applications[0] === 'bad'
      ? { ok: false, status: 422, json: async () => ({ detail: 'unknown application' }) }
      : { ok: true, status: 200, json: async () => ({ applications: JSON.parse(init.body).applications }) };
  };
  assert.deepEqual(await A.saveApplications('http://x', 'D 1', 'L12', ['aviation']), ['aviation']);
  assert.deepEqual(calls[0], ['http://x/api/family/config/D%201/L12/applications', 'PUT', '{"applications":["aviation"]}']);
  await assert.rejects(A.saveApplications('http://x', 'D', 'L', ['bad']), /unknown application/);
});

// -- Motors catalog for a role-user account (client-view batch 2026-10-14) --------------------
// The exact tree GET /api/family/tree returned for vadshe@gmail.com (role "user"): two dies.
const USER_TREE = { can_write: false, dies: [
  { name: 'CIANO14 40 new', stator_diameter: 40, layer: 'shared', read_only: true,
    configs: [{ name: 'L12', applications: ['propeller'], application: 'propeller', application_set: true, applications_error: null },
              { name: 'L20', applications: ['propeller'], application: 'propeller', application_set: true, applications_error: null }] },
  { name: 'CIANO28 85 20SW1200', stator_diameter: 85, layer: 'shared', read_only: true,
    configs: [{ name: 'L13', applications: ['robot'], application: 'robot', application_set: true, applications_error: null }] },
] };

t('role user: both dies render, one Motors section per diameter', () => {
  const all = A.catalogDiameters(USER_TREE.dies, 'all');
  assert.deepEqual(all, [40, 85]);
  const names = all.flatMap((d) => A.diesOfDiameter(USER_TREE.dies, 'all', d).map((x) => x.name));
  assert.deepEqual(names, ['CIANO14 40 new', 'CIANO28 85 20SW1200']);
  assert.equal(A.diesOfDiameter(USER_TREE.dies, 'all', 40)[0].configs.length, 2);
});

t('the application filter narrows the sections and never empties an "all" view', () => {
  assert.deepEqual(A.catalogDiameters(USER_TREE.dies, 'robot'), [85]);
  assert.deepEqual(A.catalogDiameters(USER_TREE.dies, 'propeller'), [40]);
  assert.deepEqual(A.catalogDiameters(USER_TREE.dies, 'marine'), []);
  assert.deepEqual(A.catalogDiameters([], 'all'), []);
  assert.deepEqual(A.diesOfDiameter(USER_TREE.dies, 'robot', 40), []);
});

t('a die with no readable diameter gets no section (not a crash)', () => {
  assert.deepEqual(A.catalogDiameters([{ name: 'x', stator_diameter: undefined, configs: [] }], 'all'), []);
});

// -- robot cooling: the robotics mode only (client-view batch) -------------------------------
t('a robot configuration is robotics-only cooling; a mixed or propeller one is not', () => {
  const robot = { restricted: true, cooling_options: ['robotics'], propellers: [], default_propeller: null, defaults: {} };
  assert.equal(P.isRoboticsCooled(robot), true);
  assert.equal(P.isRoboticsCooled({ ...robot, cooling_options: ['robotics', 'propeller_air'] }), false);
  assert.equal(P.isRoboticsCooled({ ...robot, cooling_options: ['propeller_air'] }), false);
  assert.equal(P.isRoboticsCooled({ ...robot, cooling_options: null }), false);
  assert.equal(P.isRoboticsCooled(null), false);
  // the application gate on a mixed registry entry ends in exactly this state
  const mixed = { restricted: true, cooling_options: ['robotics', 'propeller_air'], propellers: ['p1'], default_propeller: 'p1', defaults: { L13: 'p1' } };
  assert.equal(P.isRoboticsCooled(A.coolingForApplication(mixed, 'robot')), true);
  assert.equal(P.isRoboticsCooled(A.coolingForApplication(mixed, 'propeller')), false);
});
