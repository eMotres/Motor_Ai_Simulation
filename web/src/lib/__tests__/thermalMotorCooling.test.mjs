// node --test - the pure rules of lib/thermalMotorCooling.ts (the cooling
// fields belong to the MOTOR, 2026-10-15).
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  adoptableCoolingFields, coolingMismatch, coolingSourceLine, fieldsMode,
  followAdoption, hydrationIsStale, hydrationPlan, isCompleteCooling, isCoolingSource, ownershipIssue, parseMotorCooling, sameMotor,
  shouldAdoptMotorCooling, splitMotorKey, withoutMotorOwned,
} from '../thermalMotorCooling.ts';

const L155 = { die: 'CIANO10 200 opt', config: 'L155 motor' };

test('the motor key splits on the last bar and refuses a malformed one', () => {
  assert.deepEqual(splitMotorKey('CIANO10 200 opt|L155 motor'), L155);
  assert.deepEqual(splitMotorKey('a|b|c'), { die: 'a|b', config: 'c' });
  for (const bad of [null, undefined, 42, '', 'nobar', '|cfg', 'die|']) {
    assert.equal(splitMotorKey(bad), null);
  }
});

test('a follow answer with fields is adopted; one without is not', () => {
  const ans = {
    changed: true, motor_changed: true, source: 'application', application: 'aviation',
    fields: { coolMode: 'liquid', frame: 'housed', boreMode: 'none', flowLpm: '8', junk: 3 },
  };
  const mc = parseMotorCooling(ans);
  assert.equal(mc.source, 'application');
  assert.equal(mc.application, 'aviation');
  assert.deepEqual(mc.fields, { coolMode: 'liquid', frame: 'housed', boreMode: 'none', flowLpm: '8' });
  assert.equal(parseMotorCooling({ changed: false, fields: null }), null);
  assert.equal(parseMotorCooling({ fields: {} }), null);
  assert.equal(parseMotorCooling(null), null);
  // an unknown source is shown as the honest "last used", never as "saved"
  assert.equal(parseMotorCooling({ fields: { frame: 'open' }, source: 'bogus' }).source, 'last_used');
});

test('only the cooling fields the panel holds are adopted', () => {
  const f = adoptableCoolingFields({
    frame: 'open', boreMode: 'air', boreAirSpeed: '20', airSpeedSource: 'propeller',
    propellerId: 'x', propellerContextKey: 'k', maxIter: '9', coolMode: 'air',
  });
  assert.deepEqual(f, { coolMode: 'air', boreMode: 'air', boreAirSpeed: '20', frame: 'open' });
});

test('the source line names the motor and the source', () => {
  assert.deepEqual(coolingSourceLine(L155, 'saved', null),
    { key: 'coolingSourceSaved', args: { motor: 'CIANO10 200 opt L155 motor', app: '-' } });
  assert.equal(coolingSourceLine(L155, 'config', null).key, 'coolingSourceConfig');
  const app = coolingSourceLine(L155, 'application', 'aviation');
  assert.equal(app.key, 'coolingSourceApplication');
  assert.equal(app.args.app, 'aviation');
  assert.equal(coolingSourceLine(null, 'saved', null), null);
  assert.equal(coolingSourceLine(L155, null, null), null);
  assert.ok(['saved', 'config', 'application', 'last_used'].every(isCoolingSource));
  assert.equal(isCoolingSource('request'), false);
});

test('a propeller source is air and anything but open is housed', () => {
  assert.deepEqual(fieldsMode({ coolMode: 'liquid', airSpeedSource: 'propeller', frame: 'open' }),
    { mode: 'air', frame: 'open' });
  assert.deepEqual(fieldsMode({ coolMode: 'liquid', airSpeedSource: 'manual', frame: 'x' }),
    { mode: 'liquid', frame: 'housed' });
});

test('the loud warning: result solved air/open, fields now liquid/housed', () => {
  const result = { cooling: { outer: { mode: 'air' }, frame: 'open' } };
  const now = { coolMode: 'liquid', airSpeedSource: 'manual', frame: 'housed' };
  assert.deepEqual(coolingMismatch(result, now), { solved: 'air/open', now: 'liquid/housed' });
  // only the frame differs
  assert.deepEqual(
    coolingMismatch({ cooling: { outer: { mode: 'liquid' }, frame: 'open' } }, now),
    { solved: 'liquid/open', now: 'liquid/housed' });
});

test('no warning when the result matches, or says nothing', () => {
  const now = { coolMode: 'liquid', airSpeedSource: 'manual', frame: 'housed' };
  assert.equal(coolingMismatch({ cooling: { outer: { mode: 'liquid' }, frame: 'housed' } }, now), null);
  // a result from before the frame was reported is a housed machine
  assert.equal(coolingMismatch({ cooling: { outer: { mode: 'liquid' } } }, now), null);
  assert.equal(coolingMismatch({ cooling: {} }, now), null);
  assert.equal(coolingMismatch(null, now), null);
  // a propeller source solved as air is the same cooling
  assert.equal(coolingMismatch({ cooling: { outer: { mode: 'air' }, frame: 'open' } },
    { coolMode: 'liquid', airSpeedSource: 'propeller', frame: 'open' }), null);
});

// ── review of PR #218, finding 4: stale hydration and field ownership ─────────

const A = { die: 'CIANO14 40 new', config: 'L12' };

test('a solve under the wrong motor cooling fields is refused by name', () => {
  assert.equal(ownershipIssue(L155, L155), null);
  assert.equal(ownershipIssue(A, null), null);              // no motor loaded
  // UNKNOWN ownership with a motor loaded is not acceptable (hydration in flight,
  // or its fetch failed): the solve waits / the backend resolves the record
  assert.match(ownershipIssue(null, L155), /not loaded yet/);
  assert.match(ownershipIssue(undefined, L155), /CIANO10 200 opt L155 motor/);
  const issue = ownershipIssue(A, L155);
  assert.match(issue, /CIANO14 40 new L12/);
  assert.match(issue, /CIANO10 200 opt L155 motor/);
  assert.equal(sameMotor(A, { ...A }), true);
  assert.equal(sameMotor(A, L155), false);
});

test('a hydration that a motor change overtook is stale', () => {
  // A's GET starts at epoch 0; B's follow bumps the epoch; A's answer lands last
  assert.equal(hydrationIsStale(0, 1, A, L155), true);
  // the loaded motor changed with no follow yet
  assert.equal(hydrationIsStale(0, 0, A, L155), true);
  assert.equal(hydrationIsStale(0, 0, null, L155), true);
  assert.equal(hydrationIsStale(0, 0, A, null), true);
  // nothing moved
  assert.equal(hydrationIsStale(3, 3, A, { ...A }), false);
  assert.equal(hydrationIsStale(0, 0, null, null), false);
});

test('a stale hydration keeps its account-level fields and none of the cooling', () => {
  const adopted = {
    coolMode: 'air', frame: 'open', boreMode: 'air', airSpeedSource: 'propeller',
    propellerId: 'x', propellerContextKey: 'k', maxIter: '9', view: 'flux',
  };
  assert.deepEqual(withoutMotorOwned(adopted), { maxIter: '9', view: 'flux' });
});

// ── second review, R5: a lost follow answer is recoverable by reloading ────────

const FIELDS = { coolMode: 'liquid', frame: 'housed', boreMode: 'none' };

test('an unchanged answer still resynchronises a browser that holds the wrong motor fields', () => {
  const unchanged = { changed: false, motor_changed: false, fields: FIELDS, source: 'saved' };
  // the answer for B was lost: the browser still owns A -> adopt on the reload
  assert.equal(shouldAdoptMotorCooling(unchanged, A, L155), true);
  assert.equal(shouldAdoptMotorCooling(unchanged, null, L155), true);
  // the browser already owns B: keep whatever the user is typing
  assert.equal(shouldAdoptMotorCooling(unchanged, L155, L155), false);
  // a motor change or a server repair always adopts
  assert.equal(shouldAdoptMotorCooling({ ...unchanged, motor_changed: true }, L155, L155), true);
  assert.equal(shouldAdoptMotorCooling({ ...unchanged, changed: true }, L155, L155), true);
  // nothing to adopt without fields
  assert.equal(shouldAdoptMotorCooling({ changed: true, fields: null }, A, L155), false);
});

// ── third review, T1: ownership only from COMPLETE server fields ────────────────

const COMPLETE = {
  coolMode: 'air', ambientT: '30', airSpeed: '0', fluid: 'water', tIn: '30', hConv: '50',
  flowLpm: '8', boreMode: 'none', boreAirSpeed: '0', boreFluid: 'water', boreTIn: '30',
  boreFlowLpm: '4', shaftExtMm: '0', shaftExtSides: '2', frame: 'housed', openAirSpeed: '0',
  emissivity: '0.9', heatPath: 'housing', endFaces: 'still', endFaceSides: '2',
  airSpeedSource: 'manual', propellerId: '', propellerContextKey: '',
};
const MKEY = 'CIANO10 200 opt|L155 motor';

test('completeness: every cooling key and the source, as strings', () => {
  assert.equal(isCompleteCooling(COMPLETE), true);
  assert.equal(isCompleteCooling({ ...COMPLETE, frame: undefined }), false);
  assert.equal(isCompleteCooling({ coolMode: 'air', airSpeedSource: 'manual', ambientT: '30' }), false);
  assert.equal(isCompleteCooling({}), false);
  assert.equal(isCompleteCooling(null), false);
});

test('hydration: a sparse, empty, unbound or failed read establishes no ownership', () => {
  const sparse = { settings: { coolMode: 'air', airSpeedSource: 'manual', ambientT: '30' }, motor: MKEY };
  const planSparse = hydrationPlan(sparse, L155);
  assert.deepEqual(planSparse, { adoptCooling: false, owner: null, needsResync: true });
  // successful but EMPTY while a follow is pending
  assert.deepEqual(hydrationPlan({ settings: {}, motor: null }, L155),
    { adoptCooling: false, owner: null, needsResync: true });
  // complete but naming no motor (an unbound / legacy record)
  assert.equal(hydrationPlan({ settings: COMPLETE, motor: null }, L155).owner, null);
  // failed read
  assert.deepEqual(hydrationPlan(null, L155), { adoptCooling: false, owner: null, needsResync: true });
  // nothing loaded: nothing to resync
  assert.equal(hydrationPlan(null, null).needsResync, false);
});

test('hydration: a complete record bound to a motor is adopted; another motor asks for a resync', () => {
  const own = hydrationPlan({ settings: COMPLETE, motor: MKEY }, L155);
  assert.deepEqual(own, { adoptCooling: true, owner: L155, needsResync: false });
  const other = hydrationPlan({ settings: COMPLETE, motor: 'CIANO14 40 new|L12' }, L155);
  assert.equal(other.adoptCooling, true);
  assert.equal(other.needsResync, true);
});

test('follow: only a complete answer for the loaded motor is adopted, with ownership from it', () => {
  const answer = { motor: MKEY, motor_changed: false, changed: false, fields: COMPLETE, source: 'last_used' };
  const got = followAdoption(answer, A, L155);          // the browser held A's fields
  assert.deepEqual(got.motor, L155);
  assert.equal(got.fields.airSpeed, '0');               // the server's, not the browser's
  // an incomplete answer never establishes ownership
  const partial = { ...answer, fields: { coolMode: 'air', airSpeedSource: 'manual' } };
  assert.equal(followAdoption(partial, A, L155), null);
  // a late answer for the motor that is no longer loaded is dropped
  assert.equal(followAdoption({ ...answer, motor: 'CIANO14 40 new|L12' }, A, L155), null);
  assert.equal(followAdoption({ ...answer, motor: undefined }, A, L155), null);
  // already owned and unchanged: keep the user's typing
  assert.equal(followAdoption(answer, L155, L155), null);
});
