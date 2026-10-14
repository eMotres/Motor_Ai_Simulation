// node --test — what a standard user sees (owner 2026-10-05): Motors + Configure only,
// the default motor's reference, refusal reasons, the card summary.
// Imports lib/accessUi.ts itself (Node >= 22.18 / 24; else skipped).
import test from 'node:test';
import assert from 'node:assert/strict';

let A = null;
try { A = await import('../accessUi.ts'); } catch { /* old Node */ }
const t = A ? test : test.skip;

t('a standard user gets exactly Motors, Configure and 3D model', () => {
  const all = ['motors', 'geometry', 'materials', 'mesh', 'simulation', 'static3d', 'mechanical',
    'thermal', 'controller', 'sweep', 'comparePoints', 'cost', 'compare', 'model3d', 'admin'];
  assert.deepEqual(all.filter((id) => A.tabAllowed(id, true)), ['motors', 'compare', 'model3d']);
  assert.deepEqual(all.filter((id) => A.tabAllowed(id, false)), all);
});

t('who is a standard user', () => {
  assert.equal(A.isStandardUser(true, false, 'user'), true);
  assert.equal(A.isStandardUser(true, true, 'admin'), false);
  assert.equal(A.isStandardUser(false, true, 'admin'), false);   // local dev: everything
  assert.equal(A.isStandardUser(true, false, 'anon'), false);    // the landing, not the app
});

t('landing: Configure when something is selected there, else Motors', () => {
  assert.equal(A.landingTabForUser(true), 'compare');
  assert.equal(A.landingTabForUser(false), 'motors');
});

t('the UI offers writes only to a session that may write the shared config', () => {
  assert.equal(A.uiCanWrite(true, true), true);
  assert.equal(A.uiCanWrite(true, false), false);    // a regular account: server says true (own workspace)
  assert.equal(A.uiCanWrite(false, true), false);
  assert.equal(A.uiCanWrite(undefined, true), false);
});

t('the shared-context follower waits for role resolution and skips standard users', () => {
  assert.equal(A.mayFollowSharedContext(false, false, false), false); // auth unresolved
  assert.equal(A.mayFollowSharedContext(true, true, false), false);   // standard account
  assert.equal(A.mayFollowSharedContext(true, true, true), true);     // admin
  assert.equal(A.mayFollowSharedContext(true, false, false), true);  // local dev
});

t('only a gate refusal has a reason key', () => {
  const gate = { detail: 'This feature requires an admin account.', required_role: 'admin', your_role: 'user' };
  assert.equal(A.refusalReasonKey(403, gate), 'refused.admin');
  assert.equal(A.refusalReasonKey(401, { ...gate, your_role: 'anon' }), 'refused.signIn');
  assert.equal(A.refusalReasonKey(403, { detail: 'locked' }), null);
  assert.equal(A.refusalReasonKey(404, gate), null);
  assert.equal(A.refusalReasonKey(403, null), null);
});

const refs = [
  { id: 'cat:1', name: 'CIANO14 40 new L12', card: { die: 'CIANO14 40 new', config: 'L12' } },
  { id: 'cat:2', name: 'CIANO14 40 new L20', card: null },
  { id: 'cat:3', name: 'CILN28', card: null },
];

t('the default motor finds its reference: by card, by "<die> <config>", by die alone', () => {
  assert.equal(A.referenceOfDefault(refs, { die: 'CIANO14 40 new', config: 'L12' }).id, 'cat:1');
  assert.equal(A.referenceOfDefault(refs, { die: 'CIANO14 40 new', config: 'L20' }).id, 'cat:2');
  assert.equal(A.referenceOfDefault(refs, { die: 'CILN28', config: 'L40' }).id, 'cat:3');
  assert.equal(A.referenceOfDefault(refs, { die: 'NOT GRANTED', config: 'L1' }), null);
  assert.equal(A.referenceOfDefault(refs, null), null);
});

t('card summary: none / partial / all', () => {
  assert.deepEqual(A.cardSummary(0, 2), { text: '0/2', state: 'none' });
  assert.deepEqual(A.cardSummary(1, 2), { text: '1/2', state: 'partial' });
  assert.deepEqual(A.cardSummary(2, 2), { text: '2/2', state: 'all' });
});

// -- role "engineer" (owner 2026-10-14): Configure only, or the whole simulation ------------------------------------------

const ALL_TABS = ['motors', 'geometry', 'materials', 'mesh', 'simulation', 'static3d', 'mechanical',
  'thermal', 'controller', 'sweep', 'comparePoints', 'cost', 'compare', 'model3d', 'admin'];

// what App.tsx computes for a session: signedIn, fullUI (hasSimulationUI) and isAdmin
const visible = (enforced, isAdmin, role) => {
  const ctx = { signedIn: true, fullUI: A.hasSimulationUI(enforced, isAdmin, role), isAdmin };
  return ALL_TABS.filter((id) => A.tabVisible(id, ctx));
};

t('role user: Motors, Configure, 3D model - nothing else', () => {
  assert.deepEqual(visible(true, false, 'user'), ['motors', 'compare', 'model3d']);
});

t('role engineer: the whole simulation UI, no admin sections', () => {
  assert.deepEqual(visible(true, false, 'engineer'), ['motors', 'geometry', 'materials', 'mesh', 'simulation', 'mechanical',
    'thermal', 'controller', 'comparePoints', 'compare', 'model3d']);
  for (const id of ['admin', 'sweep', 'cost', 'static3d']) assert.equal(visible(true, false, 'engineer').includes(id), false, id);
});

t('role admin and the local backend see every tab', () => {
  assert.deepEqual(visible(true, true, 'admin'), ALL_TABS);
  assert.deepEqual(visible(false, true, 'admin'), ALL_TABS);
});

t('an engineer is not a standard user and gets no user-only landing', () => {
  assert.equal(A.isStandardUser(true, false, 'engineer'), false);
  assert.equal(A.isStandardUser(true, false, 'user'), true);
  assert.equal(A.hasSimulationUI(true, false, 'user'), false);
  assert.equal(A.hasSimulationUI(true, false, 'engineer'), true);
  assert.equal(A.hasSimulationUI(true, false, 'anon'), false);
});

t('every tab App.tsx draws has a gate, unknown tabs are admin only', () => {
  assert.deepEqual(Object.keys(A.TAB_GATES).sort(), [...ALL_TABS].sort());
  assert.equal(A.tabVisible('somethingNew', { signedIn: true, fullUI: true, isAdmin: false }), false);
});

t('the role select: Configure / Simulation / Admin for user / engineer / admin', () => {
  assert.deepEqual([...A.ROLE_VALUES], ['user', 'engineer', 'admin']);
  assert.deepEqual(A.ROLE_VALUES.map(A.roleLabel), ['Configure', 'Simulation', 'Admin']);
  assert.equal(A.roleLabel('staff'), 'staff');
});

t('the empty-catalogue hint is for non-admin accounts on an enforcing backend', () => {
  assert.equal(A.emptyCatalogHintApplies(true, false), true);
  assert.equal(A.emptyCatalogHintApplies(true, true), false);
  assert.equal(A.emptyCatalogHintApplies(false, true), false);
});

t('My compute nodes is reachable for every signed-in role, engineer included', () => {
  assert.equal(A.computeNodesReachable(true), true);
  assert.equal(A.computeNodesReachable(false), false);
  assert.ok(A.ROLE_VALUES.every((r) => A.computeNodesReachable(r !== 'anon')));
});

t('the compute hint: user and engineer on a simulation tab of an enforcing backend only', () => {
  for (const tab of A.COMPUTE_HINT_TABS) {
    assert.equal(A.computeHintApplies(tab, true, false, 'engineer'), true, tab);
    assert.equal(A.computeHintApplies(tab, true, false, 'user'), true, tab);
    assert.equal(A.computeHintApplies(tab, true, true, 'admin'), false, tab);
    assert.equal(A.computeHintApplies(tab, false, true, 'admin'), false, tab);
    assert.equal(A.computeHintApplies(tab, true, false, 'anon'), false, tab);
  }
  for (const tab of ['motors', 'compare', 'model3d', 'admin', 'sweep', 'geometry']) {
    assert.equal(A.computeHintApplies(tab, true, false, 'engineer'), false, tab);
  }
  // every hint tab is one an engineer actually sees
  const ctx = { signedIn: true, fullUI: true, isAdmin: false };
  assert.ok(A.COMPUTE_HINT_TABS.every((id) => A.tabVisible(id, ctx)));
  assert.match(A.COMPUTE_WORKER_DOCS_URL, /BYO_COMPUTE\.md#9-install$/);
});

// -- the empty Motors catalog line (client-view batch 2026-10-14) ---------------------------
t('an empty catalog never shows developer text to a role-user account', () => {
  assert.equal(A.emptyCatalogLine({ loaded: false, failed: false, note: null, canWrite: false }), 'loading');
  assert.equal(A.emptyCatalogLine({ loaded: true, failed: false, note: null, canWrite: false }), 'noMotors');
  assert.equal(A.emptyCatalogLine({ loaded: true, failed: false, note: 'no motors granted yet', canWrite: false }), 'note');
  assert.equal(A.emptyCatalogLine({ loaded: true, failed: true, note: null, canWrite: false }), 'unreachable');
  for (const loaded of [true, false]) for (const failed of [true, false]) for (const note of [null, 'n'])
    assert.notEqual(A.emptyCatalogLine({ loaded, failed, note, canWrite: false }), 'vendorHint');
});

t('the "freeze the geometry" hint is the vendor\'s, and only once the tree has answered empty', () => {
  assert.equal(A.emptyCatalogLine({ loaded: true, failed: false, note: null, canWrite: true }), 'vendorHint');
  assert.equal(A.emptyCatalogLine({ loaded: false, failed: false, note: null, canWrite: true }), 'loading');
});
