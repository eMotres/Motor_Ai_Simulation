// node --test - WHOSE result is on the Thermal / Mechanical tab (owner 2026-11-04).
// «Why does thermal draw the picture of a 400 mm motor when I loaded the 200 mm one?  Check the mechanics too.»
// The rule (lib/resultOwner): a result is drawn only when it is the loaded point's own - two witnesses, the geometry
// fingerprint and the point (die / configuration / duty).  Another motor's result is not drawn, not even dimmed.
import test from 'node:test';
import assert from 'node:assert/strict';

let R = null;
try { R = await import('../resultOwner.ts'); } catch { /* old Node */ }
const t = R ? test : test.skip;

const A = R && R.pointKeyOf({ die: 'CILN28', config: 'G2-L40', duty: 'optimal' });
const B = R && R.pointKeyOf({ die: 'CILN28', config: 'G2-L40', duty: 'rated' });
const OTHER_DIE = R && R.pointKeyOf({ die: 'M400', config: 'S3-L200', duty: 'optimal' });
const SIG = 'num_poles:28|stator_diameter:200|stator_outer_radius:100';
const SIG_400 = 'num_poles:28|stator_diameter:400|stator_outer_radius:200';

t('a point key needs all three parts and round-trips', () => {
  assert.equal(R.pointKeyOf(null), null);
  assert.equal(R.pointKeyOf({ die: 'a', config: 'b' }), null);
  assert.deepEqual(R.pointOfKey(A), { die: 'CILN28', config: 'G2-L40', duty: 'optimal' });
  assert.equal(R.pointOfKey(null), null);
  assert.equal(R.pointKeyOfContext({ die: 'CILN28', config: 'G2-L40', duty: 'optimal' }), A);
  assert.equal(R.pointKeyOfContext(null), null);
  assert.equal(R.pointKeyOfContext('x'), null);
});

// ── the /last entry: adopted only when BOTH witnesses agree ────────────────────────────────────────────────────────────
t('the bug: a 400 mm motor\'s last result is not adopted for the loaded 200 mm point', () => {
  const entry = { stale_geometry: true, context: { die: 'M400', config: 'S3-L200', duty: 'optimal' } };
  assert.equal(R.mayAdopt(entry, A), false);
  assert.equal(R.mayAdopt(entry, null), false, 'not even with no point loaded: the backend proved another machine');
});

t('an unknown fingerprint verdict is not a licence to draw (the old code drew it dimmed)', () => {
  assert.equal(R.mayAdopt({ stale_geometry: null, context: { die: 'CILN28', config: 'G2-L40', duty: 'optimal' } }, A), false);
  assert.equal(R.mayAdopt({ stale_geometry: null }, null), false);
});

t('same geometry, same point: adopted', () => {
  assert.equal(R.mayAdopt({ stale_geometry: false, context: { die: 'CILN28', config: 'G2-L40', duty: 'optimal' } }, A), true);
});

t('same geometry but ANOTHER point of the same motor is not this point\'s result', () => {
  assert.equal(R.mayAdopt({ stale_geometry: false, context: { die: 'CILN28', config: 'G2-L40', duty: 'rated' } }, A), false);
  assert.equal(R.mayAdopt({ stale_geometry: false, context: { die: 'M400', config: 'S3-L200', duty: 'optimal' } }, A), false);
});

t('an entry with no point stamp proves nothing about the point: refused for a loaded point, accepted by geometry alone otherwise', () => {
  assert.equal(R.mayAdopt({ stale_geometry: false, context: null }, A), false);
  assert.equal(R.mayAdopt({ stale_geometry: false }, A), false);
  assert.equal(R.mayAdopt({ stale_geometry: false, context: null }, null), true);
  assert.equal(R.mayAdopt(null, A), false);
  assert.equal(R.mayAdopt(undefined, null), false);
});

// ── a result already on screen, against the point loaded NOW ───────────────────────────────────────────────────────────
t('another point is foreign - never drawn - whatever the geometry says', () => {
  assert.equal(R.standingOf({ point: A, geoSig: SIG }, { point: B, geoSig: SIG }), 'foreign');
  assert.equal(R.standingOf({ point: A, geoSig: SIG }, { point: OTHER_DIE, geoSig: SIG_400 }), 'foreign');
  assert.equal(R.standingOf({ point: A, geoSig: null }, { point: B, geoSig: SIG }), 'foreign');
});

t('the same point is the loaded motor\'s own; a user\'s geometry edit after the solve is flagged, not erased', () => {
  assert.equal(R.standingOf({ point: A, geoSig: SIG }, { point: A, geoSig: SIG }), 'mine');
  assert.equal(R.standingOf({ point: A, geoSig: SIG }, { point: A, geoSig: SIG_400 }), 'edited');
  // an unknown stamp never claims a mismatch
  assert.equal(R.standingOf({ point: A, geoSig: null }, { point: A, geoSig: SIG }), 'mine');
  assert.equal(R.standingOf({ point: A, geoSig: SIG }, { point: A, geoSig: '' }), 'mine');
});

t('float spelling is not another machine (the 52.95 / 52.94999999999999 incident)', () => {
  const a = 'shaft_r:52.95|num_poles:28';
  const b = 'shaft_r:52.94999999999999|num_poles:28';
  assert.equal(R.standingOf({ point: A, geoSig: a }, { point: A, geoSig: b }), 'mine');
});

t('a result solved with no point loaded: kept only while the loaded machine is provably the same', () => {
  assert.equal(R.standingOf({ point: null, geoSig: SIG }, { point: A, geoSig: SIG }), 'mine');
  assert.equal(R.standingOf({ point: null, geoSig: SIG }, { point: A, geoSig: SIG_400 }), 'foreign');
  assert.equal(R.standingOf({ point: null, geoSig: null }, { point: A, geoSig: SIG }), 'foreign');
  assert.equal(R.standingOf({ point: A, geoSig: SIG }, { point: null, geoSig: SIG }), 'mine');
  assert.equal(R.standingOf({ point: A, geoSig: SIG }, { point: null, geoSig: SIG_400 }), 'foreign');
});

t('no point on either side: the geometry decides, as it always did', () => {
  assert.equal(R.standingOf({ point: null, geoSig: SIG }, { point: null, geoSig: SIG }), 'mine');
  assert.equal(R.standingOf({ point: null, geoSig: SIG }, { point: null, geoSig: SIG_400 }), 'edited');
});

// ── the point's stored file ────────────────────────────────────────────────────────────────────────────────────────────
t('a stored map is drawn only when its fingerprint is the live one; missing is unproven, not a pass', () => {
  assert.equal(R.storedVerdict('c26331a1', 'c26331a1'), 'draw');
  assert.equal(R.storedVerdict('32fb1d38', 'c26331a1'), 'older_build');
  assert.equal(R.storedVerdict(undefined, 'c26331a1'), 'unproven');
  assert.equal(R.storedVerdict('c26331a1', null), 'unproven');
  assert.equal(R.storedVerdict('c26331a1', 'nofp'), 'unproven');
});

// ── geoSig.geoStaleAgainstLive / isStale no longer compare stamps as strings ──────────────────────────────────────────
t('geoSigsDiffer is the comparison (never !==)', async () => {
  const G = await import('../../components/common/geoSig.ts');
  assert.equal(G.geoSigsDiffer('a:52.95|b:1', 'a:52.94999999999999|b:1'), false);
  assert.equal(G.geoSigsDiffer(SIG, SIG_400), true);
});
